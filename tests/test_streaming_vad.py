"""Incremental VAD state and endpoint timing without models or hardware."""

import unittest
from unittest.mock import Mock, patch

import numpy as np

from aqua.audio.backend import AudioError
from aqua.audio.vad import StreamingVad, UtteranceCollector, UtteranceConfig


class StreamingVadTests(unittest.TestCase):
    def make_vad(self, session):
        manager = patch("faster_whisper.vad.get_vad_model", return_value=Mock(session=session))
        self.factory = manager.start()
        self.addCleanup(manager.stop)
        return StreamingVad()

    def test_recurrent_state_and_64_sample_context_survive_frame_boundaries(self):
        session = Mock()
        session.run.side_effect = [
            (np.array([.8]), np.ones((1, 1, 128), dtype=np.float32), np.full((1, 1, 128), 2, dtype=np.float32)),
            (np.array([.7]), np.full((1, 1, 128), 3, dtype=np.float32), np.full((1, 1, 128), 4, dtype=np.float32)),
        ]
        vad = self.make_vad(session)
        first = np.linspace(0, .5, 512, dtype=np.float32)
        self.assertAlmostEqual(vad.probability(first), .8)
        self.assertAlmostEqual(vad.probability(np.zeros(512, dtype=np.float32)), .7)
        first_input = session.run.call_args_list[0].args[1]
        second_input = session.run.call_args_list[1].args[1]
        self.assertEqual(first_input["input"].shape, (1, 576))
        np.testing.assert_array_equal(first_input["input"][0, :64], np.zeros(64))
        np.testing.assert_array_equal(second_input["input"][0, :64], first[-64:])
        np.testing.assert_array_equal(second_input["h"], np.ones((1, 1, 128)))
        np.testing.assert_array_equal(second_input["c"], np.full((1, 1, 128), 2))
        self.factory.assert_called_once()

    def test_reset_clears_utterance_state_without_reloading_graph(self):
        session = Mock()
        session.run.return_value = (np.array([.9]), np.ones((1, 1, 128)), np.ones((1, 1, 128)))
        vad = self.make_vad(session)
        vad.probability(np.ones(512, dtype=np.float32))
        vad.reset()
        vad.probability(np.zeros(512, dtype=np.float32))
        inputs = session.run.call_args.args[1]
        self.assertFalse(np.any(inputs["h"]))
        self.assertFalse(np.any(inputs["c"]))
        self.assertFalse(np.any(inputs["input"]))
        self.factory.assert_called_once()

    def test_bad_frames_are_rejected_before_inference(self):
        session = Mock()
        vad = self.make_vad(session)
        for frame in (np.zeros(511), np.zeros((1, 512)), np.full(512, np.nan)):
            with self.subTest(shape=frame.shape), self.assertRaises(AudioError):
                vad.probability(frame)
        session.run.assert_not_called()

    def test_invalid_model_probability_and_model_errors_fail_closed(self):
        for output in (float("nan"), 1.5, -.1):
            with self.subTest(output=output):
                session = Mock()
                session.run.return_value = (np.array([output]), None, None)
                vad = self.make_vad(session)
                with self.assertRaises(AudioError):
                    vad.probability(np.zeros(512))
        session.run.side_effect = RuntimeError("inference failed")
        with self.assertRaises(AudioError):
            vad.probability(np.zeros(512))


class UtteranceCollectorTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.full(512, .1, dtype=np.float32)
        self.collector = UtteranceCollector()
        self.collector.begin(np.zeros(8000, dtype=np.float32))

    def push(self, count, probability):
        event = None
        for _ in range(count):
            event = self.collector.push(self.frame, probability)
            if event is not None:
                break
        return event

    def test_immediate_short_command_keeps_preroll_and_trailing_silence(self):
        self.assertIsNone(self.push(5, .9))
        event = self.push(25, 0.0)
        self.assertEqual(event.status, "complete")
        self.assertAlmostEqual(event.speech_seconds, .16)
        self.assertAlmostEqual(event.duration, .5 + .16 + .8)
        self.assertEqual(event.samples.size, 8000 + 30 * 512)
        np.testing.assert_array_equal(event.samples[:8000], np.zeros(8000))
        self.assertEqual(self.collector._parts, [])

    def test_speech_can_begin_after_brief_wake_pause(self):
        self.assertIsNone(self.push(62, 0.0))
        self.assertEqual(self.collector.speech_seconds, 0)
        self.assertIsNone(self.push(8, .9))
        event = self.push(25, 0.0)
        self.assertEqual(event.status, "complete")
        self.assertGreater(event.duration, 3)

    def test_natural_pauses_shorter_than_endpoint_do_not_cut_sentence(self):
        self.assertIsNone(self.push(8, .9))
        self.assertIsNone(self.push(15, 0.0))
        self.assertIsNone(self.push(10, .9))
        event = self.push(25, 0.0)
        self.assertEqual(event.status, "complete")
        self.assertAlmostEqual(event.speech_seconds, 18 * .032)

    def test_wake_preroll_alone_does_not_count_as_command_speech(self):
        self.collector.begin(np.ones(16000, dtype=np.float32))
        self.assertEqual(self.collector.duration, .5)
        self.assertEqual(self.collector.speech_seconds, 0)
        event = self.push(157, 0.0)
        self.assertEqual(event.status, "no_speech")
        self.assertIsNone(event.samples)
        self.assertEqual(event.speech_seconds, 0)
        self.assertEqual(self.collector._parts, [])

    def test_noise_burst_is_not_meaningful_speech_and_keeps_original_timeout(self):
        self.assertIsNone(self.push(1, .9))
        self.assertIsNone(self.push(25, 0.0))
        event = self.push(131, 0.0)
        self.assertEqual(event.status, "no_speech")
        self.assertIsNone(event.samples)

    def test_maximum_recording_rejects_whole_audio_and_releases_buffer(self):
        event = self.push(469, .9)
        self.assertEqual(event.status, "too_long")
        self.assertIsNone(event.samples)
        self.assertLessEqual(event.duration, 15.5 + .032)
        self.assertEqual(self.collector._parts, [])
        with self.assertRaises(ValueError):
            self.collector.push(self.frame, .9)

    def test_hysteresis_preserves_weak_frames_inside_speech(self):
        self.assertIsNone(self.push(1, .8))
        self.assertIsNone(self.push(4, .4))
        event = self.push(25, .1)
        self.assertEqual(event.status, "complete")
        self.assertAlmostEqual(event.speech_seconds, .16)

    def test_begin_clears_old_audio_and_state(self):
        self.push(5, .9)
        self.collector.begin(np.empty(0, dtype=np.float32))
        self.assertEqual(self.collector.duration, 0)
        self.assertEqual(self.collector.speech_seconds, 0)
        event = self.push(157, 0)
        self.assertEqual(event.status, "no_speech")

    def test_invalid_frames_probabilities_and_unarmed_capture_fail_safely(self):
        with self.assertRaises(ValueError):
            UtteranceCollector().push(self.frame, .9)
        for frame, probability in ((np.zeros(511), .9), (np.full(512, np.nan), .9), (self.frame, float("nan")), (self.frame, 2), (self.frame, True)):
            with self.subTest(probability=probability), self.assertRaises(ValueError):
                self.collector.push(frame, probability)

    def test_configuration_values_are_finite_bounded_and_not_boolean(self):
        for options in ({"end_silence": 0}, {"initial_timeout": float("inf")}, {"max_utterance": 1000}, {"pre_roll": -1}, {"speech_threshold": 2}, {"min_speech": True}, {"pre_roll": 2, "max_utterance": 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                UtteranceConfig(**options)


if __name__ == "__main__":
    unittest.main()
