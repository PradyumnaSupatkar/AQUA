"""Explicit local-Qwen/UIA smoke check with application launching disabled.

Only the named approved app's currently visible interface may be observed.
No UI tree, conversation, audio, or reasoning is saved by this helper.
"""

import argparse
import json
from math import isfinite
import sys
from unittest.mock import patch

from aqua.actions import tools
from aqua.actions.launcher import ALLOWED_APPS
from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings, OllamaClient, OllamaError


def run_demo(client, app, conversation=None) -> int:
    """Run production native observation; allow only the UIA Python worker process."""
    if type(app) is not str or app not in ALLOWED_APPS:
        raise ValueError("Choose one canonical approved application.")
    session = conversation if conversation is not None else Conversation()
    controller = ConversationController(client, session, model=DEFAULT_CHAT_MODEL,
                                        settings=GenerationSettings(temperature=0.2))
    with patch.object(tools, "execute_application",
                      side_effect=AssertionError("Observation smoke checks cannot launch applications.")) as launch_guard:
        result = controller.respond(f"What can you see in my {app} window?")
    print(f"Approved read-only scope: {app}.")
    print(result.text)
    task = result.task
    for operation in task.operations:
        print(f"Operation: {operation.tool_name}; status={operation.status}")
    # Report counts/timing only; never dump the raw accessible tree or model
    # answer. The controller above renders selected real evidence safely.
    history = session.history
    start = max((index for index, row in enumerate(history) if row["role"] == "user"), default=0)
    for message in history[start:]:
        if message["role"] != "tool":
            continue
        payload = json.loads(message["content"])
        name = payload.get("tool")
        if name not in {"list_windows", "inspect_window"}:
            continue
        data = payload.get("data", {})
        count_key = "windows" if name == "list_windows" else "controls"
        values = data.get(count_key, []) if type(data) is dict else []
        count = len(values) if type(values) is list else 0
        elapsed = payload.get("elapsed_seconds")
        timing = (f"{elapsed:.3f}s" if type(elapsed) in (int, float)
                  and isfinite(elapsed) and elapsed >= 0 else "n/a")
        print(f"Observation metrics: {name}; elapsed={timing}; {count_key}={count}")
    print(f"Task: {task.status}; decisions={task.decisions}; operations={len(task.operations)}; "
          f"requests={result.requests}; total={result.total_seconds:.3f}s")
    successful = {operation.tool_name for operation in task.operations
                  if operation.status == "observation_succeeded"}
    passed = (result.ok and task.status == "completed" and not launch_guard.called
              and {"list_windows", "inspect_window"} <= successful)
    if passed:
        print("PASS: approved window discovered and inspected; no application was launched or manipulated.")
    else:
        print("LIMITATION: discovery and inspection did not both complete successfully. "
              "The approved app may be closed, inaccessible, or expose limited UIA metadata; no interaction was attempted.")
    return 0 if passed else 1


def main(argv=None) -> int:
    from aqua.cli import _configure_output
    _configure_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly allow read-only local UIA and loopback Qwen requests.")
    parser.add_argument("--app", required=True, choices=tuple(ALLOWED_APPS),
                        help="One approved app to observe; no desktop-wide scope is available.")
    args = parser.parse_args(argv)
    if not args.live:
        parser.print_help()
        print("No requests made. Add --live to allow this explicitly scoped read-only check.", file=sys.stderr)
        return 2
    try:
        client = OllamaClient()
        info = client.check_model(DEFAULT_CHAT_MODEL)
        print(f"Local Ollama {info.version}; model={info.name}; temperature=0.2.")
        return run_demo(client, args.app)
    except OllamaError as exc:
        print(f"Observation smoke setup failed ({type(exc).__name__}); no application was launched.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nObservation smoke cancelled; no application was manipulated.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
