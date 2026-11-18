"""Deterministic comparison timing with one shared in-memory recording."""

from array import array
from pathlib import Path
import unittest
from unittest.mock import Mock, call, patch

from aqua.audio.levels import AudioLevels
from aqua.audio.recording import Recording
from aqua.speech.comparison import LoadedModel, compare_recording, load_models
from aqua.speech.models import ModelSetupError, SpeechError
from aqua.speech.recognizer import Transcript
from aqua.speech.vocabulary import DEFAULT_HOTWORDS


def transcript(text="A recognized sentence."):
    return Transcript(text, "en", 0.95, 1.5, -12.0, -24.0, 0)


def recording(pcm=None, sample_rate=100):
    if pcm is None:
        pcm = array("h", [1024] * 500).tobytes()
    return Recording(pcm, sample_rate, AudioLevels(0.03125, 0.03125, 0), 0)


class ComparisonLoadingTests(unittest.TestCase):
    def test_all_paths_preflight_before_loading_and_each_model_is_loaded_once(self):
        base, small = Path("local/base"), Path("local/small")
        events = []
        base_recognizer, small_recognizer = Mock(), Mock()

        def validate(path):
            events.append(("validate", path))
            return path

        def load(path):
            events.append(("load", path))
            return {base: base_recognizer, small: small_recognizer}[path]

        with patch("aqua.speech.comparison.validate_model", side_effect=validate), patch(
            "aqua.speech.comparison.OfflineRecognizer", side_effect=load
        ) as factory, patch(
            "aqua.speech.comparison.perf_counter", side_effect=[10.0, 10.4, 20.0, 21.5]
        ):
            loaded = load_models({"base": base, "small": small})

        self.assertEqual(events, [
            ("validate", base), ("validate", small), ("load", base), ("load", small),
        ])
        self.assertEqual(factory.call_args_list, [call(base), call(small)])
        self.assertEqual([model.name for model in loaded], ["base", "small"])
        self.assertIs(loaded[0].recognizer, base_recognizer)
        self.assertIs(loaded[1].recognizer, small_recognizer)
        self.assertAlmostEqual(loaded[0].load_seconds, 0.4)
        self.assertAlmostEqual(loaded[1].load_seconds, 1.5)

    def test_missing_small_stops_before_loading_even_the_available_base(self):
        base, small = Path("local/base"), Path("local/small")

        def validate(path):
            if path == small:
                raise ModelSetupError("Small is not installed; no download was attempted.")
            return path

        with patch("aqua.speech.comparison.validate_model", side_effect=validate) as validator, patch(
            "aqua.speech.comparison.OfflineRecognizer"
        ) as factory, patch("aqua.speech.comparison.perf_counter") as clock:
            with self.assertRaises(ModelSetupError):
                load_models({"base": base, "small": small})

        self.assertEqual(validator.call_args_list, [call(base), call(small)])
        factory.assert_not_called()
        clock.assert_not_called()

    def test_loading_error_propagates_without_reporting_a_loaded_model(self):
        with patch("aqua.speech.comparison.validate_model"), patch(
            "aqua.speech.comparison.OfflineRecognizer", side_effect=SpeechError("Invalid local model")
        ), patch("aqua.speech.comparison.perf_counter", return_value=1.0):
            with self.assertRaisesRegex(SpeechError, "Invalid local model"):
                load_models({"base": Path("local/base")})


class RecordingComparisonTests(unittest.TestCase):
    def setUp(self):
        self.recording = recording()
        self.base = Mock()
        self.small = Mock()
        self.base.transcribe.side_effect = [transcript("Base off."), transcript("Base on.")]
        self.small.transcribe.side_effect = [transcript("Small off."), transcript("Small on.")]
        self.loaded = [
            LoadedModel("base", Path("local/base"), self.base, 5.0),
            LoadedModel("small", Path("local/small"), self.small, 7.0),
        ]

    def test_same_pcm_object_is_reused_for_all_four_model_and_hint_passes(self):
        with patch("aqua.speech.comparison.perf_counter", side_effect=[0, 1, 2, 3, 4, 5, 6, 7]):
            results = compare_recording(self.recording, self.loaded)

        self.assertEqual([(result.model_name, result.hints_enabled) for result in results], [
            ("base", False), ("base", True), ("small", False), ("small", True),
        ])
        self.assertEqual([result.transcript.text for result in results], [
            "Base off.", "Base on.", "Small off.", "Small on.",
        ])
        for model in (self.base, self.small):
            self.assertEqual(model.transcribe.call_count, 2)
            off, on = model.transcribe.call_args_list
            for invocation in (off, on):
                self.assertIs(invocation.args[0], self.recording.pcm)
                self.assertEqual(invocation.args[1], self.recording.sample_rate)
            self.assertEqual(off.kwargs, {"gain_db": 0.0, "language": None})
            self.assertEqual(on.kwargs, {
                "gain_db": 0.0, "language": None, "hotwords": DEFAULT_HOTWORDS,
            })

    def test_transcription_times_and_rtf_exclude_loading_and_use_full_capture_duration(self):
        with patch("aqua.speech.comparison.perf_counter", side_effect=[
            0.0, 0.4, 10.0, 10.6, 20.0, 20.8, 30.0, 31.0,
        ]):
            results = compare_recording(self.recording, self.loaded)

        self.assertEqual(self.recording.duration, 5.0)
        for result, seconds in zip(results, (0.4, 0.6, 0.8, 1.0)):
            self.assertAlmostEqual(result.transcription_seconds, seconds)
            self.assertEqual(result.audio_seconds, 5.0)
            self.assertAlmostEqual(result.real_time_factor, seconds / 5.0)
            self.assertNotEqual(result.audio_seconds, result.transcript.speech_seconds)
        self.assertEqual([model.load_seconds for model in self.loaded], [5.0, 7.0])

    def test_custom_hotwords_language_and_gain_are_identical_except_for_hints(self):
        with patch("aqua.speech.comparison.perf_counter", side_effect=[0, 1, 2, 3]):
            compare_recording(
                self.recording, self.loaded[:1],
                hotwords=" FastAPI,\n CTranslate2 ", language="en", gain_db=-3.0,
            )
        off, on = self.base.transcribe.call_args_list
        self.assertEqual(off.kwargs, {"gain_db": -3.0, "language": "en"})
        self.assertEqual(on.kwargs, {
            "gain_db": -3.0, "language": "en", "hotwords": "FastAPI, CTranslate2",
        })

    def test_later_pass_failure_propagates_instead_of_returning_partial_success(self):
        self.base.transcribe.side_effect = [transcript("First pass."), SpeechError("Decoder failed")]
        with patch("aqua.speech.comparison.perf_counter", side_effect=[0, 1, 2]):
            with self.assertRaisesRegex(SpeechError, "Decoder failed"):
                compare_recording(self.recording, self.loaded)
        self.small.transcribe.assert_not_called()

    def test_empty_or_invalid_capture_is_rejected_before_inference_or_timing(self):
        cases = (recording(pcm=b""), recording(pcm=b"\x00\x00", sample_rate=0))
        with patch("aqua.speech.comparison.perf_counter") as clock:
            for source in cases:
                with self.subTest(sample_rate=source.sample_rate, frames=source.frames):
                    with self.assertRaisesRegex(ValueError, "nonempty recording"):
                        compare_recording(source, self.loaded)
        self.base.transcribe.assert_not_called()
        self.small.transcribe.assert_not_called()
        clock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
