"""Small defaults never download or silently fall back to installed Base."""

from array import array
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aqua.audio.levels import AudioLevels
from aqua.audio.recording import Recording
from aqua.cli import main
from aqua.speech.models import DEFAULT_MODEL_DIR, MODEL_DIRS
from aqua.speech.recognizer import OfflineRecognizer


class FinalizedSpeechDefaultsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = self.root / "faster-whisper-base"
        self.small = self.root / "faster-whisper-small"
        self.base.mkdir()
        for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
            (self.base / name).write_bytes(b"synthetic Base asset")
        self.base_before = {path.name: path.read_bytes() for path in self.base.iterdir()}
        registry = patch.dict(MODEL_DIRS, {"base": self.base, "small": self.small}, clear=True)
        registry.start()
        self.addCleanup(registry.stop)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.recording = Recording(
            array("h", [1024] * 80000).tobytes(), 16000,
            AudioLevels(1 / 32, 1 / 32, 0), 0,
        )

    def invoke(self, arguments):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = main(arguments)
        return status, output.getvalue(), errors.getvalue()

    def assert_base_untouched(self):
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.base.iterdir()}, self.base_before,
        )
        self.assertFalse(self.small.exists())

    @staticmethod
    def backend():
        model = Mock()
        info = SimpleNamespace(language="en", language_probability=0.9, duration_after_vad=1.0)
        model.transcribe.side_effect = lambda *_args, **_kwargs: (
            iter([SimpleNamespace(text="An ordinary local sentence.")]), info,
        )
        return model, Mock(return_value=model)

    def test_recognizer_api_default_uses_small_and_cpu_int8_local_files(self):
        _model, factory = self.backend()
        with patch("aqua.speech.recognizer.validate_model", return_value=self.small) as validate, patch(
            "aqua.speech.recognizer._whisper_model_class", return_value=factory
        ):
            OfflineRecognizer()
        validate.assert_called_once_with(DEFAULT_MODEL_DIR)
        self.assertEqual(DEFAULT_MODEL_DIR.name, "faster-whisper-small")
        factory.assert_called_once_with(
            str(self.small), device="cpu", compute_type="int8", cpu_threads=4,
            num_workers=1, local_files_only=True,
        )

    def test_default_transcribe_missing_small_fails_before_microphone_or_backend(self):
        with patch.dict("sys.modules", {"huggingface_hub": None}), patch(
            "aqua.speech.recognizer._whisper_model_class"
        ) as backend, patch("aqua.cli.record_test") as capture:
            status, output, errors = self.invoke(["transcribe"])
        self.assertEqual(status, 1)
        self.assertIn("Small", errors)
        self.assertIn("--model base", errors)
        self.assertIn("No download was attempted", errors)
        self.assertNotIn("Recording 5 seconds", output)
        self.assertNotIn("Traceback", errors)
        backend.assert_not_called()
        capture.assert_not_called()
        self.assert_base_untouched()

    def test_default_compare_missing_small_fails_without_loading_available_base(self):
        with patch.dict("sys.modules", {"huggingface_hub": None}), patch(
            "aqua.speech.comparison.OfflineRecognizer"
        ) as backend, patch("aqua.cli.record_test") as capture:
            status, output, errors = self.invoke(["compare"])
        self.assertEqual(status, 1)
        self.assertIn("Small", errors)
        self.assertIn("--model base", errors)
        self.assertIn("No download was attempted", errors)
        self.assertNotIn("Recording 5 seconds", output)
        backend.assert_not_called()
        capture.assert_not_called()
        self.assert_base_untouched()

    def test_explicit_base_transcribe_works_without_small_and_keeps_hints_off(self):
        model, factory = self.backend()
        with patch.dict("sys.modules", {"huggingface_hub": None}), patch(
            "aqua.speech.recognizer._whisper_model_class", return_value=factory
        ), patch("aqua.cli.record_test", return_value=self.recording) as capture:
            status, output, errors = self.invoke(["transcribe", "--model", "base"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("An ordinary local sentence.", output)
        self.assertEqual(factory.call_args.args, (str(self.base.resolve()),))
        self.assertEqual(factory.call_args.kwargs["compute_type"], "int8")
        self.assertIsNone(capture.call_args.kwargs["device"])
        self.assertIsNone(capture.call_args.kwargs["sample_rate"])
        self.assertNotIn("hotwords", model.transcribe.call_args.kwargs)
        self.assertNotIn("initial_prompt", model.transcribe.call_args.kwargs)
        self.assert_base_untouched()

    def test_explicit_base_compare_works_without_small_and_captures_only_once(self):
        model, factory = self.backend()
        with patch.dict("sys.modules", {"huggingface_hub": None}), patch(
            "aqua.speech.recognizer._whisper_model_class", return_value=factory
        ), patch("aqua.cli.record_test", return_value=self.recording) as capture:
            status, output, errors = self.invoke(["compare", "--model", "base"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("Base without hints", output)
        self.assertIn("Base with hints", output)
        self.assertNotIn("Small with", output)
        factory.assert_called_once()
        capture.assert_called_once()
        self.assertEqual(model.transcribe.call_count, 2)
        self.assertNotIn("hotwords", model.transcribe.call_args_list[0].kwargs)
        self.assertIn("hotwords", model.transcribe.call_args_list[1].kwargs)
        self.assert_base_untouched()

    def test_comparison_named_model_and_legacy_pair_option_are_exclusive(self):
        with patch("aqua.cli.load_models") as load, patch("aqua.cli.record_test") as capture:
            for name in ("base", "small"):
                with self.subTest(name=name), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as exited:
                        main(["compare", "--model", name, "--include-small"])
                    self.assertEqual(exited.exception.code, 2)
        load.assert_not_called()
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
