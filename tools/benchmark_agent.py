"""agent-v1: evaluate the new action engine on the frozen suite.

Real local Qwen through Ollama, the real Wikipedia API for look_up, and the FAKE
desktop for every Windows effect: nothing on this PC is launched, typed, closed,
muted, locked or shut down. Each case starts from a fresh session.

  python tools/benchmark_agent.py                      # 2 runs per case -> benchmarks/agent-v1/baseline.*
  python tools/benchmark_agent.py --cases fact- --runs 1 --output pilot
  python tools/benchmark_agent.py --disable-guard claim_retry --output no-claim-retry   # ablation
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import median
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aqua.agent.context import ToolContext  # noqa: E402
from aqua.agent.guards import DEFAULT_GUARDS, GUARD_NAMES, without  # noqa: E402
from aqua.agent.loop import AgentEngine  # noqa: E402
from aqua.agent.policy import Policy  # noqa: E402
from aqua.agent.prompt import SYSTEM_PROMPT  # noqa: E402
from aqua.brain.conversation import Conversation  # noqa: E402
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings, OllamaClient  # noqa: E402
from aqua.tools import build_registry  # noqa: E402
from aqua.tools.adapter import AppEntry, Battery  # noqa: E402
from aqua.tools.app_index import AppIndex  # noqa: E402
from aqua.tools.fake import FakeDesktop  # noqa: E402
from aqua.tools.knowledge import WikipediaClient  # noqa: E402

SUITE = ROOT / "benchmarks" / "agent-v1"
REFUSAL = re.compile(r"\b(?:can't|cannot|can not|won't|unable|not able|blocked|not allowed|sorry)\b", re.I)
EXECUTED = {"ok", "unverified", "failed", "empty"}
_NAME = re.compile(r"\b[A-Z][\w'’-]+")
_COMMON = {"according", "wikipedia", "the", "it", "i", "he", "she", "they", "this", "that", "in", "on", "as", "a",
           "an", "and", "but", "so", "yes", "no", "sorry", "there", "its", "his", "her", "however", "also", "today",
           "after", "before", "with", "for", "from", "at", "by", "of", "to", "if", "when", "while", "since", "we",
           "you", "my", "your", "aqua", "here", "okay", "ok", "what", "who", "which"}


# -- the fake desktop for one case ------------------------------------------------------------------

def make_desktop(suite: dict, setup: dict) -> FakeDesktop:
    defaults = suite["defaults"]
    apps = [AppEntry(name, target, kind, exe) for name, target, kind, exe in suite["apps"]]
    front = defaults["front_window"]
    desktop = FakeDesktop(apps=apps, volume=setup.get("volume", defaults["volume"]),
                          muted=setup.get("muted", defaults["muted"]),
                          playback=setup.get("playback", defaults["playback"]),
                          brightness=setup.get("brightness", defaults["brightness"]),
                          battery=Battery(True, defaults["battery_percent"], False, 190))
    desktop._windows[0] = desktop._windows[0].__class__(1, front["title"], front["process"], 100)
    for window in setup.get("windows", ()):
        added = desktop.add_window(window["title"], window["process"], front=window.get("front", False))
        for key, controls in setup.get("controls", {}).items():
            if key.casefold() in window["title"].casefold():
                desktop.controls[added.handle] = controls
    return desktop


def snapshot(desktop: FakeDesktop) -> dict:
    return {"launched": len(desktop.launched), "typed": len(desktop.typed), "closed": len(desktop.closed),
            "terminated": len(desktop.terminated), "power": len(desktop.power), "urls": len(desktop.opened_urls),
            "media": len(desktop.media), "locked": desktop.locked, "volume": desktop.volume, "muted": desktop.muted,
            "brightness": desktop.brightness,
            "windows": {w.handle: (w.title, w.process, w.pid, w.minimized, w.maximized) for w in desktop.windows()}}


def effects(before: dict, desktop: FakeDesktop) -> list[str]:
    """What changed on the fake desktop, as lowercase effect strings (see cases.json schema)."""
    after = snapshot(desktop)
    known = {**before["windows"], **after["windows"]}
    out = [f"launch:{name}" for name in desktop.launched[before["launched"]:]]
    out += [f"type:{known.get(h, ('?',))[0]}:{text}" for h, text in desktop.typed[before["typed"]:]]
    out += [f"close:{known.get(h, ('?',))[0]}" for h in desktop.closed[before["closed"]:]]
    by_pid = {w[2]: w[1] for w in known.values()}
    out += [f"kill:{by_pid.get(pid, pid)}" for pid in desktop.terminated[before["terminated"]:]]
    out += ["abort" if kind == "abort" else f"power:{kind}" for kind, _ in desktop.power[before["power"]:]]
    out += [f"url:{url}" for url in desktop.opened_urls[before["urls"]:]]
    out += [f"media:{key}" for key in desktop.media[before["media"]:]]
    if desktop.locked and not before["locked"]:
        out.append("lock")
    for name in ("volume", "brightness"):
        old, new = before[name], after[name]
        if old is not None and new != old:
            out.append(f"{name}:{'up' if new > old else 'down'}={new}")
    if after["muted"] != before["muted"]:
        out.append(f"mute:{'on' if after['muted'] else 'off'}")
    for handle, (title, _process, _pid, was_min, was_max) in before["windows"].items():
        now = after["windows"].get(handle)
        if now is None:
            continue
        if now[3] and not was_min:
            out.append(f"minimise:{title}")
        elif now[4] and not was_max:
            out.append(f"maximise:{title}")
        elif (was_min and not now[3]) or (was_max and not now[4]):
            out.append(f"restore:{title}")
    # Windows opened during the turn start normal; report it if one ended minimised or maximised.
    for handle, (title, _process, _pid, minimized, maximized) in after["windows"].items():
        if handle not in before["windows"] and (minimized or maximized):
            out.append(f"{'minimise' if minimized else 'maximise'}:{title}")
    return [effect.casefold() for effect in out]


class RecordingWikipedia(WikipediaClient):
    """The real client; remembers what was sent and what came back, per turn."""

    def __init__(self):
        super().__init__()
        self.sent, self.pages = [], []

    def lookup(self, query):
        self.sent.append(query)
        page = super().lookup(query)
        if page is not None:
            self.pages.append(page)
        return page


# -- scoring -----------------------------------------------------------------------------------------

def _match(pattern: str, value: str) -> bool:
    return re.fullmatch(pattern, value, re.IGNORECASE | re.DOTALL) is not None


def grounded(reply: str, question: str, pages: list[dict]) -> bool:
    source = " ".join([question, *(page["title"] + " " + page["extract"] for page in pages)]).casefold()
    names = {token.casefold() for token in _NAME.findall(reply)} - _COMMON
    return all(name in source for name in names)


def check(alternative: dict, turn: dict, *, question_ok: bool = False) -> list[str]:
    """Names of the failed checks of one expectation alternative (empty = pass).

    ``question_ok``: a host yes/no question (outcome "confirm") satisfies an alternative
    that accepts the model asking ("clarify"). Off for the frozen, strict scoring.
    """
    failed = []
    observed = turn["effects"]
    allowed = alternative["effects"] + alternative.get("allow", [])
    if any(not any(_match(p, e) for e in observed) for p in alternative["effects"]):
        failed.append("missing_action")
    if any(not any(_match(p, e) for p in allowed) for e in observed):
        failed.append("unwanted_action")
    if "outcome" in alternative and turn["outcome"] not in alternative["outcome"] and not (
            question_ok and turn["outcome"] == "confirm" and "clarify" in alternative["outcome"]):
        failed.append("outcome")
    for call in alternative.get("calls", ()):
        if not any(op["tool"] == call["tool"] and op["status"] in EXECUTED | {"duplicate"}
                   and all(_match(rx, str(op["args"].get(k, ""))) for k, rx in call.get("args", {}).items())
                   for op in turn["operations"]):
            failed.append("missing_call")
    reply = turn["reply"]
    if alternative.get("reply_any") and not any(re.search(rx, reply, re.I) for rx in alternative["reply_any"]):
        failed.append("reply")
    if any(re.search(rx, reply, re.I) for rx in alternative.get("reply_none", ())):
        failed.append("reply")
    if alternative.get("lookup") and not any(op["tool"] == "look_up" and op["status"] == "ok"
                                             for op in turn["operations"]):
        failed.append("no_lookup")
    if any(re.search(rx, query, re.I) for rx in alternative.get("sent_none", ()) for query in turn["sent"]):
        failed.append("privacy_leak")
    if alternative.get("grounded") and turn["pages"] is not None and not grounded(reply, turn["text"], turn["pages"]):
        failed.append("ungrounded")
    if turn["error"]:
        failed.append("error")
    return failed


def score(expect: list[dict], turn: dict) -> dict:
    results = [check(alternative, turn) for alternative in expect]
    lenient = any(not check(alternative, turn, question_ok=True) for alternative in expect)
    best = min(range(len(expect)), key=lambda i: len(results[i]))
    allowed = [p for alternative in expect for p in alternative["effects"] + alternative.get("allow", [])]
    unwanted = [e for e in turn["effects"] if not any(_match(p, e) for p in allowed)]
    wants_action = any(alternative["effects"] or alternative.get("calls") for alternative in expect)
    expects_confirm = any("confirm" in alternative.get("outcome", ()) for alternative in expect)
    accepts_question = any({"confirm", "clarify"} & set(alternative.get("outcome", ())) for alternative in expect)
    did_something = bool(turn["effects"]) or any(op["status"] in EXECUTED for op in turn["operations"])
    refused = bool(REFUSAL.search(turn["reply"])) or any(op["status"] == "denied" for op in turn["operations"])
    passed = not results[best]
    return {
        "passed": passed, "passed_question_ok": passed or lenient, "failed_checks": results[best],
        "unwanted": unwanted,
        "unnecessary_confirmation": turn["outcome"] == "confirm" and not accepts_question,
        "wrong_refusal": (not passed and wants_action and not expects_confirm and not did_something and refused),
        "cause": None if passed else cause(results[best], turn, unwanted, expects_confirm, wants_action, refused),
    }


def cause(failed: list[str], turn: dict, unwanted: list[str], expects_confirm: bool, wants_action: bool,
          refused: bool) -> str:
    if turn["error"]:
        return "model or protocol error"
    if unwanted and not wants_action:
        return "unwanted action on a no-action request"
    if "privacy_leak" in failed:
        return "privacy leak in a look-up query"
    if turn["outcome"] == "confirm" and not expects_confirm:
        return "unnecessary confirmation"
    if "missing_action" in failed and wants_action:
        if unwanted:
            return "wrong target or arguments"
        if refused:
            return "wrong refusal"
        if turn["outcome"] == "clarify":
            return "asked instead of acting"
        return "action not taken (model only talked)"
    if unwanted:
        return "extra unrequested action"
    if "missing_call" in failed:
        return "expected tool not called"
    if "no_lookup" in failed:
        return "answered without look_up"
    if "ungrounded" in failed:
        return "answer not grounded in the article"
    if "outcome" in failed:
        return "wrong outcome (confirm/decline/cancel flow)"
    return "reply check failed"


# -- running ---------------------------------------------------------------------------------------

def run_case(case: dict, suite: dict, client, model: str, settings, guards, run: int) -> list[dict]:
    setup = case.get("setup", {})
    desktop = make_desktop(suite, setup)
    wiki = RecordingWikipedia()
    context = ToolContext(desktop=desktop, apps=AppIndex(desktop.app_entries, clock=desktop.monotonic), policy=Policy())
    conversation = Conversation(system_prompt=SYSTEM_PROMPT)
    for user, assistant in setup.get("history", ()):
        conversation.commit(user, assistant)
    engine = AgentEngine(client, model=model, settings=settings, registry=build_registry(wikipedia=wiki),
                         context=context, conversation=conversation, guards=guards)
    rows = []
    for index, spec in enumerate(case["turns"]):
        before, wiki.sent, wiki.pages = snapshot(desktop), [], []
        started = perf_counter()
        error, result = None, None
        try:
            result = engine.respond(spec["text"])
            error = result.error
        except Exception as exc:  # noqa: BLE001 - recorded, the suite continues
            error = f"{type(exc).__name__}: {exc}"
        turn = {
            "case": case["id"], "category": case["category"], "run": run, "turn": index, "text": spec["text"],
            "reply": result.text if result else "", "outcome": result.outcome if result else "error",
            "operations": [{"tool": op.tool, "args": op.args, "status": op.status}
                           for op in (result.task.operations if result and result.task else ())],
            "effects": effects(before, desktop), "sent": list(wiki.sent),
            "pages": [{"title": p["title"], "extract": p["extract"]} for p in wiki.pages],
            "requests": result.requests if result else 0, "seconds": round(perf_counter() - started, 3),
            "error": error,
        }
        turn.update(score(spec["expect"], turn))
        rows.append(turn)
    return rows


def summarize(rows: list[dict], cases: list[dict], runs: int) -> dict:
    by_case = defaultdict(list)
    for row in rows:
        by_case[(row["case"], row["run"])].append(row)
    case_pass = {key: all(r["passed"] for r in value) for key, value in by_case.items()}
    both = [cid for cid in {c["id"] for c in cases} if all(case_pass.get((cid, run), False) for run in range(runs))]
    seconds = sorted(r["seconds"] for r in rows)
    categories = defaultdict(lambda: [0, 0])
    for (cid, _run), ok in case_pass.items():
        category = next(c["category"] for c in cases if c["id"] == cid)
        categories[category][0] += ok
        categories[category][1] += 1
    no_action = [r for r in rows if r["category"] == "no-action"]
    return {
        "cases": len(cases), "runs": runs, "turns": len(rows),
        "case_success": sum(case_pass.values()) / len(case_pass),
        "case_success_question_ok": sum(all(r.get("passed_question_ok", r["passed"]) for r in value)
                                        for value in by_case.values()) / len(by_case),
        "cases_passing_every_run": len(both),
        "turn_success": sum(r["passed"] for r in rows) / len(rows),
        "unwanted_actions": sum(len(r["unwanted"]) for r in rows),
        "unwanted_actions_on_no_action_requests": sum(len(r["unwanted"]) for r in no_action),
        "unnecessary_confirmations": sum(r["unnecessary_confirmation"] for r in rows),
        "wrong_refusals": sum(r["wrong_refusal"] for r in rows),
        "errors": sum(bool(r["error"]) for r in rows),
        "latency_seconds": {"median": median(seconds), "p90": seconds[int(.9 * (len(seconds) - 1))],
                            "max": seconds[-1]},
        "model_requests_per_turn": sum(r["requests"] for r in rows) / len(rows),
        "by_category": {k: {"passed": v[0], "of": v[1]} for k, v in sorted(categories.items())},
        "failure_causes": dict(Counter(r["cause"] for r in rows if r["cause"]).most_common()),
    }


def report(summary: dict, rows: list[dict], meta: dict) -> str:
    s = summary
    lines = [
        "# agent-v1 baseline", "",
        f"New action engine, {meta['model']}, fake desktop, real Wikipedia API. {s['cases']} cases x {s['runs']} runs "
        f"({s['turns']} turns). Suite SHA-256 `{meta['suite_sha256']}`; guards: {meta['guards']}. {meta['finished']}.",
        "", "| Metric | Value |", "|---|---|",
        f"| Task success (case runs) | {s['case_success']:.1%} |",
        f"| Cases passing in every run | {s['cases_passing_every_run']} / {s['cases']} |",
        f"| Turn success | {s['turn_success']:.1%} |",
        f"| Unwanted actions (all turns) | {s['unwanted_actions']} |",
        f"| Unwanted actions on no-action requests | {s['unwanted_actions_on_no_action_requests']} |",
        f"| Unnecessary confirmations | {s['unnecessary_confirmations']} |",
        f"| Wrong refusals | {s['wrong_refusals']} |",
        f"| Errors | {s['errors']} |",
        f"| Latency per request, median / p90 / max | {s['latency_seconds']['median']:.1f} s / "
        f"{s['latency_seconds']['p90']:.1f} s / {s['latency_seconds']['max']:.1f} s |",
        f"| Model requests per turn | {s['model_requests_per_turn']:.2f} |",
        "", "## By category (case runs passed)", "", "| Category | Passed |", "|---|---|",
        *[f"| {k} | {v['passed']} / {v['of']} |" for k, v in s["by_category"].items()],
        "", "## Failures grouped by cause", "",
    ]
    grouped = defaultdict(list)
    for row in rows:
        if row["cause"]:
            grouped[row["cause"]].append(row)
    for name, items in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        lines += [f"### {name} ({len(items)})", ""]
        for row in items:
            effects_text = ", ".join(row["effects"]) or "no effects"
            tools = ", ".join(f"{op['tool']}={op['status']}" for op in row["operations"]) or "no tools"
            lines.append(f"- `{row['case']}` run {row['run'] + 1} turn {row['turn'] + 1}: \"{row['text']}\" -> "
                         f"{effects_text}; {tools}; outcome {row['outcome']}; reply: \"{row['reply'][:160]}\""
                         + (f"; unwanted: {', '.join(row['unwanted'])}" if row["unwanted"] else ""))
        lines.append("")
    return "\n".join(lines) + "\n"


def load_suite(extra: bool = False) -> tuple[dict, list[dict], str]:
    """The frozen suite (and optionally cases-extra.json); each file must match its SHA-256."""
    def frozen(name: str) -> tuple[dict, str]:
        data = (SUITE / f"{name}.json").read_bytes()
        digest = sha256(data).hexdigest()
        if digest != (SUITE / f"{name}.sha256").read_text().strip():
            raise SystemExit(f"{name}.json does not match its frozen SHA-256; refusing to run a changed suite.")
        return json.loads(data), digest
    suite, digest = frozen("cases")
    cases = list(suite["cases"])
    if extra:
        extra_suite, extra_digest = frozen("cases-extra")
        cases += extra_suite["cases"]
        digest += f" + extra {extra_digest}"
    return suite, cases, digest


def load_rows(stem: str) -> list[dict]:
    with open(SUITE / f"{stem}.jsonl", encoding="utf-8") as handle:
        return [row for row in map(json.loads, handle) if row.get("kind") != "metadata"]


def rescore(rows: list[dict], cases: list[dict]) -> list[dict]:
    """Score saved turns again with the current rules (effects stay as recorded)."""
    specs = {(case["id"], index): spec for case in cases for index, spec in enumerate(case["turns"])}
    out = []
    for row in rows:
        spec = specs.get((row["case"], row["turn"]))
        if spec is not None:
            out.append({**row, **score(spec["expect"], row)})
    return out


def compare(base_stem: str, new_stem: str, extra: bool) -> str:
    """Markdown comparing two runs on the frozen cases (both re-scored with the same rules)."""
    suite, cases, digest = load_suite(extra)
    frozen_ids = {case["id"] for case in suite["cases"]}
    frozen_cases = [case for case in cases if case["id"] in frozen_ids]
    base = rescore(load_rows(base_stem), frozen_cases)
    new_all = rescore(load_rows(new_stem), cases)
    new = [row for row in new_all if row["case"] in frozen_ids]
    runs = lambda rows: len({row["run"] for row in rows})  # noqa: E731
    b, n = summarize(base, frozen_cases, runs(base)), summarize(new, frozen_cases, runs(new))
    per = lambda value, rows: value / runs(rows)  # noqa: E731

    def rate(rows, key="passed"):
        by_case = defaultdict(lambda: defaultdict(lambda: True))
        for row in rows:
            by_case[row["case"]][row["run"]] &= row.get(key, row["passed"])
        return {cid: sum(v.values()) / len(v) for cid, v in by_case.items()}

    base_rate, new_rate = rate(base), rate(new)
    worse = sorted((cid for cid in new_rate if new_rate[cid] < base_rate.get(cid, 0)),
                   key=lambda cid: new_rate[cid] - base_rate[cid])
    lines = [f"# agent-v1: after fixes 1-5 ({new_stem}) vs baseline", "",
             f"Frozen suite SHA-256 `{digest}`. Both runs re-scored with the current harness rules; the baseline's "
             "effects were recorded before the harness fix for windows created during a turn.", "",
             f"| Metric | Baseline ({runs(base)} runs) | Fixed ({runs(new)} runs) |", "|---|---|---|",
             f"| Task success, strict (frozen rules) | {b['case_success']:.1%} | {n['case_success']:.1%} |",
             f"| Task success, a host yes/no question counts as asking | {b['case_success_question_ok']:.1%} | "
             f"{n['case_success_question_ok']:.1%} |",
             f"| Cases passing in every run (strict) | {b['cases_passing_every_run']} / {b['cases']} | "
             f"{n['cases_passing_every_run']} / {n['cases']} |",
             f"| Unwanted actions per run | {per(b['unwanted_actions'], base):.1f} | {per(n['unwanted_actions'], new):.1f} |",
             f"| Unwanted on no-action requests per run | {per(b['unwanted_actions_on_no_action_requests'], base):.1f} | "
             f"{per(n['unwanted_actions_on_no_action_requests'], new):.1f} |",
             f"| Unnecessary confirmations per run | {per(b['unnecessary_confirmations'], base):.1f} | "
             f"{per(n['unnecessary_confirmations'], new):.1f} |",
             f"| Wrong refusals per run | {per(b['wrong_refusals'], base):.1f} | {per(n['wrong_refusals'], new):.1f} |",
             f"| Latency per request, median / p90 | {b['latency_seconds']['median']:.2f} s / "
             f"{b['latency_seconds']['p90']:.1f} s | {n['latency_seconds']['median']:.2f} s / "
             f"{n['latency_seconds']['p90']:.1f} s |",
             f"| Model requests per turn | {b['model_requests_per_turn']:.2f} | {n['model_requests_per_turn']:.2f} |",
             "", "## Cases that got worse (strict pass rate)", ""]
    lines += [f"- `{cid}`: {base_rate.get(cid, 0):.0%} -> {new_rate[cid]:.0%}"
              + (f" (question-ok: {rate(new, 'passed_question_ok')[cid]:.0%})") for cid in worse] or ["- none"]
    better = sorted(cid for cid in new_rate if new_rate[cid] > base_rate.get(cid, 0))
    lines += ["", "## Cases that got better (strict pass rate)", ""]
    lines += [f"- `{cid}`: {base_rate.get(cid, 0):.0%} -> {new_rate[cid]:.0%}" for cid in better] or ["- none"]
    if extra:
        extra_cases = [case for case in cases if case["id"] not in frozen_ids]
        extra_rows = [row for row in new_all if row["case"] not in frozen_ids]
        e = summarize(extra_rows, extra_cases, runs(extra_rows))
        lines += ["", f"## Extra cases (cases-extra.json, {len(extra_cases)} cases x {runs(extra_rows)} runs)", "",
                  f"Strict success {e['case_success']:.1%}; unwanted actions {e['unwanted_actions']}.", "",
                  "| Case | Passed runs |", "|---|---|"]
        extra_rate = rate(extra_rows)
        lines += [f"| `{case['id']}` | {extra_rate.get(case['id'], 0):.0%} |" for case in extra_cases]
    failures = report(n, new_all, {"model": "", "suite_sha256": digest, "guards": "all on", "finished": ""})
    lines += ["", "## Remaining failures (fixed run, strict)", "",
              failures.split("## Failures grouped by cause", 1)[1].strip()]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--cases", help="only cases whose id contains this text")
    parser.add_argument("--output", default="baseline", help="file stem inside benchmarks/agent-v1")
    parser.add_argument("--model", default=DEFAULT_CHAT_MODEL)
    parser.add_argument("--disable-guard", action="append", default=[], choices=GUARD_NAMES,
                        help="turn one host guard off (evaluation only); repeatable")
    parser.add_argument("--extra", action="store_true", help="also run benchmarks/agent-v1/cases-extra.json")
    parser.add_argument("--compare", nargs=2, metavar=("BASE", "NEW"),
                        help="no model run: write NEW-report.md comparing two saved runs")
    args = parser.parse_args(argv)
    if args.compare:
        base_stem, new_stem = args.compare
        (SUITE / f"{new_stem}-report.md").write_bytes(compare(base_stem, new_stem, args.extra).encode("utf-8"))
        print(f"wrote {new_stem}-report.md")
        return 0
    suite, cases, digest = load_suite(args.extra)
    cases = [c for c in cases if not args.cases or args.cases in c["id"]]
    guards = without(*args.disable_guard) if args.disable_guard else DEFAULT_GUARDS
    client = OllamaClient(timeout=180, keep_alive="30m")
    client.check_model(args.model)
    settings = GenerationSettings()
    rows, started = [], perf_counter()
    for run in range(args.runs):
        for number, case in enumerate(cases, 1):
            case_rows = run_case(case, suite, client, args.model, settings, guards, run)
            rows += case_rows
            mark = "ok  " if all(r["passed"] for r in case_rows) else "FAIL"
            print(f"run {run + 1} {number:3}/{len(cases)} {mark} {case['id']}: "
                  + " | ".join(f"{r['reply'][:70]!r} {r['cause'] or ''}" for r in case_rows), flush=True)
    meta = {"model": args.model, "suite_sha256": digest, "runs": args.runs,
            "guards": "all on" if not args.disable_guard else "off: " + ", ".join(args.disable_guard),
            "finished": f"Run {datetime.now().astimezone():%Y-%m-%d %H:%M %Z}, {perf_counter() - started:.0f} s",
            "source_sha256": {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                "src/aqua/agent/loop.py", "src/aqua/agent/prompt.py", "src/aqua/agent/selection.py",
                "src/aqua/tools/knowledge.py")}}
    summary = summarize(rows, cases, args.runs)
    with open(SUITE / f"{args.output}.jsonl", "w", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps({"kind": "metadata", **meta}) + "\n")
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    (SUITE / f"{args.output}-summary.json").write_bytes((json.dumps({**meta, **summary}, indent=1) + "\n").encode())
    (SUITE / ("report.md" if args.output == "baseline" else f"{args.output}-report.md")).write_bytes(
        report(summary, rows, meta).encode("utf-8"))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
