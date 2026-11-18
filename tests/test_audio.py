"""Hardware-free checks for levels, device selection, and bounded capture."""

from array import array
import math
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aqua.audio.backend import AudioError, get_backend
from aqua.audio.devices import DeviceInfo, list_devices, resolve_input_device
from aqua.audio.levels import measure_levels
from aqua.audio.recording import RECORD_SECONDS, record_test


def pcm(*samples: int) -> bytes:
    return array("h", samples).tobytes()


class FakePortAudioError(Exception):
    pass


def device_backend(default_input: int = 0) -> SimpleNamespace:
    devices = [
        {"name": "Built-in microphone", "hostapi": 0,
         "max_input_channels": 2, "max_output_channels": 0,
         "default_samplerate": 48000.0},
        {"name": "Speakers", "hostapi": 0,
         "max_input_channels": 0, "max_output_channels": 2,
         "default_samplerate": 48000.0},
        {"name": "USB microphone", "hostapi": 1,
         "max_input_channels": 1, "max_output_channels": 0,
         "default_samplerate": 44100.0},
    ]
    return SimpleNamespace(
        PortAudioError=FakePortAudioError,
        default=SimpleNamespace(device=(default_input, 1)),
        query_devices=Mock(return_value=devices),
        query_hostapis=Mock(return_value=[{"name": "MME"}, {"name": "WASAPI"}]),
    )


class FakeStream:
    """A blocking stream double returning native-endian int16 buffers."""

    def __init__(self, *, samples=None, overflow_reads=(), failure=None):
        self.samples = samples
        self.overflow_reads = set(overflow_reads)
        self.failure = failure
        self.requests = []
        self.position = 0
        self.entered = False
        self.closed = False
        self.start_failure = None
        self.close_failure = None

    def start(self):
        self.entered = True
        if self.start_failure is not None:
            raise self.start_failure

    def close(self):
        self.closed = True
        if self.close_failure is not None:
            raise self.close_failure

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *_args):
        self.closed = True
        return False

    def read(self, frames):
        self.requests.append(frames)
        if self.failure is not None:
            raise self.failure
        if self.samples is None:
            result = array("h", [16384] * frames).tobytes()
        else:
            result = array("h", self.samples[self.position:self.position + frames]).tobytes()
        self.position += frames
        return memoryview(result), len(self.requests) in self.overflow_reads


class LevelsTests(unittest.TestCase):
    def test_empty_and_silent_audio_have_zero_levels_and_negative_infinity_dbfs(self):
        for audio in (b"", pcm(0, 0, 0)):
            with self.subTest(audio=audio):
                result = measure_levels(audio)
                self.assertEqual(result.peak, 0.0)
                self.assertEqual(result.rms, 0.0)
                self.assertEqual(result.peak_dbfs, -math.inf)
                self.assertEqual(result.rms_dbfs, -math.inf)
                self.assertEqual(result.clipped_samples, 0)

    def test_peak_rms_and_dbfs_use_full_scale_normalization(self):
        result = measure_levels(pcm(-16384, 0, 16384, 0))
        self.assertEqual(result.peak, 0.5)
        self.assertAlmostEqual(result.rms, math.sqrt(0.125))
        self.assertAlmostEqual(result.peak_dbfs, 20 * math.log10(0.5))
        self.assertAlmostEqual(result.rms_dbfs, 20 * math.log10(math.sqrt(0.125)))
        self.assertEqual(result.clipped_samples, 0)

    def test_both_int16_endpoints_count_as_clipping_without_overflow(self):
        result = measure_levels(pcm(-32768, 32767, -32767, 32766))
        self.assertEqual(result.peak, 1.0)
        self.assertEqual(result.peak_dbfs, 0.0)
        self.assertEqual(result.clipped_samples, 2)
        self.assertGreater(result.rms, 0.99)

    def test_partial_sample_is_rejected(self):
        with self.assertRaises(ValueError):
            measure_levels(b"\x00")


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.backend = device_backend()
        self.backend_patch = patch("aqua.audio.devices.get_backend", return_value=self.backend)
        self.backend_patch.start()
        self.addCleanup(self.backend_patch.stop)

    def test_input_listing_preserves_ids_and_labels_host_apis_and_default(self):
        devices = list_devices()
        self.assertEqual([device.index for device in devices], [0, 2])
        self.assertEqual([device.host_api for device in devices], ["MME", "WASAPI"])
        self.assertTrue(devices[0].is_default_input)
        self.assertFalse(devices[1].is_default_input)
        self.assertEqual(devices[0].input_channels, 2)
        self.assertEqual(devices[1].default_sample_rate, 44100.0)

    def test_all_devices_includes_outputs(self):
        self.assertEqual([device.index for device in list_devices(include_output=True)], [0, 1, 2])

    def test_default_and_explicit_device_selection(self):
        self.assertEqual(resolve_input_device().index, 0)
        self.assertEqual(resolve_input_device(2).name, "USB microphone")

    def test_missing_default_requires_explicit_selection(self):
        self.backend.default.device = (-1, 1)
        with self.assertRaises(AudioError):
            resolve_input_device()
        self.assertEqual(resolve_input_device(2).index, 2)

    def test_nonexistent_negative_and_output_only_ids_are_rejected(self):
        for index in (99, -1, 1):
            with self.subTest(index=index), self.assertRaises(AudioError):
                resolve_input_device(index)

    def test_no_devices_returns_empty_list_and_selection_is_graceful(self):
        self.backend.query_devices.return_value = []
        self.backend.default.device = (-1, -1)
        self.assertEqual(list_devices(), [])
        with self.assertRaises(AudioError):
            resolve_input_device()

    def test_portaudio_enumeration_error_is_wrapped(self):
        self.backend.query_devices.side_effect = FakePortAudioError("driver unavailable")
        with self.assertRaises(AudioError):
            list_devices()


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.device = DeviceInfo(
            index=2, name="Fake microphone", host_api="Fake API", input_channels=1,
            output_channels=0, default_sample_rate=100.0, is_default_input=True,
        )
        self.stream = FakeStream()
        self.backend = SimpleNamespace(
            PortAudioError=FakePortAudioError,
            check_input_settings=Mock(),
            RawInputStream=Mock(return_value=self.stream),
        )
        backend_patch = patch("aqua.audio.recording.get_backend", return_value=self.backend)
        resolve_patch = patch("aqua.audio.recording.resolve_input_device", return_value=self.device)
        backend_patch.start()
        resolve_patch.start()
        self.addCleanup(backend_patch.stop)
        self.addCleanup(resolve_patch.stop)

    def test_capture_is_exactly_five_seconds_in_memory_and_closes_stream(self):
        with patch("builtins.open", side_effect=AssertionError("capture must not write files")):
            result = record_test(device=2)
        self.assertEqual(RECORD_SECONDS, 5)
        self.assertEqual(result.sample_rate, 100)
        self.assertEqual(result.frames, 500)
        self.assertEqual(result.duration, 5.0)
        self.assertEqual(result.pcm, array("h", [16384] * 500).tobytes())
        self.assertEqual(result.levels.peak, 0.5)
        self.assertEqual(result.levels.rms, 0.5)
        self.assertEqual(result.overflow_count, 0)
        self.assertTrue(self.stream.entered)
        self.assertTrue(self.stream.closed)

    def test_rate_override_and_partial_last_read_do_not_exceed_five_seconds(self):
        self.stream.samples = [0] * 500 + [16384] * 5
        result = record_test(sample_rate=101)
        self.assertEqual(result.sample_rate, 101)
        self.assertEqual(result.frames, 505)
        self.assertEqual(result.duration, 5.0)
        self.assertEqual(sum(self.stream.requests), 505)
        self.assertEqual(self.stream.requests[-1], 5)
        self.assertAlmostEqual(result.levels.rms, 0.5 * math.sqrt(5 / 505))
        self.backend.check_input_settings.assert_called_once_with(
            device=2, channels=1, dtype="int16", samplerate=101,
        )
        self.assertEqual(self.backend.RawInputStream.call_args.kwargs["channels"], 1)
        self.assertEqual(self.backend.RawInputStream.call_args.kwargs["dtype"], "int16")

    def test_progress_is_cumulative_and_reports_final_time_and_levels(self):
        self.stream.samples = [0] * 25 + [16384] * 25
        progress = []
        result = record_test(sample_rate=10, on_progress=lambda elapsed, levels: progress.append((elapsed, levels)))
        self.assertTrue(progress)
        self.assertEqual(progress[-1][0], 5.0)
        self.assertEqual(progress[-1][1], result.levels)
        self.assertEqual(progress[0][1].peak, 0.0)
        self.assertEqual(result.levels.peak, 0.5)
        self.assertAlmostEqual(result.levels.rms, math.sqrt(0.125))
        self.assertEqual([time for time, _ in progress], sorted(time for time, _ in progress))

    def test_overflows_are_reported(self):
        self.stream.overflow_reads = {1, 3}
        self.assertEqual(record_test().overflow_count, 2)

    def test_invalid_rates_never_open_a_stream(self):
        for rate in (0, -16000):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                record_test(sample_rate=rate)
        self.backend.RawInputStream.assert_not_called()

    def test_unsupported_settings_fail_before_capture(self):
        self.backend.check_input_settings.side_effect = FakePortAudioError("unsupported sample rate")
        with self.assertRaises(AudioError):
            record_test(sample_rate=44100)
        self.backend.RawInputStream.assert_not_called()

    def test_stream_open_error_is_wrapped(self):
        self.backend.RawInputStream.side_effect = FakePortAudioError("microphone access denied")
        with self.assertRaises(AudioError):
            record_test()

    def test_start_failure_still_closes_stream_and_is_wrapped(self):
        self.stream.start_failure = FakePortAudioError("failed to start microphone")
        with self.assertRaises(AudioError):
            record_test()
        self.assertTrue(self.stream.closed)

    def test_close_failure_is_wrapped(self):
        self.stream.close_failure = FakePortAudioError("failed to release microphone")
        with self.assertRaises(AudioError):
            record_test()
        self.assertTrue(self.stream.closed)

    def test_read_failure_closes_stream_and_is_wrapped(self):
        self.stream.failure = FakePortAudioError("microphone disconnected")
        with self.assertRaises(AudioError):
            record_test()
        self.assertTrue(self.stream.closed)

    def test_incomplete_buffer_is_rejected_and_closes_stream(self):
        self.stream.read = Mock(return_value=(memoryview(b"\x00"), False))
        with self.assertRaises(AudioError):
            record_test()
        self.assertTrue(self.stream.closed)

    def test_keyboard_interrupt_closes_stream_and_propagates(self):
        self.stream.failure = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            record_test()
        self.assertTrue(self.stream.closed)


class BackendTests(unittest.TestCase):
    def test_missing_dependency_has_actionable_audio_error(self):
        with patch.dict("sys.modules", {"sounddevice": None}):
            with self.assertRaises(AudioError):
                get_backend()

    def test_backend_initialization_failure_becomes_audio_error(self):
        with patch("builtins.__import__", side_effect=FakePortAudioError("PortAudio initialization failed")):
            with self.assertRaises(AudioError):
                get_backend()


if __name__ == "__main__":
    unittest.main()
