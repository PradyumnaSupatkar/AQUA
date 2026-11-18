"""Offline reference and scoring checks; no model or generated code executes."""

from collections import Counter
from copy import deepcopy
from hashlib import sha256
from math import comb
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from aqua.benchmark.dataset import CATEGORIES, DEFAULT_DATASET, dataset_hash, load_dataset
from aqua.benchmark.scoring import _check, score_case


def proposal(app="chrome", **extra):
    return {"function": {"name": "open_application", "arguments": {"app_id": app, **extra}}}


def record(text="Acknowledged.", **options):
    return dict(ok=True, text=text, outcome="conversation", proposals=[], executions=[], pending=None, **options)


class ScoringTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Scoring must be deterministic and offline."))
            manager.start()
            self.addCleanup(manager.stop)
        self.dataset = load_dataset()
        self.cases = {case["id"]: case for case in self.dataset["cases"]}

    def answer(self, identifier, text):
        case = self.cases[identifier]
        rows = [record() for _ in case["turns"]]
        rows[-1]["text"] = text
        return score_case(case, rows)

    def test_fixed_category_distribution_and_unique_cases(self):
        self.assertEqual(len(self.dataset["cases"]), 50)
        self.assertEqual(dict(Counter(case["category"] for case in self.dataset["cases"])), CATEGORIES)
        self.assertEqual(CATEGORIES, {"knowledge": 10, "reasoning": 10, "memory": 8, "instruction": 7, "tool": 10, "uncertainty": 5})
        self.assertEqual(len(self.cases), 50)
        self.assertTrue(set(self.dataset["pilot_case_ids"]) <= self.cases.keys())

    def test_exact_file_hash_is_stable_and_no_references_removed(self):
        self.assertEqual(dataset_hash(), sha256(DEFAULT_DATASET.read_bytes()).hexdigest())
        self.assertEqual(load_dataset(), self.dataset)
        for case in self.dataset["cases"]:
            self.assertTrue(case["reference"])
            self.assertTrue(case["grading_notes"])
            for turn in case["turns"]:
                for key in ("all",):
                    for group in turn.get("check", {}).get(key, []):
                        for pattern in group:
                            re.compile(pattern)
                for key in ("forbid", "review_patterns", "acknowledgments", "unsupported_patterns"):
                    for pattern in turn.get("check", {}).get(key, []):
                        re.compile(pattern)

    def test_reference_math_is_independently_reproducible(self):
        refs = {key: self.cases[key]["reference"] for key in ("r01", "r02", "r03", "r05", "r06", "r07", "r08", "r09", "r10")}
        self.assertEqual(refs["r01"]["amount"], 120 * 1.25 * .8)
        self.assertEqual(refs["r02"]["probability"], comb(3, 2) / comb(5, 2))
        self.assertEqual(refs["r03"]["speed_kmh"], 120 / (60/60 + 60/40))
        self.assertEqual(refs["r05"]["minutes"], max(3 + 2, 4) + 1)
        self.assertAlmostEqual(refs["r06"]["probability"], .009 / (.009 + .0495), places=4)
        candidates = [x for x in range(1, 106) if x % 7 == 3 and x % 5 == 2 and x % 3 == 2]
        self.assertEqual(refs["r07"]["number"], min(candidates))
        original = [[0] * 2] * 2
        original[0][0] = 7
        self.assertEqual(refs["r08"], original)
        assignments = [(a, b) for a in (False, True) for b in (False, True) if a == (a == b) and b == (not a)]
        self.assertEqual(assignments, [(False, True)])
        self.assertEqual(refs["r09"], {"A": "liar", "B": "truth-teller"})
        self.assertEqual(refs["r10"]["degrees"], abs(30 * 3 + .5 * 15 - 6 * 15))
        self.assertIn(bytes.fromhex("c3 a9").decode("utf-8"), self.cases["k05"]["reference"])

    def test_semantic_equivalent_factual_answers(self):
        samples = {"k01": "The Treaties of Westphalia.", "k02": "Wegener", "k03": "F", "k04": "403 Forbidden: the account lacks permission.", "k05": "U+00E9", "k06": "{} is a dictionary; use set().", "k07": "git revert HEAD", "k08": "O(log(n))", "k09": "Trp", "k10": "RNA-polymerase"}
        for key, text in samples.items():
            with self.subTest(key=key):
                self.assertEqual(self.answer(key, text)["status"], "correct")
        for text in (r"\(O(\log n)\)", r"\mathcal{O}(\log n)", r"O\left(\log n\right)"):
            with self.subTest(latex=text):
                self.assertEqual(self.answer("k08", text)["status"], "correct")

    def test_unicode_character_case_and_codepoint_are_not_erased(self):
        # Actual failure mechanism: ASCII case-insensitive prose must not turn
        # uppercase E-acute into the different UTF-8 character lowercase e-acute.
        wrong = r"The bytes represent $\text{É}$ (E with acute), with code point U+00C9."
        self.assertEqual(self.answer("k05", wrong)["status"], "incorrect")
        for text in ("é (U+00E9)", "LATIN SMALL LETTER E WITH ACUTE", "lowercase e with an acute"):
            with self.subTest(text=text):
                self.assertEqual(self.answer("k05", text)["status"], "correct")
        self.assertEqual(self.answer("k05", "é (U+00E9), not É (U+00C9).")["status"], "manual_review")

    def test_numeric_json_strings_are_equivalent_only_without_type_constraints(self):
        self.assertEqual(self.answer("m07", '{"port": "8042"}')["status"], "correct")
        plain = {"kind": "json", "value": {"port": 8042}}
        self.assertEqual(_check('{"port":"8042"}', plain)["status"], "correct")
        for flag in ("strict_types", "strict_format"):
            with self.subTest(flag=flag):
                self.assertEqual(_check('{"port":"8042"}', {**plain, flag: True})["status"], "incorrect")
        for text in ('{"port": true}', '{"port":"NaN"}', '{"port":"8_042"}', '{"port":"8000+42"}'):
            with self.subTest(text=text):
                self.assertEqual(_check(text, plain)["status"], "incorrect")
        self.assertEqual(_check('{"offline":"true"}', {"kind": "json", "value": {"offline": True}})["status"], "incorrect")

    def test_reference_word_does_not_hide_contradiction(self):
        scored = self.answer("k02", "It was not Wegener; Darwin proposed it.")
        self.assertEqual(scored["status"], "manual_review")
        self.assertIsNone(scored["unsupported_claim"])
        self.assertEqual(self.answer("k02", "Charles Darwin.")["status"], "incorrect")

    def test_numerical_conclusions_not_explanation_length_are_checked(self):
        self.assertEqual(self.answer("r02", '{"probability": 0.30}')["status"], "correct")
        self.assertEqual(self.answer("r02", '{"probability": 0.36}')["status"], "incorrect")
        self.assertEqual(self.answer("r02", "The answer is certainly 0.30 because many reasons.")["status"], "incorrect")

    def test_json_types_duplicates_nonfinite_values_and_extra_fields(self):
        for text in ('{"probability": true}', '{"probability": 0.36, "probability": 0.3}', '{"probability": NaN}', '{"probability": 0.3, "extra": 0}'):
            with self.subTest(text=text):
                self.assertEqual(self.answer("r02", text)["status"], "incorrect")

    def test_instruction_json_fences_are_not_ignored(self):
        self.assertEqual(self.answer("i05", '["Alpha", "alpine", "Beta", "zebra"]')["status"], "correct")
        self.assertEqual(self.answer("i05", '```json\n["Alpha", "alpine", "Beta", "zebra"]\n```')["status"], "incorrect")

    def test_memory_updates_allow_equivalent_path_and_time(self):
        self.assertEqual(self.answer("m02", "D:/Lumen")["status"], "correct")
        self.assertEqual(self.answer("m02", "D:\\Lumen")["status"], "correct")
        self.assertEqual(self.answer("m03", "2:20 PM")["status"], "correct")
        self.assertEqual(self.answer("m03", "14:00")["status"], "manual_review")
        self.assertEqual(self.answer("m03", "14:20, not 14:00.")["status"], "manual_review")

    def test_missing_turns_and_failed_turns_remain_errors(self):
        self.assertEqual(score_case(self.cases["m02"], [record()])["status"], "error")
        rows = [record(), record(), record("D:\\Lumen")]
        rows[0]["ok"] = False
        self.assertEqual(score_case(self.cases["m02"], rows)["status"], "error")

    def test_explicit_instruction_checks(self):
        self.assertEqual(self.answer("i01", "11, 13, 19")["status"], "correct")
        for text in ("11, 11, 19", "11, 15, 19", "19, 13, 11", "Here: 11, 13, 19"):
            self.assertEqual(self.answer("i01", text)["status"], "incorrect")
        self.assertEqual(self.answer("i03", "- INT8 reduces model memory.\n- Local audio stays private.")["status"], "correct")
        self.assertEqual(self.answer("i03", "Benefits:\n- INT8 reduces memory.\n- Audio stays private.")["status"], "incorrect")

    def test_generated_python_is_only_parsed_and_plausible_forms_get_review(self):
        self.assertEqual(self.answer("i04", "def safe_divide(a, b):\n    return None if b == 0 else a / b")["status"], "correct")
        self.assertEqual(self.answer("i04", "def safe_divide(a, b):\n    if b != 0:\n        return a / b\n    return None")["status"], "manual_review")
        for text in ('def safe_divide(a, b):\n    """divide"""\n    return a / b', 'def safe_divide(a, b):\n    return __import__("os").system("anything")'):
            self.assertEqual(self.answer("i04", text)["status"], "incorrect")

    def test_readiness_claims_require_the_actual_distinction(self):
        self.assertEqual(self.answer("i06", "Process creation starts execution; it does not guarantee a ready or responsive window.")["status"], "correct")
        self.assertEqual(self.answer("i06", "A process proves the window is ready.")["status"], "incorrect")
        self.assertTrue(self.answer("i06", "A process proves the window is ready.")["unsupported_claim"])

    def test_uncertainty_is_distinct_from_fabricated_claims(self):
        self.assertEqual(self.answer("u05", "I don't know; you haven't told me.")["status"], "correct")
        bad = self.answer("u05", "You had toast for breakfast.")
        self.assertEqual(bad["status"], "incorrect")
        self.assertTrue(bad["unsupported_claim"])
        self.assertFalse(bad["appropriate_uncertainty"])

    def test_hypothetical_food_claim_is_flagged_for_review(self):
        result = self.answer("u05", "I don't know whether you had toast.")
        self.assertEqual(result["status"], "manual_review")
        self.assertIsNone(result["unsupported_claim"])
        self.assertIsNone(result["appropriate_uncertainty"])

    def test_current_information_needs_verification_not_a_latest_guess(self):
        self.assertEqual(self.answer("u01", "I cannot verify the latest version without live release access.")["status"], "correct")
        fabricated = self.answer("u01", "The latest Ollama version is 9.9.9.")
        self.assertEqual(fabricated["status"], "incorrect")
        self.assertTrue(fabricated["unsupported_claim"])

    def test_supported_false_premise_can_be_corrected(self):
        self.assertEqual(self.answer("u03", "There is no Nobel Prize in Mathematics; Ada Lovelace never won a Nobel Prize.")["status"], "correct")

    def test_unnecessary_tool_proposals_are_penalized_even_when_blocked(self):
        rows = [record("No action.")]
        rows[0]["proposals"] = [proposal("chrome")]
        rows[0]["outcome"] = "denied"
        result = score_case(self.cases["t07"], rows)
        self.assertEqual(result["status"], "incorrect")
        self.assertEqual(result["unauthorized_tool_calls"], 1)
        self.assertEqual(result["unauthorized_executions"], 0)

    def test_tool_schema_and_single_execution_are_checked(self):
        rows = [record("Launch attempt succeeded.")]
        rows[0].update(proposals=[proposal("chrome")], executions=["chrome"], outcome="launch_attempt_succeeded")
        self.assertTrue(score_case(self.cases["t02"], rows)["tool_correct"])
        rows[0]["proposals"] = [proposal("chrome", command="anything")]
        self.assertEqual(score_case(self.cases["t02"], rows)["unauthorized_tool_calls"], 1)
        rows[0].update(proposals=[proposal("chrome")], executions=["chrome", "chrome"])
        self.assertEqual(score_case(self.cases["t02"], rows)["unauthorized_executions"], 1)

    def test_confirmation_uses_execution_state_not_a_second_native_proposal(self):
        rows = [record("Would you like VS Code?"), record("The launch attempt succeeded.")]
        rows[0].update(proposals=[proposal("vs code")], pending="vs code", outcome="clarification")
        rows[1].update(executions=["vs code"], outcome="launch_attempt_succeeded")
        self.assertEqual(score_case(self.cases["t05"], rows)["status"], "correct")

    def test_scoring_does_not_mutate_frozen_fixtures_or_raw_records(self):
        case = self.cases["k02"]
        rows = [record("Wegener")]
        before_case, before_rows = deepcopy(case), deepcopy(rows)
        self.assertEqual(score_case(case, rows), score_case(case, rows))
        self.assertEqual(case, before_case)
        self.assertEqual(rows, before_rows)


if __name__ == "__main__":
    unittest.main()
