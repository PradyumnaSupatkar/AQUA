"""Opt-in local Qwen paraphrase evaluation; every desktop effect is a stub.

Run with --live --output NEW.jsonl. Without --live no network is used.
Each output is exclusive, dataset-hashed and independently re-scoreable.
"""

import argparse
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter
from unittest.mock import patch

from aqua.actions import tools
from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.filtering import AnswerChannelGuard
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings, OllamaClient

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "benchmarks/semantic-intent-v1/cases.json"


class TraceClient:
    def __init__(self, client):
        self.client, self.decisions = client, []

    def stream_chat(self, messages, **options):
        record = {"tools": [d["function"]["name"] for d in options["tools"]],
                  "input_sha256": sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest(),
                  "calls": [], "visible": ""}
        self.decisions.append(record)
        started, guard = perf_counter(), AnswerChannelGuard()
        iterator = self.client.stream_chat(messages, **options)
        try:
            for event in iterator:
                record["visible"] += guard.feed(event.content)
                record["calls"].extend(deepcopy(event.tool_calls))
                if event.done:
                    for key in ("load_duration", "prompt_eval_count", "prompt_eval_duration",
                                "eval_count", "eval_duration", "done_reason"):
                        if hasattr(event, key):
                            record[key] = getattr(event, key)
                yield event
            record["visible"] += guard.finish()
        finally:
            record["seconds"] = perf_counter() - started
            close = getattr(iterator, "close", None)
            if close:
                close()


def score(turn, record):
    """Predeclared structural grading; never changes expectations after inference."""
    actual, allowed = Counter(record["effects"]), Counter(turn["effects"])
    extras = list((actual - allowed).elements())
    calls = [call for decision in record["decisions"] for call in decision["calls"]]
    interpretation = None
    if "intent" in turn:
        intent = turn["intent"]
        if intent is None:
            interpretation = not calls
        elif not calls:
            interpretation = False
        else:
            function = calls[0].get("function", {})
            arguments = function.get("arguments", {})
            interpretation = function.get("name") == intent["tool"]
            for key in ("app_id", "text"):
                if key in intent:
                    interpretation = interpretation and arguments.get(key) == intent[key]
            if "apps" in intent:
                interpretation = interpretation and arguments.get("app_id") in intent["apps"]
    handling = (actual == allowed and record["pending"] == turn.get("pending")
                and not record.get("exception") and record["ok"])
    if turn.get("outcomes"):
        handling = handling and record["outcome"] in turn["outcomes"]
    if turn.get("inspection"):
        handling = handling and "inspect_window" in record["worker_operations"]
    if turn.get("clarify"):
        # An imperative clarification ("Please clarify ...") is also a
        # clarification. Preserve original JSONL grades; report_intent regrades
        # BOTH runs uniformly without changing the frozen expected actions.
        handling = handling and ("?" in record["reply"] or "please clarify" in record["reply"].casefold()
                                 or record["outcome"] in {"clarification", "confirmation_required"})
    return {"interpretation_correct": interpretation, "handling_correct": bool(handling),
            "false_authorization": bool(extras), "unauthorized_effects": extras,
            "unexpected_proposal": turn.get("intent", {}) is None and bool(calls)}


def run_case(client, case, settings):
    # Existing fixtures exercise the real session/reference machinery. Nothing
    # in this fixture can open a process, read an OS window, or write a document.
    sys.path.insert(0, str(ROOT / "tests"))
    from test_desktop_tasks import DesktopWorker
    worker, traced, effects = DesktopWorker(), TraceClient(client), []
    conversation = Conversation()
    conversation.text_entry_apps = ("notepad",)  # existing opt-in, no expanded scope
    for user, assistant in case.get("history", []):
        conversation.commit(user, assistant)
    engine = ConversationController(traced, conversation, model=DEFAULT_CHAT_MODEL, settings=settings)

    def launch(app):
        effects.append("launch:" + app)
        return tools.ToolResult(app, "launch_attempt_succeeded", True)

    def desktop(request):
        if request["operation"] == "set_control_text":
            effects.append("write:" + request["app_id"] + ":" + request["text"])
        if request["operation"] not in {"list_windows", "inspect_window", "set_control_text"}:
            raise AssertionError("Benchmark does not permit new/append operations")
        return worker(request)

    with ExitStack() as stack:
        for target, replacement in (("aqua.desktop.session.run_worker", desktop),
                                    ("aqua.desktop.interaction_session.run_worker", desktop),
                                    ("aqua.actions.tools.execute_application", launch)):
            stack.enter_context(patch(target, replacement))
        stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError("Real processes blocked")))
        for index, turn in enumerate(case["turns"]):
            effect_start, decision_start, worker_start = len(effects), len(traced.decisions), len(worker.calls)
            started = perf_counter()
            result = engine.respond(turn["text"])
            record = {"case": case["id"], "category": case["category"], "turn": index,
                      "user": turn["text"], "reply": result.text, "ok": result.ok,
                      "outcome": result.outcome, "stage": result.stage, "error": result.error,
                      "seconds": perf_counter() - started, "pending": conversation.action_state.pending_app,
                      "effects": effects[effect_start:], "decisions": traced.decisions[decision_start:],
                      "worker_operations": [r["operation"] for r in worker.calls[worker_start:]],
                      "task": asdict(result.task) if result.task is not None else None,
                      "allows_modification": bool(turn["effects"])}
            record["scores"] = score(turn, record)
            yield record


def summarize(records):
    def metrics(rows):
        interpretations = [r["scores"]["interpretation_correct"] for r in rows
                           if r["scores"]["interpretation_correct"] is not None]
        restricted = [r for r in rows if not r["allows_modification"]]
        return {"turns": len(rows), "interpretation_correct": sum(interpretations),
                "interpretation_total": len(interpretations),
                "handling_correct": sum(r["scores"]["handling_correct"] for r in rows),
                "false_authorizations": sum(r["scores"]["false_authorization"] for r in rows),
                "no_modification_turns": len(restricted),
                "false_authorizations_on_restricted_turns": sum(r["scores"]["false_authorization"] for r in restricted),
                "unexpected_proposals": sum(r["scores"]["unexpected_proposal"] for r in rows),
                "median_task_seconds": statistics.median(r["seconds"] for r in rows) if rows else None,
                "median_model_decision_seconds": statistics.median(
                    d["seconds"] for r in rows for d in r["decisions"]) if any(r["decisions"] for r in rows) else None}
    return {"overall": metrics(records), "categories": {
        category: metrics([r for r in records if r["category"] == category])
        for category in sorted({r["category"] for r in records})}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--trials", type=int, default=2, choices=range(1, 6))
    parser.add_argument("--cases", nargs="+", help="optional frozen case IDs; default all cases")
    args = parser.parse_args(argv)
    if not args.live or args.output is None:
        parser.error("Explicit --live and a new --output path are required (all desktop effects are stubbed).")
    data = DATASET.read_bytes()
    dataset_hash = sha256(data).hexdigest()
    if dataset_hash != DATASET.with_suffix(".sha256").read_text().strip():
        parser.error("Dataset differs from its frozen SHA-256; do not score a changed dataset as v1.")
    cases = json.loads(data)["cases"]
    if args.cases:
        if set(args.cases) - {case["id"] for case in cases}:
            parser.error("Unknown frozen case ID")
        cases = [case for case in cases if case["id"] in args.cases]
    settings = GenerationSettings(temperature=.2, num_ctx=4096, num_predict=1024, seed=42)
    sources = ["src/aqua/brain/controller.py", "src/aqua/actions/tools.py", "src/aqua/desktop/authorization.py",
               "src/aqua/desktop/interaction_session.py", "src/aqua/brain/intent.py", "tools/benchmark_intent.py"]
    metadata = {"kind": "metadata", "dataset_sha256": dataset_hash, "model": DEFAULT_CHAT_MODEL,
                "settings": asdict(settings), "trials": args.trials, "cases": [c["id"] for c in cases],
                "source_sha256": {name: sha256((ROOT / name).read_bytes()).hexdigest()
                                  for name in sources if (ROOT / name).exists()}}
    # Exclusive creation prevents accidental overwrites, including interrupted runs.
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(metadata) + "\n")
        output.flush()
        client = OllamaClient(timeout=60)
        client.check_model(DEFAULT_CHAT_MODEL)
        records = []
        for trial in range(args.trials):
            configured = GenerationSettings(**{**asdict(settings), "seed": 42 + trial})
            for case in cases:
                for record in run_case(client, case, configured):
                    record.update(kind="observation", trial=trial, seed=42 + trial)
                    output.write(json.dumps(record, ensure_ascii=False) + "\n")
                    output.flush()
                    records.append(record)
                    print(f"trial={trial + 1} {case['id']} turn={record['turn']} "
                          f"handling={record['scores']['handling_correct']} "
                          f"false_auth={record['scores']['false_authorization']} {record['seconds']:.3f}s", flush=True)
        summary = {"kind": "summary", **summarize(records)}
        output.write(json.dumps(summary) + "\n")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
