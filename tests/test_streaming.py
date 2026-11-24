"""Continuous capture tests require neither microphone nor model/network."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from aqua.audio.backend import AudioError
from aqua.audio.devices import DeviceInfo
from aqua.audio.processing import to_16khz
from aqua.audio.streaming import FRAME_SAMPLES, MicrophoneStream, StreamingResampler


class StreamingResamplerTests(unittest.TestCase):
    def test_failed_diagnostic_is_disabled_without_changing_resampling(self):
        for rate in (16000, 44100):
            with self.subTest(rate=rate):
                source = np.rint(np.sin(2 * np.pi * 1000 * np.arange(rate) / rate) * 2000).astype(np.int16)
                expected = np.concatenate(StreamingResampler(rate).feed(source.tobytes()))

                def broken_observer(samples, _rate):
                    samples[:] = 0
                    raise RuntimeError("private diagnostic content")

                observer = Mock(side_effect=broken_observer)
                resampler = StreamingResampler(rate, on_resampled_audio=observer)
                frames = []
                for offset in range(0, source.size, max(512, rate // 50)):
                    frames.extend(resampler.feed(source[offset:offset + max(512, rate // 50)].tobytes()))
                np.testing.assert_array_equal(np.concatenate(frames), expected)
                observer.assert_called_once()
                self.assertEqual(resampler.diagnostic_errors, ["resampled: RuntimeError"])
                self.assertNotIn("private", str(resampler.diagnostic_errors))

    def test_diagnostic_cancellation_is_not_swallowed(self):
        for failure in (KeyboardInterrupt(), SystemExit()):
            with self.subTest(failure=type(failure).__name__):
                resampler = StreamingResampler(16000, on_resampled_audio=Mock(side_effect=failure))
                with self.assertRaises(type(failure)):
                    resampler.feed(np.ones(512, dtype=np.int16).tobytes())
                self.assertEqual(resampler.diagnostic_errors, [])

    def test_diagnostic_observer_sees_only_new_samples_and_cannot_attenuate_output(self):
        observed = []

        def observe(samples, rate):
            observed.append((samples.size, rate, float(np.max(np.abs(samples)))))
            samples[:] = 0  # A diagnostic receives a copy, not production audio.

        resampler = StreamingResampler(16000, on_resampled_audio=observe)
        self.assertEqual(resampler.feed(np.full(256, 16384, dtype=np.int16).tobytes()), [])
        frames = resampler.feed(np.full(256, 16384, dtype=np.int16).tobytes())
        self.assertEqual(observed, [(256, 16000, .5), (256, 16000, .5)])
        np.testing.assert_array_equal(frames[0], np.full(512, .5, dtype=np.float32))

    def test_quiet_speech_band_signal_is_not_normalized_twice(self):
        rate = 44100
        source = np.rint(np.sin(2 * np.pi * 1000 * np.arange(rate) / rate) * 32768 * 10**(-41.9 / 20)).astype(np.int16)
        native_rms = np.sqrt(np.mean((source.astype(np.float64) / 32768) ** 2))
        actual = np.concatenate(StreamingResampler(rate).feed(source.tobytes()))[512:15552]
        ratio_db = 20 * np.log10(np.sqrt(np.mean(actual.astype(np.float64) ** 2)) / native_rms)
        self.assertLess(abs(ratio_db), .01)
        self.assertGreater(float(np.max(np.abs(actual))), .0075)

    def test_native_16k_preserves_frames_and_bounded_remainder(self):
        source = np.arange(1300, dtype=np.int16)
        resampler = StreamingResampler(16000)
        first = resampler.feed(source[:1000].tobytes())
        second = resampler.feed(source[1000:].tobytes())
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        np.testing.assert_array_equal(np.concatenate(first + second), source[:1024].astype(np.float32) / 32768)
        self.assertLess(resampler._pending.size, FRAME_SAMPLES)

    def test_44100_chunks_match_one_continuous_filtered_resampling(self):
        rate = 44100
        source = (np.sin(2 * np.pi * 1000 * np.arange(rate) / rate) * 20000).astype(np.int16)
        resampler = StreamingResampler(rate)
        frames = []
        for offset in range(0, source.size, 882):
            frames.extend(resampler.feed(source[offset:offset + 882].tobytes()))
            self.assertLess(resampler._pending.size, FRAME_SAMPLES)
        actual = np.concatenate(frames)
        expected = to_16khz(source.tobytes(), rate)
        self.assertGreater(actual.size, 15000)
        self.assertLessEqual(actual.size, 16000)
        np.testing.assert_allclose(actual, expected[:actual.size], atol=2e-6)
        self.assertTrue(all(part.shape == (512,) and part.dtype == np.float32 for part in frames))

    def test_downsampling_attenuates_out_of_band_audio_instead_of_relabeling(self):
        rate = 44100
        source = (np.sin(2 * np.pi * 12000 * np.arange(rate) / rate) * 20000).astype(np.int16)
        frames = StreamingResampler(rate).feed(source.tobytes())
        self.assertLess(float(np.sqrt(np.mean(np.concatenate(frames)[512:] ** 2))), .02)

    def test_invalid_rates_and_incomplete_pcm_fail_before_processing(self):
        for rate in (True, 0, -1, 44100.0, 384001):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                StreamingResampler(rate)
        resampler = StreamingResampler(16000)
        self.assertEqual(resampler.feed(b""), [])
        with self.assertRaises(ValueError):
            resampler.feed(b"\x00")


class MicrophoneStreamTests(unittest.TestCase):
    def setUp(self):
        self.selected = DeviceInfo(7, "Synthetic input", "Mock API", 1, 0, 16000, True)
        self.device_patch = patch("aqua.audio.streaming.resolve_input_device", return_value=self.selected)
        self.resolve = self.device_patch.start()
        self.addCleanup(self.device_patch.stop)
        self.native = Mock(active=True)
        self.backend = SimpleNamespace(check_input_settings=Mock(), RawInputStream=Mock(return_value=self.native))
        self.backend_patch = patch("aqua.audio.streaming.get_backend", return_value=self.backend)
        self.backend_patch.start()
        self.addCleanup(self.backend_patch.stop)

    def feed(self, stream, value=1000, frames=512, status=False):
        stream._callback(np.full(frames, value, dtype=np.int16).tobytes(), frames, None, status)

    def test_single_selected_native_stream_and_complete_frame(self):
        with MicrophoneStream(device=12) as stream:
            self.resolve.assert_called_once_with(12)
            self.backend.RawInputStream.assert_called_once()
            kwargs = self.backend.RawInputStream.call_args.kwargs
            self.assertEqual((kwargs["device"], kwargs["channels"], kwargs["dtype"], kwargs["samplerate"]), (7, 1, "int16", 16000))
            self.assertEqual(kwargs["blocksize"], 320)
            self.feed(stream)
            frame = stream.read_frame(0)
            np.testing.assert_array_equal(frame, np.full(512, 1000 / 32768, dtype=np.float32))
            self.assertEqual(stream.native_sample_rate, 16000)
            self.assertIs(stream.device_info, self.selected)
        self.native.close.assert_called_once()

    def test_native_probe_runs_only_in_consumer_and_preserves_audio(self):
        native, converted = Mock(), Mock()
        with MicrophoneStream(on_native_audio=native, on_resampled_audio=converted) as stream:
            self.feed(stream, value=1000)
            native.assert_not_called()
            converted.assert_not_called()
            output = stream.read_frame(0)
            native.assert_called_once_with(np.full(512, 1000, dtype=np.int16).tobytes(), 16000)
            converted.assert_called_once()
            self.assertEqual(converted.call_args.args[1], 16000)
            np.testing.assert_array_equal(output, np.full(512, 1000 / 32768, dtype=np.float32))
            self.assertIsNone(stream.read_frame(0))
            native.assert_called_once()

    def test_observer_failures_do_not_stop_capture_and_stay_disabled_after_resume(self):
        native = Mock(side_effect=RuntimeError("private native data"))
        resampled = Mock(side_effect=ValueError("private resampled data"))
        with MicrophoneStream(on_native_audio=native, on_resampled_audio=resampled) as stream:
            for value in (1000, 2000):
                self.feed(stream, value=value)
                output = stream.read_frame(0)
                np.testing.assert_array_equal(output, np.full(512, value / 32768, dtype=np.float32))
            self.assertEqual(stream.diagnostic_errors, ["native: RuntimeError", "resampled: ValueError"])
            self.assertNotIn("private", str(stream.diagnostic_errors))
            stream.pause()
            stream.resume()
            self.feed(stream, value=3000)
            np.testing.assert_array_equal(stream.read_frame(0), np.full(512, 3000 / 32768, dtype=np.float32))
            native.assert_called_once()
            resampled.assert_called_once()
            self.assertEqual(len(stream.diagnostic_errors), 2)

    def test_native_diagnostic_cancellation_propagates_and_closes_input(self):
        for failure in (KeyboardInterrupt(), SystemExit()):
            with self.subTest(failure=type(failure).__name__):
                self.native.close.reset_mock()
                with self.assertRaises(type(failure)):
                    with MicrophoneStream(on_native_audio=Mock(side_effect=failure)) as stream:
                        self.feed(stream)
                        stream.read_frame(0)
                self.native.close.assert_called_once()
                self.assertEqual(stream.diagnostic_errors, [])

    def test_actual_resampler_failure_remains_fatal_with_diagnostics_enabled(self):
        with MicrophoneStream(on_native_audio=Mock()) as stream:
            self.feed(stream)
            with patch.object(stream._resampler, "feed", side_effect=ValueError("invalid filter state")):
                with self.assertRaisesRegex(AudioError, "resample"):
                    stream.read_frame(0)
            self.assertEqual(stream.diagnostic_errors, [])

    def test_default_device_is_resolved_without_hardcoded_index(self):
        with MicrophoneStream():
            self.resolve.assert_called_once_with(None)

    def test_pause_drops_audio_and_resume_clears_processing_backlog(self):
        with MicrophoneStream() as stream:
            self.feed(stream, 20000)
            stream.pause()
            self.feed(stream, 20000)
            self.assertIsNone(stream.read_frame(0))
            stream.resume()
            self.feed(stream, 0)
            np.testing.assert_array_equal(stream.read_frame(0), np.zeros(512, dtype=np.float32))
            self.backend.RawInputStream.assert_called_once()

    def test_queue_overflow_drops_blocks_and_reports_a_gap_instead_of_growing(self):
        with MicrophoneStream(queue_blocks=32) as stream:
            for _ in range(33):
                self.feed(stream)
            self.assertEqual(stream._pcm.qsize(), 32)  # Bounded: the 33rd block was dropped.
            self.assertEqual(stream.gaps["queue_full"], 1)
            self.assertEqual(stream.take_gap(), "queue_full")
            self.assertIsNone(stream.take_gap())  # Reported once.
            self.assertIsNotNone(stream.read_frame(0))  # The stream keeps working.
            stream.resume()

    def test_input_overflow_is_a_gap_and_incomplete_audio_is_a_failure(self):
        from aqua.audio.streaming import MicrophoneFailure
        with MicrophoneStream() as stream:
            self.feed(stream, status=True)
            self.assertEqual(stream.gaps["input_overflow"], 1)
            self.assertEqual(stream._pcm.qsize(), 0)  # The late block is dropped, never used.
            self.assertEqual(stream.take_gap(), "input_overflow")
            self.assertIsNone(stream.read_frame(0))
        with MicrophoneStream() as stream:
            stream._callback(b"\x00" * 100, 512, None, False)
            with self.assertRaises(MicrophoneFailure) as caught:
                stream.read_frame(0)
            self.assertEqual(caught.exception.kind, "invalid")

    def test_disconnected_input_returns_explanatory_error(self):
        with MicrophoneStream() as stream:
            self.native.active = False
            with self.assertRaisesRegex(AudioError, "disconnected"):
                stream.read_frame(0)

    def test_empty_poll_returns_none_and_validates_timeout(self):
        with MicrophoneStream() as stream:
            self.assertIsNone(stream.read_frame(0))
            for timeout in (-1, 2, True, float("nan")):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    stream.read_frame(timeout)

    def test_active_but_stalled_driver_fails_by_wall_time_without_waiting(self):
        clock = [0.0]
        with patch("aqua.audio.streaming.monotonic", side_effect=lambda: clock[0]):
            with MicrophoneStream() as stream:
                clock[0] = 2.1
                with self.assertRaisesRegex(AudioError, "no audio for 2 s") as caught:
                    stream.read_frame(0)
                self.assertEqual(caught.exception.kind, "stall")

    def test_resume_resets_stall_deadline_after_long_model_processing(self):
        clock = [0.0]
        with patch("aqua.audio.streaming.monotonic", side_effect=lambda: clock[0]):
            with MicrophoneStream() as stream:
                stream.pause()
                clock[0] = 10.0
                self.assertIsNone(stream.read_frame(0))
                stream.resume()
                self.assertIsNone(stream.read_frame(0))
                clock[0] = 11.0
                self.feed(stream)
                clock[0] = 12.0
                self.assertIsNotNone(stream.read_frame(0))

    def test_start_failure_and_start_interrupt_both_close_input(self):
        for error in (OSError("missing"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.native.close.reset_mock()
                self.native.start.side_effect = error
                expected = KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else AudioError
                with self.assertRaises(expected):
                    with MicrophoneStream():
                        pass
                self.native.close.assert_called_once()

    def test_ctrl_c_inside_capture_closes_input_and_propagates(self):
        with self.assertRaises(KeyboardInterrupt):
            with MicrophoneStream():
                raise KeyboardInterrupt
        self.native.close.assert_called_once()

    def test_no_capture_outside_context(self):
        stream = MicrophoneStream()
        with self.assertRaises(AudioError):
            stream.read_frame()
        with self.assertRaises(AudioError):
            stream.resume()


if __name__ == "__main__":
    unittest.main()
