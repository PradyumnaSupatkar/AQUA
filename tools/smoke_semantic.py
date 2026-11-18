"""Historical V0.5 classifier smoke check; never launches an application.

For current V0.7 verification use tools/smoke_tools.py instead. This legacy
diagnostic and its regression fixtures are retained for reproducibility only.

Run from D:\\Aqua with the existing virtual environment:
    .\\.venv\\Scripts\\python.exe tools\\smoke_semantic.py --live --diagnostics

Only the seven fixed fixture phrases below are sent to local Ollama. No
microphone, conversation history, model download, or recording file is used.
"""

import argparse
from dataclasses import dataclass
import sys
from time import perf_counter
from unittest.mock import patch

from aqua.actions import semantic
from aqua.actions.launcher import ActionResult
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, DEFAULT_CONTEXT_SIZE


@dataclass(frozen=True)
class Case:
    text: str
    decisions: tuple[semantic.Decision, ...]
    outcomes: tuple[str, ...]


CASES = (
    Case("Hey Aqua, open up my coding environment.",
         (semantic.Decision("open_app", "vs code"),), ("would_launch:vs code",)),
    Case("Hey Aqua, I feel like doing some programming.",
         (semantic.Decision("ask", "vs code"),), ("clarify:vs code",)),
    Case("Could you bring up my browser?",
         (semantic.Decision("open_app", "chrome"),), ("would_launch:chrome",)),
    Case("Tell me about Google Chrome.",
         (semantic.Decision("chat", "none"),), ("conversation",)),
    Case("Don't open Chrome.",
         (semantic.Decision("chat", "none"), semantic.Decision("unsupported", "none")),
         ("conversation", "denied")),
    Case("Open PowerShell.",
         (semantic.Decision("unsupported", "none"),), ("denied",)),
    Case("Open Chrome and delete my files.",
         (semantic.Decision("unsupported", "none"),), ("denied",)),
)


def _preview_policy(text: str, decision: semantic.Decision) -> str:
    """Exercise production authorization with a cached decision, without effects."""
    state = semantic.ActionState()
    proposed = []

    def preview(app: str) -> ActionResult:
        if app not in semantic.ALLOWED_APPS:
            raise AssertionError("The policy supplied an unapproved application.")
        proposed.append(app)
        return ActionResult(True, "Dry run: application launching is disabled.")

    with patch.object(semantic, "interpret_request", return_value=decision), \
            patch.object(semantic, "open_application", side_effect=preview):
        result = semantic.route_semantic_request(
            text, model=DEFAULT_CHAT_MODEL, state=state,
            num_ctx=DEFAULT_CONTEXT_SIZE, timeout=15.0,
        )
    if proposed:
        if len(proposed) != 1:
            raise AssertionError("The policy proposed multiple launches.")
        return f"would_launch:{proposed[0]}"
    if state.pending_app is not None:
        return f"clarify:{state.pending_app}"
    return "conversation" if result is None else "denied"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly allow local Ollama classification requests.")
    parser.add_argument("--diagnostics", action="store_true", help="Show safe classifier transport diagnostics.")
    args = parser.parse_args(argv)
    if not args.live:
        parser.print_help()
        print("No requests made. Add --live to contact the existing local Ollama server.", file=sys.stderr)
        return 2

    print(f"Local semantic smoke check: {DEFAULT_CHAT_MODEL}; application launches disabled.")
    failures = 0
    # This second guard also protects against a future accidental bypass of
    # the dry-run application function in _preview_policy.
    with patch("aqua.actions.launcher.subprocess.Popen", side_effect=AssertionError("Smoke checks must never launch applications.")):
        for number, case in enumerate(CASES, 1):
            started = perf_counter()
            options = {"model": DEFAULT_CHAT_MODEL, "num_ctx": DEFAULT_CONTEXT_SIZE, "timeout": 15.0}
            if args.diagnostics:
                options["diagnostics"] = True
            try:
                decision = semantic.interpret_request(case.text, **options)
            except semantic.IntentError as exc:
                elapsed = perf_counter() - started
                failures += 1
                print(f"{number}. FAIL {elapsed:.3f}s; classification failed ({type(exc).__name__}).")
                continue
            elapsed = perf_counter() - started
            outcome = _preview_policy(case.text, decision)
            passed = decision in case.decisions and outcome in case.outcomes
            failures += not passed
            print(f"{number}. {'PASS' if passed else 'FAIL'} {elapsed:.3f}s; intent={decision.intent}; app={decision.app}; policy={outcome}")
            print(f"   Fixture: {case.text}")
    print(f"{len(CASES) - failures}/{len(CASES)} cases matched. No application actions were executed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
