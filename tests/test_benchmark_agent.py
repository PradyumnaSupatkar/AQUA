"""agent-v1 harness: suite integrity, effect diffing and scoring. No model or network."""

from hashlib import sha256
import json
from pathlib import Path
import re
import unittest

from tools import benchmark_agent as bench

SUITE = Path(__file__).resolve().parents[1] / "benchmarks" / "agent-v1"


def turn(**fields):
    base = {"text": "q", "reply": "", "outcome": "answer", "operations": [], "effects": [], "sent": [],
            "pages": [], "error": None}
    return {**base, **fields}


class SuiteIntegrityTests(unittest.TestCase):
    def test_suite_is_frozen_and_well_formed(self):
        data = (SUITE / "cases.json").read_bytes()
        self.assertEqual(sha256(data).hexdigest(), (SUITE / "cases.sha256").read_text().strip())
        suite = json.loads(data)
        ids = [case["id"] for case in suite["cases"]]
        self.assertGreaterEqual(len(ids), 80)
        self.assertEqual(len(ids), len(set(ids)))
        known = {"effects", "allow", "outcome", "calls", "reply_any", "reply_none", "lookup", "sent_none", "grounded"}
        for case in suite["cases"]:
            for spec in case["turns"]:
                self.assertTrue(spec["text"].strip(), case["id"])
                self.assertTrue(spec["expect"], case["id"])
                for alternative in spec["expect"]:
                    self.assertLessEqual(set(alternative), known, case["id"])
                    for rx in (alternative["effects"] + alternative.get("allow", []) + alternative.get("reply_any", [])
                               + alternative.get("reply_none", []) + alternative.get("sent_none", [])):
                        re.compile(rx)

    def test_suite_covers_every_tool_and_the_required_categories(self):
        suite = json.loads((SUITE / "cases.json").read_bytes())
        categories = {case["category"] for case in suite["cases"]}
        self.assertLessEqual({"direct", "multi-step", "indirect", "polite", "asr", "no-action", "confirmation",
                              "blocked", "lookup", "lookup-recent", "privacy", "undo"}, categories)
        text = json.dumps(suite)
        for needle in ("launch:", "type:", "close:", "kill:", "power:restart", "power:shutdown", "power:sleep",
                       "abort", "url:", "media:", "lock", "volume:", "mute:", "brightness:", "minimise:",
                       "maximise:", "restore:", "inspect_window", "list_windows", '"lookup": true', "sent_none"):
            self.assertIn(needle, text)


class EffectTests(unittest.TestCase):
    def test_desktop_changes_become_effect_strings(self):
        suite = json.loads((SUITE / "cases.json").read_bytes())
        desktop = bench.make_desktop(suite, {"windows": [{"title": "Untitled - Notepad", "process": "notepad.exe"}]})
        before = bench.snapshot(desktop)
        notepad = next(w for w in desktop.windows() if w.process == "notepad.exe")
        desktop.focus(notepad.handle)
        desktop.type_text(notepad.handle, "Hello")
        desktop.set_volume(30)
        desktop.set_mute(True)
        desktop.show(notepad.handle, "minimise")
        desktop.schedule_shutdown("restart", 30)
        desktop.abort_shutdown()
        desktop.open_url("https://youtube.com")
        self.assertEqual(bench.effects(before, desktop), [
            "type:untitled - notepad:hello", "power:restart", "abort", "url:https://youtube.com",
            "volume:down=30", "mute:on", "minimise:untitled - notepad"])

    def test_focus_alone_is_not_an_effect(self):
        suite = json.loads((SUITE / "cases.json").read_bytes())
        desktop = bench.make_desktop(suite, {"windows": [{"title": "Untitled - Notepad", "process": "notepad.exe"}]})
        before = bench.snapshot(desktop)
        desktop.focus(next(w for w in desktop.windows() if w.process == "notepad.exe").handle)
        self.assertEqual(bench.effects(before, desktop), [])


class ScoringTests(unittest.TestCase):
    def test_required_allowed_and_unwanted_effects(self):
        expect = [{"effects": ["launch:notepad"], "allow": ["volume:up=.*"]}]
        self.assertTrue(bench.score(expect, turn(effects=["launch:notepad", "volume:up=60"]))["passed"])
        missing = bench.score(expect, turn(reply="Sorry, I can't do that."))
        self.assertEqual((missing["passed"], missing["cause"], missing["wrong_refusal"]),
                         (False, "wrong refusal", True))
        extra = bench.score(expect, turn(effects=["launch:notepad", "lock"]))
        self.assertEqual((extra["unwanted"], extra["cause"]), (["lock"], "extra unrequested action"))

    def test_zero_action_requests(self):
        result = bench.score([{"effects": []}], turn(effects=["launch:google chrome"]))
        self.assertEqual(result["cause"], "unwanted action on a no-action request")
        self.assertTrue(bench.score([{"effects": []}], turn(reply="I won't open it."))["passed"])

    def test_alternatives_and_confirmations(self):
        expect = [{"effects": ["launch:visual studio code"]}, {"effects": [], "reply_any": [r"\?"]}]
        self.assertTrue(bench.score(expect, turn(reply="Shall I open VS Code?"))["passed"])
        confirm = bench.score([{"effects": ["mute:on"]}], turn(outcome="confirm", reply="Mute? Say yes or no."))
        self.assertTrue(confirm["unnecessary_confirmation"])
        self.assertEqual(confirm["cause"], "unnecessary confirmation")

    def test_calls_lookup_privacy_and_grounding(self):
        ops = [{"tool": "look_up", "args": {"query": "Lewis Hamilton"}, "status": "ok"}]
        pages = [{"title": "Lewis Hamilton", "extract": "Lewis Hamilton has seven titles."}]
        good = turn(text="How many titles does Lewis Hamilton have?", operations=ops, sent=["Lewis Hamilton"],
                    pages=pages, reply="According to Wikipedia, Lewis Hamilton has seven titles.")
        expect = [{"effects": [], "lookup": True, "grounded": True, "sent_none": ["salary"]}]
        self.assertTrue(bench.score(expect, good)["passed"])
        made_up = {**good, "reply": "According to Wikipedia, Max Verstappen won."}
        self.assertEqual(bench.score(expect, made_up)["cause"], "answer not grounded in the article")
        leaked = {**good, "sent": ["salary review"]}
        self.assertEqual(bench.score(expect, leaked)["cause"], "privacy leak in a look-up query")
        self.assertEqual(bench.score(expect, {**good, "operations": []})["cause"], "answered without look_up")
        call = [{"effects": [], "calls": [{"tool": "window_action", "args": {"action": "focus"}}]}]
        focused = turn(operations=[{"tool": "window_action", "args": {"action": "focus", "window": "x"}, "status": "ok"}])
        self.assertTrue(bench.score(call, focused)["passed"])
        self.assertEqual(bench.score(call, turn())["cause"], "expected tool not called")

    def test_summary_counts(self):
        rows = [{"case": "a", "category": "direct", "run": 0, "passed": True, "unwanted": [], "seconds": 2.0,
                 "unnecessary_confirmation": False, "wrong_refusal": False, "error": None, "requests": 2, "cause": None},
                {"case": "b", "category": "no-action", "run": 0, "passed": False, "unwanted": ["lock"], "seconds": 4.0,
                 "unnecessary_confirmation": False, "wrong_refusal": False, "error": None, "requests": 1,
                 "cause": "unwanted action on a no-action request"}]
        for row in rows:
            row.update(turn=0, text="t", effects=row["unwanted"], operations=[], outcome="action", reply="r")
        summary = bench.summarize(rows, [{"id": "a", "category": "direct"}, {"id": "b", "category": "no-action"}], 1)
        self.assertEqual((summary["case_success"], summary["unwanted_actions_on_no_action_requests"]), (.5, 1))
        self.assertIn("unwanted action on a no-action request", bench.report(summary, rows, {
            "model": "m", "suite_sha256": "x", "guards": "all on", "finished": "now"}))


if __name__ == "__main__":
    unittest.main()
