"""Voice-chat confidence gates use synthetic audio and segment metadata."""

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from aqua.audio.levels import AudioLevels
from aqua.audio.recording import Recording
from aqua.speech.recognizer import OfflineRecognizer, Transcript
from aqua.speech.reliability import transcript_rejection_reason


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.recording = Recording(b"\x00\x04" * 80000, 16000, AudioLevels(.03, .03, 0), 0)
        self.transcript = Transcript("Hello Aqua.", "en", .95, 1.0, -30, -40, 0)

    def test_usable_speech_passes_without_optional_metrics(self):
        self.assertIsNone(transcript_rejection_reason(self.recording, self.transcript))

    def test_quiet_capture_is_not_rejected_for_volume_alone(self):
        quiet = replace(self.recording, levels=AudioLevels(.001, .0001, 0))
        self.assertIsNone(transcript_rejection_reason(quiet, self.transcript))

    def test_empty_punctuation_and_fragments_are_not_sent(self):
        for text in ("", "  ", "...", "a"):
            with self.subTest(text=text):
                self.assertIsNotNone(transcript_rejection_reason(self.recording, replace(self.transcript, text=text)))

    def test_overflow_no_signal_and_invalid_capture_are_rejected(self):
        cases = [replace(self.recording, overflow_count=1), replace(self.recording, pcm=b""),
                 replace(self.recording, sample_rate=0), replace(self.recording, levels=AudioLevels(0, 0, 0))]
        for recording in cases:
            with self.subTest(recording_rate=recording.sample_rate, frames=recording.frames):
                self.assertIsNotNone(transcript_rejection_reason(recording, self.transcript))

    def test_short_or_invalid_vad_is_rejected(self):
        for duration in (0, .29, float("nan"), float("inf")):
            with self.subTest(duration=duration):
                self.assertIsNotNone(transcript_rejection_reason(self.recording, replace(self.transcript, speech_seconds=duration)))

    def test_excessive_recognition_input_clipping_is_rejected(self):
        result = replace(self.transcript, saturated_samples=801)
        self.assertIn("clipped", transcript_rejection_reason(self.recording, result))

    def test_low_logprob_high_no_speech_and_repetition_are_rejected(self):
        for changes in ({"avg_logprob": -1.1}, {"no_speech_probability": .61}, {"compression_ratio": 2.5},
                        {"avg_logprob": float("nan")}, {"no_speech_probability": float("inf")}):
            with self.subTest(changes=changes):
                self.assertIsNotNone(transcript_rejection_reason(self.recording, replace(self.transcript, **changes)))

    def test_reasonable_metrics_and_non_english_text_pass(self):
        result = replace(self.transcript, text="नमस्ते", avg_logprob=-.5, no_speech_probability=.1, compression_ratio=1.2)
        self.assertIsNone(transcript_rejection_reason(self.recording, result))

    def test_recognizer_aggregates_real_segment_confidence_without_new_dependencies(self):
        recognizer = object.__new__(OfflineRecognizer)
        recognizer._model = Mock()
        segments = [SimpleNamespace(text="Hello", avg_logprob=-.3, no_speech_prob=.1, compression_ratio=1.0),
                    SimpleNamespace(text="Aqua.", avg_logprob=-.7, no_speech_prob=.2, compression_ratio=1.5)]
        info = SimpleNamespace(language="en", language_probability=.95, duration_after_vad=1.0)
        recognizer._model.transcribe.return_value = (iter(segments), info)
        result = recognizer.transcribe(self.recording.pcm, 16000)
        self.assertEqual(result.text, "Hello Aqua.")
        self.assertAlmostEqual(result.avg_logprob, -.5)
        self.assertEqual(result.no_speech_probability, .2)
        self.assertEqual(result.compression_ratio, 1.5)


if __name__ == "__main__":
    unittest.main()
