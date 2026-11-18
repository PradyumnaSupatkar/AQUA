"""Explicit local-Qwen parity trials with simulated speech and UIA results only.

No application/worker launches, desktop reads/writes or microphone access. Test
fixtures provide the virtual target. Only loopback inference is real. Optional
JSONL output contains synthetic trial metadata/visible replies, never reasoning.
"""

import argparse
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from dataclasses import asdict
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import Mock, patch

from aqua import chat, handsfree
from aqua.actions.tools import ToolResult
from aqua.brain.controller import ConversationController, local_time_context
from aqua.brain.conversation import Conversation
from aqua.brain.filtering import AnswerChannelGuard
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings, OllamaClient

REQUEST = "Can you write down I am Aqua, your personal assistant into the open notepad window?"


def history_session(scenario):
    conversation = Conversation()
    if scenario == "history":
        call = {"id": "fixture-launch", "function": {"name": "open_application", "arguments": {"app_id": "notepad"}}}
        conversation.commit_tool_turn("Open Notepad.", {"role": "assistant", "content": "", "tool_calls": [call]},
            [ToolResult("notepad", "launch_attempt_succeeded", True).to_message("fixture-launch")],
            final_text="Launch attempt for notepad succeeded; readiness was not checked.")
        conversation.commit(REQUEST, "I cannot access open windows or their UI from here.")
    return conversation


class TraceClient:
    def __init__(self, client):
        self.client, self.first_hash, self.first_tools, self.native_calls = client, None, [], []
        self.replies = []

    def __getattr__(self, name):
        return getattr(self.client, name)

    def stream_chat(self, messages, **options):
        if self.first_hash is None:
            payload = {"messages": messages, **options, "settings": asdict(options["settings"])}
            self.first_hash = sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
            self.first_tools = [d["function"]["name"] for d in options["tools"]]
        iterator = self.client.stream_chat(messages, **options)
        guard, visible = AnswerChannelGuard(), []
        try:
            for event in iterator:
                visible.append(guard.feed(event.content))
                self.native_calls.extend(c["function"]["name"] for c in event.tool_calls)
                yield event
            visible.append(guard.finish())
        finally:
            self.replies.append("".join(visible)[:500])
            close = getattr(iterator, "close", None)
            if close is not None:
                close()


def run_trial(client, mode, scenario, *, request=REQUEST):
    # Import the existing deterministic fixtures only when explicitly invoked.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    import test_text_entry_handsfree as fixture
    from collections import deque
    worker, traced = fixture.Worker(), TraceClient(client)
    conversation, results = history_session(scenario), []
    settings = GenerationSettings(temperature=.2)
    class CaptureController(ConversationController):
        def respond(self, *args, **kwargs):
            value = super().respond(*args, **kwargs)
            results.append(value)
            return value
    output, errors = StringIO(), StringIO()
    started = perf_counter()
    with ExitStack() as stack:
        for target, replacement in (("aqua.desktop.session.run_worker", worker),
                                    ("aqua.desktop.interaction_session.run_worker", worker),
                                    ("aqua.chat.ConversationController", CaptureController)):
            stack.enter_context(patch(target, replacement))
        stack.enter_context(patch("aqua.chat._start", return_value=(traced, conversation, settings)))
        stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError("Real processes are blocked")))
        stack.enter_context(patch("aqua.actions.tools.execute_application", side_effect=AssertionError("Real app execution is blocked")))
        with redirect_stdout(output), redirect_stderr(errors):
            if mode == "chat":
                stack.enter_context(patch("builtins.input", side_effect=[request, "/quit"]))
                status = chat.run_text_chat(settings=settings, text_entry_apps=("notepad",))
            elif mode == "handsfree":
                microphone = fixture.hf.Microphone()
                microphone.frames = deque(fixture.hf.turn_frames())
                recognizer, console = Mock(), Mock()
                recognizer.transcribe.return_value = fixture.hf.transcript(request)
                console.poll.return_value = None
                for target, value in (("SherpaWakeDetector", fixture.hf.AcousticDetector()),
                                      ("StreamingVad", fixture.hf.AcousticVad()),
                                      ("OfflineRecognizer", recognizer), ("MicrophoneStream", microphone),
                                      ("ConsoleCommands", console)):
                    stack.enter_context(patch("aqua.handsfree." + target, return_value=value))
                status = handsfree.run_handsfree(settings=settings, text_entry_apps=("notepad",), language="en",
                    end_silence=.128, initial_timeout=.512, max_utterance=1.024, pre_roll=.064, wake_cooldown=0)
            else:
                raise ValueError("Unknown input mode")
    if not results:
        return {"mode": mode, "scenario": scenario, "status": "harness_error", "frontend_status": status}
    result = results[-1]
    return {"mode": mode, "scenario": scenario, "status": result.task.status, "ok": result.ok,
        "outcome": result.outcome, "stage": result.stage, "decisions": result.task.decisions,
        "operations": [o.tool_name for o in result.task.operations], "native_calls": traced.native_calls,
        "stub_writes": sum(r["operation"] == "set_control_text" for r in worker.calls),
        "first_request_sha256": traced.first_hash, "first_tools": traced.first_tools,
        "stop_reason": result.task.stop_reason, "seconds": round(perf_counter() - started, 3),
        "early_reply": result.text[:500] if not result.task.operations else None,
        "model_replies": traced.replies if not result.ok else [],
        "frontend_status": status, "stderr_present": bool(errors.getvalue())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--trials", type=int, choices=(1, 2, 3), default=2,
                        help="Trials per mode AND history scenario, interleaved")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.live:
        print("No inference or desktop access. Add --live for local-Qwen fixture trials.")
        return 2
    if args.output and args.output.exists():
        parser.error("Output already exists; choose a new file to preserve prior results.")
    client = OllamaClient(timeout=30)
    client.check_model(DEFAULT_CHAT_MODEL)
    clock = local_time_context()
    records = []
    with ExitStack() as stack:
        stream = stack.enter_context(args.output.open("x", encoding="utf-8")) if args.output else None
        stack.enter_context(patch("aqua.brain.controller.local_time_context", return_value=clock))
        for trial in range(1, args.trials + 1):
            for scenario in ("fresh", "history"):
                for mode in ("chat", "handsfree"):
                    record = {"trial": trial, **run_trial(client, mode, scenario)}
                    records.append(record)
                    line = json.dumps(record, ensure_ascii=True)
                    print(line, flush=True)
                    if stream:
                        stream.write(line + "\n")
                        stream.flush()
    for mode in ("chat", "handsfree"):
        group = [r for r in records if r["mode"] == mode]
        print(json.dumps({"summary": mode, "completed": sum(r["status"] == "completed" and r["stub_writes"] == 1 for r in group), "trials": len(group)}), flush=True)
    return 0 if all(r["status"] == "completed" and r["stub_writes"] == 1 for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
