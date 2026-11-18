"""Reproducible benchmark execution uses synthetic replies and stubbed effects."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aqua.benchmark import runner
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import ChatEvent, GenerationSettings, ModelInfo, OllamaGenerationLimitError


def proposal(app):
    return ChatEvent(done=True, tool_calls=({"function": {"name": "open_application", "arguments": {"app_id": app}}},))


def fixture(identifier="fact", category="knowledge", turns=None):
    return {"id": identifier, "category": category, "temporal": "static", "reference": "REFERENCE_NOT_MODEL_INPUT",
            "grading_notes": "SCORING_NOT_MODEL_INPUT", "turns": turns or [{"text": "Return the number 7.", "check": {"kind": "json", "value": 7}}]}


CONFIG = {"schema_version": 1, "dataset": "dataset.json", "shared": {"num_ctx": 4096, "num_predict": 512, "seed": 42},
          "configurations": [{"id": "A", "model": "fixture:local", "thinking": "off", "temperature": .2}]}


class Client:
    instances = []

    def __init__(self, **kwargs):
        self.chat_request_count = 0
        self.requests = []
        self.validations = []
        self.__class__.instances.append(self)

    def check_model(self, name):
        return ModelInfo(name, "fixture", "test", "non_thinking")

    def validate_thinking(self, name, mode):
        self.validations.append((name, mode))
        return False

    def _json(self, path):
        return {"models": []}

    def stream_chat(self, messages, **options):
        self.chat_request_count += 1
        self.requests.append((deepcopy(messages), options))
        if messages[-1]["role"] == "tool":
            yield ChatEvent("The verified launch attempt succeeded.")
        elif messages[-1]["content"] in {"Please open Notepad.", "I feel like writing."}:
            yield proposal("notepad")
            return
        else:
            yield ChatEvent("7")
        yield ChatEvent(done=True, eval_count=2, eval_duration=100_000_000,
                        prompt_eval_count=20, prompt_eval_duration=50_000_000, load_duration=2_000_000)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        Client.instances = []
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Benchmark tests must have no external effects."))
            manager.start()
            self.addCleanup(manager.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def args(self, *extra):
        import argparse
        parser = argparse.ArgumentParser()
        runner.add_arguments(parser)
        return parser.parse_args(list(extra))

    def files(self, cases=None):
        config = self.directory / "config.json"
        config.write_text(json.dumps(CONFIG), encoding="utf-8")
        dataset = {"schema_version": 1, "cases": cases or [fixture()]}
        (self.directory / "dataset.json").write_text(json.dumps(dataset), encoding="utf-8")
        return config

    def invoke(self, args):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return runner.run(args)

    def test_no_live_flag_never_constructs_client_or_writes_results(self):
        with patch.object(runner, "OllamaClient") as factory:
            self.assertEqual(self.invoke(self.args()), 2)
        factory.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_pilot_uses_frozen_dataset_selection_not_first_easy_case(self):
        data = {"cases": [fixture("easy"), fixture("chosen")], "pilot_case_ids": ["chosen"]}
        configurations, cases = runner._select(CONFIG, data, self.args("--pilot"))
        self.assertEqual([case["id"] for case in cases], ["chosen"])
        self.assertEqual([item["id"] for item in configurations], ["A"])

    def test_full_run_saves_auditable_results_without_leaking_references(self):
        config = self.files([fixture("one"), fixture("two")])
        output = self.directory / "results"
        with patch.object(runner, "OllamaClient", Client), patch.object(runner.controller_module, "local_time_context", return_value="FIXED_CLOCK"):
            self.assertEqual(self.invoke(self.args("--live", "--config", str(config), "--output", str(output))), 0)
        rows = [json.loads(line) for line in (output / "raw.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["score"]["status"] for row in rows], ["correct", "correct"])
        self.assertEqual(len(Client.instances), 1)
        client = Client.instances[0]
        self.assertEqual(client.validations, [("fixture:local", "off")])
        self.assertEqual([len(messages) for messages, _ in client.requests], [2, 2])
        for messages, options in client.requests:
            self.assertIn("FIXED_CLOCK", messages[0]["content"])
            self.assertNotIn("REFERENCE_NOT_MODEL_INPUT", str(messages))
            self.assertNotIn("SCORING_NOT_MODEL_INPUT", str(messages))
            self.assertEqual(options["settings"].seed, 42)
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["configurations"]["A"]["correct"], 2)
        self.assertEqual(summary["configurations"]["A"]["requests"], 2)
        self.assertEqual(summary["configurations"]["A"]["generated_tokens_including_thinking"], 4)
        self.assertTrue((output / "report.md").is_file())
        self.assertTrue((output / "dataset.json").is_file())

    def test_approved_tool_runs_only_stub_and_retains_paired_history(self):
        case = fixture("launch", "tool", [{"text": "Please open Notepad.", "expected_tool": {
            "proposal_apps": ["notepad"], "executions": ["notepad"], "pending": None,
            "outcomes": ["launch_attempt_succeeded"]}}])
        row = runner._run_case(Client(), CONFIG["configurations"][0], CONFIG["shared"], case)
        self.assertEqual(row["score"]["status"], "correct")
        self.assertEqual(row["turns"][0]["executions"], ["notepad"])
        self.assertEqual(row["turns"][0]["requests"], 2)
        self.assertEqual([message["role"] for message in row["history"]], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(json.loads(row["history"][2]["content"])["status"], "launch_attempt_succeeded")

    def test_confirmation_is_host_authorization_not_a_second_native_proposal(self):
        case = fixture("confirm", "tool", [
            {"text": "I feel like writing.", "expected_tool": {"proposal_apps": ["notepad"], "executions": [], "pending": "notepad", "outcomes": ["clarification"]}},
            {"text": "Yes, please.", "expected_tool": {"proposal_apps": [], "executions": ["notepad"], "pending": None, "outcomes": ["launch_attempt_succeeded"]}},
        ])
        row = runner._run_case(Client(), CONFIG["configurations"][0], CONFIG["shared"], case)
        self.assertEqual(row["score"]["status"], "correct")
        self.assertEqual(row["turns"][1]["proposals"], [])
        self.assertEqual(row["turns"][1]["executions"], ["notepad"])
        self.assertEqual(row["turns"][1]["requests"], 1)

    def test_observer_preserves_safe_limit_metrics_without_completing_a_call(self):
        completion = ChatEvent(eval_count=512, eval_duration=2_000_000_000, thinking_chunks=10, thinking_characters=200)
        error = OllamaGenerationLimitError(completion)
        class Limited(Client):
            def stream_chat(self, *_args, **_options):
                yield ChatEvent("Partial visible answer")
                raise error
        client = runner.ObservedClient(Limited())
        with self.assertRaises(OllamaGenerationLimitError):
            list(client.stream_chat([], tools=[]))
        record = client.streams[0]
        self.assertFalse(record["complete"])
        self.assertEqual(record["metrics"]["eval_count"], 512)
        self.assertEqual(record["metrics"]["thinking_characters"], 200)
        self.assertEqual(record["failure_kind"], "token_limit")
        self.assertEqual(record["proposals"], [])
        self.assertEqual(record["answer_text"], "Partial visible answer")

    def test_resume_skips_completed_records_and_preserves_frozen_clock(self):
        config = self.files()
        output = self.directory / "results"
        with patch.object(runner, "OllamaClient", Client), patch.object(runner.controller_module, "local_time_context", return_value="ORIGINAL_CLOCK"):
            self.assertEqual(self.invoke(self.args("--live", "--config", str(config), "--output", str(output))), 0)
        raw = (output / "raw.jsonl").read_bytes()
        with patch.object(runner, "OllamaClient") as factory, patch.object(runner.controller_module, "local_time_context", return_value="NEW_CLOCK"):
            self.assertEqual(self.invoke(self.args("--live", "--config", str(config), "--resume", str(output))), 0)
        factory.assert_not_called()
        self.assertEqual((output / "raw.jsonl").read_bytes(), raw)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["frozen_local_clock"], "ORIGINAL_CLOCK")

    def test_resume_rejects_changed_data_before_any_model_request(self):
        config = self.files()
        output = self.directory / "results"
        with patch.object(runner, "OllamaClient", Client):
            self.assertEqual(self.invoke(self.args("--live", "--config", str(config), "--output", str(output))), 0)
        dataset_path = self.directory / "dataset.json"
        data = json.loads(dataset_path.read_text(encoding="utf-8"))
        data["cases"][0]["turns"][0]["text"] = "Changed fixture"
        dataset_path.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(runner, "OllamaClient") as factory, self.assertRaisesRegex(ValueError, "dataset_sha256"):
            self.invoke(self.args("--live", "--config", str(config), "--resume", str(output)))
        factory.assert_not_called()

    def test_interrupted_partial_case_is_audited_and_rerun_on_resume(self):
        class Cancelled(Client):
            def stream_chat(self, *_args, **_options):
                self.chat_request_count += 1
                yield ChatEvent("Partial visible reply")
                raise KeyboardInterrupt
        config = self.files()
        output = self.directory / "results"
        with patch.object(runner, "OllamaClient", Cancelled):
            self.assertEqual(self.invoke(self.args("--live", "--config", str(config), "--output", str(output))), 130)
        self.assertFalse((output / "raw.jsonl").exists())
        attempts = (output / "interrupted_attempts.jsonl").read_bytes()
        self.assertTrue(json.loads(attempts)["interrupted"])
        with patch.object(runner, "OllamaClient", Client):
            self.assertEqual(self.invoke(self.args("--live", "--config", str(config), "--resume", str(output))), 0)
        rows = (output / "raw.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])["score"]["status"], "correct")
        self.assertEqual((output / "interrupted_attempts.jsonl").read_bytes(), attempts)

    def test_summary_keeps_errors_and_review_outcomes_in_denominator(self):
        base = runner._run_case(Client(), CONFIG["configurations"][0], CONFIG["shared"], fixture())
        rows = []
        for index, status in enumerate(("correct", "incorrect", "manual_review", "error")):
            row = deepcopy(base)
            row["case_id"] = str(index)
            row["score"]["status"] = status
            rows.append(row)
        manifest = {"dataset_sha256": "dataset", "config_sha256": "config", "selection": {"configuration_ids": ["A"], "case_ids": ["0", "1", "2", "3"]}}
        summary = runner.summarize(rows, manifest)["configurations"]["A"]
        self.assertEqual(summary["accuracy_all_completed"], .25)
        self.assertEqual(summary["accuracy_adjudicated"], .5)
        self.assertEqual(summary["manual_review"], 1)
        self.assertEqual(summary["error"], 1)

    def test_unexpected_event_rates_use_matching_all_and_tool_turn_denominators(self):
        base = runner._run_case(Client(), CONFIG["configurations"][0], CONFIG["shared"], fixture())
        knowledge, tool = deepcopy(base), deepcopy(base)
        knowledge.update(case_id="knowledge", category="knowledge", turns=knowledge["turns"] * 4)
        knowledge["score"].update(unauthorized_tool_calls=3, unauthorized_executions=2)
        tool.update(case_id="tool", category="tool", turns=tool["turns"] * 2)
        tool["score"].update(unauthorized_tool_calls=1, unauthorized_executions=1, tool_correct=False)
        manifest = {"dataset_sha256": "dataset", "config_sha256": "config", "selection": {"configuration_ids": ["A"], "case_ids": ["knowledge", "tool"]}}
        summary = runner.summarize([knowledge, tool], manifest)["configurations"]["A"]
        self.assertEqual(summary["all_turn_count"], 6)
        self.assertEqual(summary["tool_turn_count"], 2)
        self.assertEqual(summary["unauthorized_tool_calls"], 4)
        self.assertEqual(summary["unauthorized_executions"], 3)
        self.assertEqual(summary["unauthorized_tool_calls_in_tool_cases"], 1)
        self.assertEqual(summary["unauthorized_executions_in_tool_cases"], 1)
        self.assertAlmostEqual(summary["unauthorized_tool_call_rate_per_all_turns"], 4 / 6)
        self.assertEqual(summary["unauthorized_tool_call_rate_per_tool_turn"], .5)
        self.assertEqual(summary["unauthorized_execution_rate_per_all_turns"], .5)
        self.assertEqual(summary["unauthorized_execution_rate_per_tool_turn"], .5)
        self.assertIn("events per turn", summary["unauthorized_rate_unit"])

    def test_unexpected_conversational_events_do_not_create_a_tool_turn_denominator(self):
        row = runner._run_case(Client(), CONFIG["configurations"][0], CONFIG["shared"], fixture())
        row["score"].update(unauthorized_tool_calls=1, unauthorized_executions=1)
        manifest = {"dataset_sha256": "dataset", "config_sha256": "config", "selection": {"configuration_ids": ["A"], "case_ids": [row["case_id"]]}}
        summary = runner.summarize([row], manifest)["configurations"]["A"]
        self.assertEqual(summary["unauthorized_tool_call_rate_per_all_turns"], 1)
        self.assertEqual(summary["unauthorized_execution_rate_per_all_turns"], 1)
        self.assertIsNone(summary["unauthorized_tool_call_rate_per_tool_turn"])
        self.assertIsNone(summary["unauthorized_execution_rate_per_tool_turn"])
        self.assertEqual(summary["tool_turn_count"], 0)
        self.assertEqual(summary["unauthorized_tool_calls_in_tool_cases"], 0)

    def test_reference_audit_updates_points_and_preserves_automatic_points(self):
        row = {"configuration_id": "A", "case_id": "case", "score": {"status": "incorrect", "points": 0, "tool_correct": False}}
        for status, points in (("correct", 1), ("incorrect", 0), ("manual_review", None), ("error", 0)):
            with self.subTest(status=status):
                score = runner._reviewed_score(row, {"A:case": {"status": status, "rationale": "Checked reference.", "reviewer": "Codex"}})
                self.assertEqual(score["points"], points)
                self.assertEqual(score["automatic_points"], 0)
                self.assertEqual(score["automatic_status"], "incorrect")
                self.assertEqual(score["tool_correct"], status == "correct")
        self.assertEqual(row["score"]["status"], "incorrect")

    def test_report_only_is_local_and_preserves_raw_answers(self):
        config = self.files()
        output = self.directory / "results"
        with patch.object(runner, "OllamaClient", Client):
            self.assertEqual(self.invoke(self.args("--live", "--config", str(config), "--output", str(output))), 0)
        raw = (output / "raw.jsonl").read_bytes()
        (output / "reviews.json").write_text(json.dumps({"A:fact": {"status": "incorrect", "rationale": "Explicit audit example.", "reviewer": "Codex"}}), encoding="utf-8")
        with patch.object(runner, "OllamaClient") as factory:
            self.assertEqual(self.invoke(self.args("--report-only", str(output))), 0)
        factory.assert_not_called()
        self.assertEqual((output / "raw.jsonl").read_bytes(), raw)
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["configurations"]["A"]["incorrect"], 1)
        self.assertIn("Reference audit (Codex)", (output / "report.md").read_text(encoding="utf-8"))
        self.assertNotIn("Human review:", (output / "report.md").read_text(encoding="utf-8"))

    def test_unresolved_reference_evidence_does_not_become_a_known_safe_claim(self):
        row = runner._run_case(Client(), CONFIG["configurations"][0], CONFIG["shared"], fixture())
        row["score"].update(unsupported_claim=False, appropriate_uncertainty=False)
        review = {"status": "manual_review", "rationale": "Evidence remains unresolved.",
                  "unsupported_claim": None, "appropriate_uncertainty": None}
        score = runner._reviewed_score(row, {"A:fact": review})
        self.assertIsNone(score["unsupported_claim"])
        self.assertIsNone(score["appropriate_uncertainty"])
        manifest = {"dataset_sha256": "data", "config_sha256": "config",
                    "selection": {"configuration_ids": ["A"], "case_ids": ["fact"]}}
        summary = runner.summarize([row], manifest, reviews={"A:fact": review})["configurations"]["A"]
        self.assertEqual(summary["unsupported_claims_unreviewed"], 1)
        self.assertEqual(summary["correct"], 0)
        self.assertEqual(summary["manual_review"], 1)

    def test_rescore_with_explicit_grading_revision_preserves_collection_artifacts(self):
        case = fixture()
        case["turns"][0]["check"]["value"] = 8
        config = self.files([case])
        output = self.directory / "results"
        with patch.object(runner, "OllamaClient", Client):
            self.assertEqual(self.invoke(self.args("--live", "--config", str(config), "--output", str(output))), 0)
        originals = {name: (output / name).read_bytes() for name in ("raw.jsonl", "summary.json", "report.md", "dataset.json", "manifest.json")}
        grading = json.loads(originals["dataset.json"])
        grading["grading_revision"] = "test-equivalence-fix"
        grading["cases"][0]["turns"][0]["check"]["value"] = 7
        grading_path = self.directory / "grading.json"
        grading_path.write_text(json.dumps(grading), encoding="utf-8")
        with patch.object(runner, "OllamaClient") as factory:
            self.assertEqual(self.invoke(self.args("--report-only", str(output), "--rescore", "--dataset", str(grading_path))), 0)
        factory.assert_not_called()
        for name, original in originals.items():
            self.assertEqual((output / name).read_bytes(), original)
        rescored = json.loads((output / "rescored.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(rescored["original_score"]["status"], "incorrect")
        self.assertEqual(rescored["score"]["status"], "correct")
        rescore_manifest = json.loads((output / "rescored" / "rescore_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(rescore_manifest["source_raw_sha256"], runner._hash(originals["raw.jsonl"]))
        self.assertEqual(rescore_manifest["grading_file_sha256"], runner._hash(grading_path.read_bytes()))
        self.assertTrue(rescore_manifest["scoring_source_sha256"])
        summary = json.loads((output / "rescored" / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["configurations"]["A"]["correct"], 1)

    def test_rescore_rejects_changed_questions_or_references_without_model_calls(self):
        config = self.files()
        output = self.directory / "results"
        with patch.object(runner, "OllamaClient", Client):
            self.assertEqual(self.invoke(self.args("--live", "--config", str(config), "--output", str(output))), 0)
        original = json.loads((output / "dataset.json").read_text(encoding="utf-8"))
        for field in ("question", "reference", "tool"):
            grading = deepcopy(original)
            case = grading["cases"][0]
            if field == "question":
                case["turns"][0]["text"] = "Different question"
            elif field == "reference":
                case["reference"] = "Changed reference fact"
            else:
                case["turns"][0]["expected_tool"] = {"proposal_apps": ["chrome"]}
            grading_path = self.directory / "grading.json"
            grading_path.write_text(json.dumps(grading), encoding="utf-8")
            with self.subTest(field=field), patch.object(runner, "OllamaClient") as factory, self.assertRaisesRegex(ValueError, "questions, references"):
                self.invoke(self.args("--report-only", str(output), "--rescore", "--dataset", str(grading_path)))
            factory.assert_not_called()
        self.assertFalse((output / "rescored.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
