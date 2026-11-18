"""Exercise offline recognition without a model, microphone, or network."""

import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from aqua.speech.models import MODEL_FILES, ModelSetupError, SpeechError
from aqua.speech.recognizer import OfflineRecognizer, _whisper_model_class


class RecognizerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "base"
        self.path.mkdir()
        for name in MODEL_FILES:
            (self.path / name).write_bytes(b"synthetic local model asset")
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.info = SimpleNamespace(
            language="en", language_probability=0.98, duration_after_vad=1.75
        )
        self.model = Mock()
        self.model.transcribe.return_value = (
            iter([SimpleNamespace(text=" Recognized sentence. ")]), self.info
        )
        self.model_class = Mock(return_value=self.model)
        factory = patch(
            "aqua.speech.recognizer._whisper_model_class", return_value=self.model_class
        )
        self.factory = factory.start()
        self.addCleanup(factory.stop)

    def recognizer(self):
        return OfflineRecognizer(self.path)

    @staticmethod
    def tone(sample_rate=16000, seconds=0.1):
        times = np.arange(round(sample_rate * seconds)) / sample_rate
        return (np.sin(times * 2 * np.pi * 440) * 8192).astype(np.int16).tobytes()

    def test_model_load_uses_validated_local_path_cpu_int8_and_no_downloads(self):
        self.recognizer()
        self.model_class.assert_called_once_with(
            str(self.path.resolve()), device="cpu", compute_type="int8",
            cpu_threads=4, num_workers=1, local_files_only=True,
        )
        self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
        self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")
        self.assertEqual(os.environ["HF_HUB_DISABLE_TELEMETRY"], "1")

    def test_missing_model_fails_before_import_or_model_loading(self):
        with self.assertRaises(ModelSetupError) as error:
            OfflineRecognizer(self.path / "missing")
        self.factory.assert_not_called()
        self.model_class.assert_not_called()
        self.assertIn("setup-model --download", str(error.exception))
        self.assertFalse((self.path / "missing").exists())

    def test_missing_tokenizer_never_triggers_a_remote_tokenizer_fallback(self):
        (self.path / "tokenizer.json").unlink()
        with self.assertRaises(ModelSetupError):
            self.recognizer()
        self.factory.assert_not_called()
        self.model_class.assert_not_called()

    def test_dependency_import_failure_is_an_actionable_speech_error(self):
        self.factory.side_effect = ImportError("test missing dependency")
        with self.assertRaises(SpeechError) as error:
            self.recognizer()
        message = str(error.exception)
        self.assertIn("dependencies", message)
        self.assertIn("no download was attempted", message)
        self.assertIn("test missing dependency", message)

    def test_backend_loader_disables_onnx_telemetry_before_sessions_are_used(self):
        telemetry = Mock()
        expected_class = object()
        with patch.dict("sys.modules", {
            "onnxruntime": SimpleNamespace(disable_telemetry_events=telemetry),
            "faster_whisper": SimpleNamespace(WhisperModel=expected_class),
        }):
            self.assertIs(_whisper_model_class(), expected_class)
        telemetry.assert_called_once_with()

    def test_invalid_local_model_error_does_not_attempt_download(self):
        self.model_class.side_effect = RuntimeError("test corrupt weights")
        with self.assertRaises(SpeechError) as error:
            self.recognizer()
        self.assertIn("local model files", str(error.exception))
        self.assertIn("no download was attempted", str(error.exception))

    def test_native_44100_audio_is_resampled_to_five_seconds_at_16000(self):
        recognizer = self.recognizer()
        result = recognizer.transcribe(self.tone(44100, 5), 44100)
        audio = self.model.transcribe.call_args.args[0]
        self.assertEqual(audio.shape, (80000,))
        self.assertEqual(audio.dtype, np.float32)
        self.assertTrue(audio.flags.c_contiguous)
        self.assertAlmostEqual(float(np.max(np.abs(audio))), 0.25, delta=0.002)
        self.assertEqual(result.text, "Recognized sentence.")
        self.assertEqual(result.language, "en")
        self.assertEqual(result.language_probability, 0.98)
        self.assertEqual(result.speech_seconds, 1.75)

    def test_language_and_bundled_vad_are_passed_explicitly(self):
        recognizer = self.recognizer()
        recognizer.transcribe(self.tone(), 16000, language="hi")
        options = self.model.transcribe.call_args.kwargs
        self.assertEqual(options["language"], "hi")
        self.assertEqual(options["task"], "transcribe")
        self.assertTrue(options["vad_filter"])
        self.assertEqual(options["temperature"], 0.0)
        self.assertFalse(options["condition_on_previous_text"])
        self.assertEqual(options["vad_parameters"]["min_silence_duration_ms"], 300)

    def test_optional_gain_reports_saturation_without_mutating_pcm(self):
        source = np.array([16384, -16384] * 100, dtype=np.int16).tobytes()
        original = bytes(source)
        result = self.recognizer().transcribe(source, 16000, gain_db=20)
        audio = self.model.transcribe.call_args.args[0]
        self.assertEqual(source, original)
        self.assertTrue(np.all(np.abs(audio) == 1))
        self.assertEqual(result.saturated_samples, 200)
        self.assertEqual(result.input_peak_dbfs, 0.0)
        self.assertEqual(result.input_rms_dbfs, 0.0)

    def test_segments_are_fully_consumed_and_blank_segments_ignored(self):
        visited = []

        def segments():
            for number, text in enumerate([" First ", "  ", " sentence. "]):
                visited.append(number)
                yield SimpleNamespace(text=text)

        self.model.transcribe.return_value = (segments(), self.info)
        result = self.recognizer().transcribe(self.tone(), 16000)
        self.assertEqual(result.text, "First sentence.")
        self.assertEqual(visited, [0, 1, 2])

    def test_iteration_failure_is_wrapped_instead_of_returning_partial_success(self):
        def failing_segments():
            yield SimpleNamespace(text="Partial")
            raise RuntimeError("test decoder failed during iteration")

        self.model.transcribe.return_value = (failing_segments(), self.info)
        with self.assertRaises(SpeechError) as error:
            self.recognizer().transcribe(self.tone(), 16000)
        self.assertIn("during iteration", str(error.exception))

    def test_silent_or_empty_input_never_calls_inference(self):
        recognizer = self.recognizer()
        for pcm in (b"", b"\x00\x00" * 4410):
            with self.subTest(samples=len(pcm) // 2):
                result = recognizer.transcribe(pcm, 44100, language="hi")
                self.assertEqual(result.text, "")
                self.assertEqual(result.language, "hi")
                self.assertEqual(result.speech_seconds, 0.0)
                self.assertEqual(result.input_peak_dbfs, -math.inf)
                self.assertEqual(result.input_rms_dbfs, -math.inf)
        self.model.transcribe.assert_not_called()

    def test_preprocessing_dependency_errors_are_friendly(self):
        with patch("aqua.speech.recognizer.to_16khz", side_effect=ImportError("test PyAV unavailable")):
            with self.assertRaises(SpeechError) as error:
                self.recognizer().transcribe(self.tone(44100), 44100)
        self.assertIn("Offline speech recognition failed", str(error.exception))
        self.model.transcribe.assert_not_called()

    def test_cancel_during_model_loading_propagates(self):
        self.factory.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.recognizer()

    def test_cancel_during_inference_propagates(self):
        self.model.transcribe.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.recognizer().transcribe(self.tone(), 16000)


if __name__ == "__main__":
    unittest.main()
