r"""Explicit local Qwen tool/conversation smoke test with execution disabled.

Run from the project root using the existing virtual environment:
    .\.venv\Scripts\python.exe -B tools\smoke_tools.py --live --diagnostics

Only fixed test phrases are sent to loopback Ollama. Application execution is
replaced by a recording stub, and process creation is independently blocked.
No microphone, model download, or persistent conversation is used.
"""

import argparse
from dataclasses import dataclass
import re
import sys
from time import perf_counter
from unittest.mock import patch

from aqua.actions import tools
from aqua.actions.launcher import ALLOWED_APPS, APP_ALIASES
from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings, OllamaClient, OllamaError


@dataclass(frozen=True)
class Case:
    scenario: str
    text: str
    outcome: str
    requests: int
    app: str | None = None
    pending: str | None = None
    mentions: str | None = None
    capabilities: bool = False
    failed_reply: bool = False


# Stateful scenarios intentionally share one Conversation. Other scenarios are
# isolated so later wording cannot acquire accidental prior authorization.
CASES = (
    Case("notepad", "Open Notepad.", "launch_attempt_succeeded", 2, app="notepad"),
    Case("notepad", "What did you just open?", "conversation", 1, mentions="notepad"),
    Case("filler", "Well. Open VS Code.", "launch_attempt_succeeded", 2, app="vs code"),
    Case("explicit-activity", "I feel like programming. Can you open VS Code?", "launch_attempt_succeeded", 2, app="vs code"),
    Case("confirmation", "I feel like programming.", "clarification", 1, pending="vs code"),
    Case("confirmation", "Yes, please.", "launch_attempt_succeeded", 1, app="vs code"),
    Case("confirmation", "Yes, go ahead.", "stale_confirmation", 0),
    Case("cancellation", "I feel like programming.", "clarification", 1, pending="vs code"),
    Case("cancellation", "No thanks.", "cancelled_confirmation", 0),
    Case("cancellation", "Yes, go ahead.", "stale_confirmation", 0),
    Case("negation", "Don't open Chrome.", "conversation", 1),
    Case("hypothetical", "If I said open Chrome, what would you do?", "conversation", 1),
    Case("compound", "Open Chrome and delete my files.", "conversation", 1),
    Case("capabilities", "What applications can you open?", "conversation", 1, capabilities=True),
    Case("failure", "Open Chrome.", "launch_failed", 1, app="chrome", failed_reply=True),
    Case("failure", "Did you open Chrome?", "conversation", 1, mentions="chrome", failed_reply=True),
)


def _mentions_app(text: str, app: str) -> bool:
    names = {app, *(alias for alias, target in APP_ALIASES.items() if target == app)}
    normalized = " ".join(text.casefold().split())
    return any(re.search(r"\b" + re.escape(name) + r"\b", normalized) for name in names)


def _check(case: Case, result, actual_calls: list[str], pending: str | None) -> list[str]:
    failures = []
    if not result.ok:
        failures.append("turn did not complete successfully")
    if result.outcome != case.outcome:
        failures.append(f"expected outcome={case.outcome}")
    if result.requests != case.requests:
        failures.append(f"expected requests={case.requests}")
    expected_calls = [case.app] if case.app is not None else []
    if actual_calls != expected_calls:
        failures.append(f"expected stub calls={expected_calls}")
    if pending != case.pending:
        failures.append(f"expected pending={case.pending}")
    if case.mentions and not _mentions_app(result.text, case.mentions):
        failures.append(f"reply did not recall {case.mentions}")
    if case.capabilities and not all(_mentions_app(result.text, app) for app in ALLOWED_APPS):
        failures.append("reply omitted an enabled application")
    if case.failed_reply and not re.search(r"\b(?:failed|failure|couldn't|could not|didn't|did not|unable|not successfully)\b", result.text.casefold()):
        failures.append("follow-up did not acknowledge launch failure")
    if case.failed_reply and re.search(r"\blaunch attempt succeeded\b", result.text.casefold()):
        failures.append("reply contradicted the verified launch failure")
    # An affirmative readiness claim is stronger than the stub's evidence.
    if re.search(r"\b(?:notepad|chrome|calculator|vs code)\s+is\s+(?:now\s+)?(?:ready|visible|responding)\b", result.text.casefold()):
        failures.append("reply claimed unverified application readiness")
    return failures


def run_cases(client, *, diagnostics: bool = False) -> int:
    """Use the production controller with exactly one recording executor stub."""
    recorded = []
    sessions = {}
    current = None

    def execute(app: str) -> tools.ToolResult:
        if app not in ALLOWED_APPS:
            raise AssertionError("An unapproved app reached the executor stub.")
        recorded.append(app)
        if current.scenario == "failure":
            return tools.ToolResult(app, "launch_failed", False, error_category="smoke_stub_failure")
        return tools.ToolResult(app, "launch_attempt_succeeded", True, readiness_checked=False)

    failures = 0
    with patch.object(tools, "execute_application", side_effect=execute), \
            patch("aqua.actions.launcher.subprocess.Popen", side_effect=AssertionError("Smoke checks must never launch applications.")):
        for number, current in enumerate(CASES, 1):
            if current.scenario not in sessions:
                conversation = Conversation()
                controller = ConversationController(client, conversation, model=DEFAULT_CHAT_MODEL, settings=GenerationSettings())
                sessions[current.scenario] = controller
            controller = sessions[current.scenario]
            before = len(recorded)
            result = controller.respond(current.text)
            actual = recorded[before:]
            reasons = _check(current, result, actual, controller.conversation.action_state.pending_app)
            passed = not reasons
            failures += not passed
            first = f"{result.first_token_seconds:.3f}s" if result.first_token_seconds is not None else "n/a"
            print(f"{number}. {'PASS' if passed else 'FAIL'} outcome={result.outcome}; requests={result.requests}; "
                  f"first visible model token={first}; total={result.total_seconds:.3f}s; stub calls={actual}")
            print(f"   Fixture: {current.text}")
            print(f"   Aqua: {result.text}")
            if diagnostics:
                print(f"   stage={result.stage}; model={DEFAULT_CHAT_MODEL}; endpoint=http://127.0.0.1:11434/api/chat; error={'present' if result.error else 'none'}")
            if reasons:
                print("   Mismatch: " + "; ".join(reasons))
    print(f"{len(CASES) - failures}/{len(CASES)} turns matched. Executor results were stubbed; no Windows applications were launched.")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    # Use the same Windows/redirected Unicode handling as Aqua's terminal CLI.
    from aqua.cli import _configure_output
    _configure_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly contact the installed local Ollama model.")
    parser.add_argument("--diagnostics", action="store_true", help="Show failure stage and fixed endpoint/model metadata.")
    args = parser.parse_args(argv)
    if not args.live:
        parser.print_help()
        print("No requests made. Add --live to allow this execution-disabled loopback check.", file=sys.stderr)
        return 2
    try:
        client = OllamaClient()
        started = perf_counter()
        info = client.check_model(DEFAULT_CHAT_MODEL)
        print(f"Local Ollama {info.version}; model={info.name}; preflight={perf_counter() - started:.3f}s (once; metadata excluded from per-turn generation counts).")
        print("Native tool smoke check: actual Qwen responses, recording executor only; no microphone or downloads.")
        return run_cases(client, diagnostics=args.diagnostics)
    except OllamaError as exc:
        print(f"Live tool smoke setup failed ({type(exc).__name__}); no models were downloaded or applications launched.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nLive tool smoke cancelled; no Windows applications were launched.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
