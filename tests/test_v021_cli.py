"""V0.2.1 CLI checks with synthetic capture and mocked local recognizers."""

from array import array
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from aqua.audio.levels import AudioLevels
from aqua.audio.recording import Recording
from aqua.cli import main
from aqua.speech.comparison import ComparisonPass, LoadedModel
from aqua.speech.models import DEFAULT_MODEL_DIR, MODEL_DIRS, ModelSetupError
from aqua.speech.recognizer import Transcript
from aqua.speech.vocabulary import DEFAULT_HOTWORDS


class V021CliTests(unittest.TestCase):
    def setUp(self):
        # These tests cover Whisper; the CLI default recogniser is Parakeet.
        _asr = patch("aqua.cli.DEFAULT_ASR", "whisper")
        _asr.start()
        self.addCleanup(_asr.stop)
        self.source = Recording(
            array("h", [1024] * 5000).tobytes(), 1000,
            AudioLevels(1 / 32, 1 / 32, 0), 0,
        )

    @staticmethod
    def transcript(text="Aqua uses Python."):
        return Transcript(text, "en", 0.95, 2.5, -18.0, -24.0, 0)

    def invoke(self, arguments):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = main(arguments)
        return status, output.getvalue(), errors.getvalue()

    def model(self, name="small", load_seconds=0.125):
        return LoadedModel(name, MODEL_DIRS[name], Mock(), load_seconds)

    def comparison_passes(self, name="small"):
        return [
            ComparisonPass(name, False, self.transcript("Hello, Ako."), 0.4, 5.0),
            ComparisonPass(name, True, self.transcript("Hello, Aqua."), 0.6, 5.0),
        ]

    def test_transcribe_defaults_to_small_and_allows_explicit_base_with_default_microphone(self):
        for options, expected in (([], DEFAULT_MODEL_DIR), (["--model", "base"], MODEL_DIRS["base"])):
            with self.subTest(options=options):
                with patch("aqua.cli.OfflineRecognizer") as constructor, patch(
                    "aqua.cli.record_test", return_value=self.source
                ) as record:
                    constructor.return_value.transcribe.return_value = self.transcript()
                    status, _output, errors = self.invoke(["transcribe", *options])
                self.assertEqual(status, 0)
                self.assertEqual(errors, "")
                constructor.assert_called_once_with(expected)
                self.assertIsNone(record.call_args.kwargs["device"])
                constructor.return_value.transcribe.assert_called_once_with(
                    self.source.pcm, 1000, gain_db=0.0, language="en",
                )

    def test_transcribe_small_selects_its_registered_local_directory(self):
        with patch("aqua.cli.OfflineRecognizer") as constructor, patch(
            "aqua.cli.record_test", return_value=self.source
        ):
            constructor.return_value.transcribe.return_value = self.transcript()
            status, output, errors = self.invoke(["transcribe", "--model", "small"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        constructor.assert_called_once_with(MODEL_DIRS["small"])
        self.assertIn(str(MODEL_DIRS["small"]), output)

    def test_missing_small_transcribe_does_not_open_the_microphone(self):
        with patch("aqua.cli.OfflineRecognizer", side_effect=ModelSetupError("Small is not installed")), patch(
            "aqua.cli.record_test"
        ) as record:
            status, output, errors = self.invoke(["transcribe", "--model", "small"])
        self.assertEqual(status, 1)
        record.assert_not_called()
        self.assertIn("Small is not installed", errors)
        self.assertNotIn("Recording 5 seconds", output)

    def test_explicit_model_directory_remains_available(self):
        directory = Path("D:/Aqua/models/custom-local")
        with patch("aqua.cli.OfflineRecognizer") as constructor, patch(
            "aqua.cli.record_test", return_value=self.source
        ):
            constructor.return_value.transcribe.return_value = self.transcript()
            status, _output, _errors = self.invoke([
                "transcribe", "--model-dir", str(directory),
            ])
        self.assertEqual(status, 0)
        constructor.assert_called_once_with(directory)

    def test_named_model_and_explicit_directory_cannot_be_combined(self):
        with patch("aqua.cli.OfflineRecognizer") as constructor, patch("aqua.cli.record_test") as record:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as exited:
                    main(["transcribe", "--model", "base", "--model-dir", "custom-local"])
        self.assertEqual(exited.exception.code, 2)
        constructor.assert_not_called()
        record.assert_not_called()

    def test_enabled_hints_pass_default_vocabulary_without_changing_text(self):
        unrelated = "The blue bicycle is beside the tree."
        with patch("aqua.cli.OfflineRecognizer") as constructor, patch(
            "aqua.cli.record_test", return_value=self.source
        ):
            constructor.return_value.transcribe.return_value = self.transcript(unrelated)
            status, output, errors = self.invoke(["transcribe", "--hints"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        constructor.return_value.transcribe.assert_called_once_with(
            self.source.pcm, 1000, gain_db=0.0, language="en", hotwords=DEFAULT_HOTWORDS,
        )
        self.assertIn(f"Recognized text: {unrelated}", output)

    def test_explicit_no_hints_omits_all_hint_arguments(self):
        with patch("aqua.cli.OfflineRecognizer") as constructor, patch(
            "aqua.cli.record_test", return_value=self.source
        ):
            constructor.return_value.transcribe.return_value = self.transcript()
            status, _output, _errors = self.invoke(["transcribe", "--no-hints"])
        self.assertEqual(status, 0)
        constructor.return_value.transcribe.assert_called_once_with(
            self.source.pcm, 1000, gain_db=0.0, language="en",
        )

    def test_custom_vocabulary_is_normalized_when_hints_are_enabled(self):
        with patch("aqua.cli.OfflineRecognizer") as constructor, patch(
            "aqua.cli.record_test", return_value=self.source
        ):
            constructor.return_value.transcribe.return_value = self.transcript()
            status, _output, errors = self.invoke([
                "transcribe", "--hints", "--hotwords", "  Aqua,\n  FastAPI,\t CTranslate2  ",
            ])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(
            constructor.return_value.transcribe.call_args.kwargs["hotwords"],
            "Aqua, FastAPI, CTranslate2",
        )

    def test_invalid_hint_options_fail_before_model_load_or_capture(self):
        invalid = [
            ["--hotwords", "Aqua"],
            ["--no-hints", "--hotwords", "Aqua"],
            ["--hints", "--no-hints"],
            ["--hints", "--hotwords", " \t\n "],
        ]
        with patch("aqua.cli.OfflineRecognizer") as constructor, patch("aqua.cli.record_test") as record:
            for options in invalid:
                with self.subTest(options=options):
                    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                        with self.assertRaises(SystemExit) as exited:
                            main(["transcribe", *options])
                    self.assertEqual(exited.exception.code, 2)
        constructor.assert_not_called()
        record.assert_not_called()

    def test_compare_captures_once_and_reports_both_transcripts_and_separate_timings(self):
        models = [self.model()]
        with patch("aqua.cli.load_models", return_value=models) as load, patch(
            "aqua.cli.record_test", return_value=self.source
        ) as record, patch(
            "aqua.cli.compare_recording", return_value=self.comparison_passes()
        ) as compare:
            status, output, errors = self.invoke(["compare"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        load.assert_called_once_with({"small": MODEL_DIRS["small"]})
        record.assert_called_once()
        self.assertIsNone(record.call_args.kwargs["device"])
        self.assertIsNone(record.call_args.kwargs["sample_rate"])
        self.assertIs(compare.call_args.args[0], self.source)
        self.assertIs(compare.call_args.args[1], models)
        self.assertEqual(compare.call_args.kwargs["hotwords"], DEFAULT_HOTWORDS)
        self.assertEqual(compare.call_args.kwargs["gain_db"], 0.0)
        self.assertEqual(compare.call_args.kwargs["language"], "en")
        self.assertIn("Small without hints", output)
        self.assertIn("Hello, Ako.", output)
        self.assertIn("Small with hints", output)
        self.assertIn("Hello, Aqua.", output)
        self.assertIn("loading 0.125s", output)
        self.assertIn("Transcription: 0.400s", output)
        self.assertIn("Transcription: 0.600s", output)
        self.assertIn("Captured audio: 5.000s", output)
        self.assertIn("RTF: 0.080", output)
        self.assertIn("RTF: 0.120", output)
        self.assertIn("same in-memory capture", output)
        self.assertIn("no recording was saved", output)

    def test_optional_small_is_prepared_once_and_included_in_comparison(self):
        models = [self.model("base"), self.model("small", 0.5)]
        with patch("aqua.cli.load_models", return_value=models) as load, patch(
            "aqua.cli.record_test", return_value=self.source
        ) as record, patch(
            "aqua.cli.compare_recording",
            return_value=self.comparison_passes("base") + self.comparison_passes("small"),
        ) as compare:
            status, output, errors = self.invoke(["compare", "--include-small"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        load.assert_called_once_with({"base": MODEL_DIRS["base"], "small": MODEL_DIRS["small"]})
        record.assert_called_once()
        self.assertIs(compare.call_args.args[1], models)
        self.assertIn("Small without hints", output)
        self.assertIn("Small with hints", output)

    def test_missing_optional_small_fails_before_capture_or_hub_import(self):
        with patch.dict("sys.modules", {"huggingface_hub": None}), patch(
            "aqua.cli.load_models", side_effect=ModelSetupError("Small is not installed")
        ) as load, patch("aqua.cli.record_test") as record, patch(
            "aqua.cli.compare_recording"
        ) as compare:
            status, output, errors = self.invoke(["compare", "--include-small"])
        self.assertEqual(status, 1)
        load.assert_called_once_with({"base": MODEL_DIRS["base"], "small": MODEL_DIRS["small"]})
        record.assert_not_called()
        compare.assert_not_called()
        self.assertIn("Small is not installed", errors)
        self.assertNotIn("Recording 5 seconds", output)
        self.assertNotIn("Traceback", errors)

    def test_compare_forwards_manual_microphone_and_custom_vocabulary_without_auto_gain(self):
        models = [self.model()]
        with patch("aqua.cli.load_models", return_value=models), patch(
            "aqua.cli.record_test", return_value=self.source
        ) as record, patch(
            "aqua.cli.compare_recording", return_value=self.comparison_passes()
        ) as compare:
            status, _output, errors = self.invoke([
                "compare", "--device", "7", "--sample-rate", "48000", "--language", "en",
                "--hotwords", "  Aqua,   FastAPI  ",
            ])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(record.call_args.kwargs["device"], 7)
        self.assertEqual(record.call_args.kwargs["sample_rate"], 48000)
        self.assertEqual(compare.call_args.kwargs["hotwords"], "Aqua, FastAPI")
        self.assertEqual(compare.call_args.kwargs["language"], "en")
        self.assertEqual(compare.call_args.kwargs["gain_db"], 0.0)

    def test_blank_comparison_hotwords_reject_before_models_or_capture(self):
        with patch("aqua.cli.load_models") as load, patch("aqua.cli.record_test") as record:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as exited:
                    main(["compare", "--hotwords", "   "])
        self.assertEqual(exited.exception.code, 2)
        load.assert_not_called()
        record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
