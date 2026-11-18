"""Opt-in Stage 1 multi-step check: real local Qwen, stubbed application effects.

This is a functional check, not a model benchmark. Nothing is saved, installed,
or launched. The existing model and production authorization remain unchanged.
"""

import argparse
import json
import sys
from unittest.mock import patch

from aqua.actions import tools
from aqua.actions.launcher import ALLOWED_APPS
from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.filtering import AnswerChannelGuard
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings, OllamaClient, OllamaError

REQUEST = "Open Notepad and Calculator."


class _DiagnosticClient:
    """Show completed answer/tool channels, never private thinking or partial calls."""

    def __init__(self, client):
        self.client, self.decisions = client, 0

    def __getattr__(self, name):
        return getattr(self.client, name)

    def stream_chat(self, messages, **options):
        self.decisions += 1
        guard, answer = AnswerChannelGuard(), []
        iterator = self.client.stream_chat(messages, **options)
        try:
            for event in iterator:
                answer.append(guard.feed(event.content))
                if event.done:
                    answer.append(guard.finish())
                    review = "Single bounded completeness review" in messages[0]["content"]
                    print(f"Decision {self.decisions}: reconsideration={review}; "
                          f"answer={json.dumps(''.join(answer), ensure_ascii=False)}; "
                          f"native_calls={json.dumps(event.tool_calls, ensure_ascii=False)}")
                yield event
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                close()


def run_demo(client, *, fail_first=False, request=REQUEST, diagnostics=False) -> int:
    attempts = []
    expected = {app for app in ALLOWED_APPS
                if tools.authorize_task_request(request, app).status == "approved"}

    def execute(app):
        if app not in ALLOWED_APPS:
            raise AssertionError("Only canonical approved apps may reach this stub.")
        attempts.append(app)
        if fail_first and len(attempts) == 1:
            return tools.ToolResult(app, "launch_failed", False, error_category="demo_stub_failure")
        return tools.ToolResult(app, "launch_attempt_succeeded", True)

    conversation = Conversation()
    controller = ConversationController(
        _DiagnosticClient(client) if diagnostics else client, conversation, model=DEFAULT_CHAT_MODEL,
        settings=GenerationSettings(temperature=0.2),
    )
    with patch.object(tools, "execute_application", side_effect=execute), \
            patch("aqua.actions.launcher.subprocess.Popen",
                  side_effect=AssertionError("Agent demo must never launch applications.")):
        result = controller.respond(request)
    task = result.task
    expected_status = ("partial_failure" if len(expected) > 1 else "failed") if fail_first else "completed"
    passed = (bool(expected) and result.ok and task.status == expected_status
              and len(attempts) == len(expected) and set(attempts) == expected
              and len(task.operations) == len(expected) and task.decisions <= 6
              and len(task.operations) <= 5
              and len(task.failed_operations) == int(fail_first)
              and conversation.turn_count == 1)
    print(f"Fixture: {request}")
    print(f"Stub execution order: {attempts}")
    print(result.text)
    print(f"Task: {task.status}; decisions={task.decisions}; operations={len(task.operations)}; "
          f"requests={result.requests}; total={result.total_seconds:.3f}s")
    print("History roles: " + ", ".join(row["role"] for row in conversation.history))
    if diagnostics:
        print(f"Final stage={result.stage}; stop_reason={task.stop_reason or 'none'}")
    print(f"{'PASS' if passed else 'FAIL'}: effects were stubbed; no Windows applications were launched.")
    return 0 if passed else 1


def _repeat_count(value):
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--repeat must be an integer from 1 to 20.") from None
    if not 1 <= count <= 20:
        raise argparse.ArgumentTypeError("--repeat must be an integer from 1 to 20.")
    return count


def main(argv=None) -> int:
    from aqua.cli import _configure_output
    _configure_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow real requests to the installed loopback Qwen model.")
    parser.add_argument("--fail-first", action="store_true", help="Return one simulated launch failure before the next choice.")
    parser.add_argument("--request", default=REQUEST, help="Explicit approved-app fixture to evaluate.")
    parser.add_argument("--repeat", type=_repeat_count, default=1, help="Fresh conversation sessions, 1 to 20; reuse one checked client.")
    parser.add_argument("--diagnostics", action="store_true", help="Show completed answer/native tool channels and stop reason.")
    args = parser.parse_args(argv)
    if not args.request.strip() or len(args.request) > tools.MAX_REQUEST_CHARACTERS:
        parser.error("--request must be nonblank and within the application request limit.")
    if not args.live:
        parser.print_help()
        print("No requests made. Add --live for this execution-disabled check.", file=sys.stderr)
        return 2
    try:
        client = OllamaClient()
        info = client.check_model(DEFAULT_CHAT_MODEL)
        print(f"Local Ollama {info.version}; model={info.name}; temperature=0.2; seed=unset.")
        completed = 0
        for number in range(1, args.repeat + 1):
            print(f"Clean session {number}/{args.repeat}")
            completed += run_demo(client, fail_first=args.fail_first, request=args.request,
                                  diagnostics=args.diagnostics) == 0
        print(f"Completed {completed}/{args.repeat} clean-session tasks ({completed / args.repeat:.1%}).")
        return 0 if completed == args.repeat else 1
    except OllamaError as exc:
        print(f"Local model check failed ({type(exc).__name__}); no application was launched.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAgent demo cancelled; no Windows applications were launched.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
