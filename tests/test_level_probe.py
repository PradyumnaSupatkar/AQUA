"""Scalar-only microphone diagnostics use synthetic/mocked audio offline."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from aqua.audio import level_probe as probe
from aqua.audio.backend import AudioError


class SignalSummaryTests(unittest.TestCase):
    def test_summary_uses_weighted_energy_dc_and_twenty_ms_windows(self):
        stats = probe.SignalStats(1000)
        stats.update(np.zeros(20))
        stats.update(np.full(20, .5))
        self.assertEqual(stats.count, 40)
        self.assertAlmostEqual(stats.peak, .5)
        self.assertAlmostEqual(stats.rms, np.sqrt(.125))
        self.assertAlmostEqual(stats.dc, .25)
        self.assertAlmostEqual(stats.p90_rms, .45)
        self.assertEqual(stats._pending_count, 0)

    def test_pcm_counts_scaling_clipping_and_window_split_are_exact(self):
        stats = probe.StageStats(1000, 1, .002)
        stats.update(np.array([0, -32768, 32767, 16384], dtype=np.int16), raw_pcm=True)
        self.assertEqual(stats.raw_peak_count, 32768)
        self.assertEqual(stats.all.clipped, 2)
        self.assertEqual(stats.quiet.clipped, 1)
        self.assertEqual(stats.speech.clipped, 1)
        self.assertEqual((stats.quiet.count, stats.speech.count), (2, 2))
        self.assertAlmostEqual(stats.all.rms, np.sqrt((1 + (32767 / 32768) ** 2 + .25) / 4))

    def test_stage_storage_is_bounded_by_diagnostic_duration(self):
        stats = probe.StageStats(1000, 1, .2)
        for _ in range(100):
            stats.update(np.ones(100))
        self.assertEqual(stats.all.count, 1000)
        self.assertEqual(len(stats.all._window_rms), 50)
        self.assertEqual((stats.quiet.count, stats.speech.count), (200, 800))

    def test_invalid_signal_and_empty_audio(self):
        stats = probe.SignalStats(16000)
        stats.update([])
        self.assertEqual((stats.peak, stats.rms, stats.dc, stats.p90_rms), (0, 0, 0, 0))
        for bad in ([np.nan], [np.inf], [[0.0]]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                stats.update(bad)

    @staticmethod
    def stage(quiet_amplitude=.001, speech_amplitude=.01):
        stage = probe.StageStats(16000, 2, 1)
        tone = np.sin(2 * np.pi * 1000 * np.arange(16000) / 16000)
        stage.update(tone * quiet_amplitude)
        stage.update(tone * speech_amplitude)
        return stage

    def test_gain_candidate_is_capped_and_report_only(self):
        stage = self.stage()
        before = (stage.all.energy, stage.all.peak, stage.speech.rms)
        advice = probe.gain_advice(stage)
        self.assertIn("+12.0 dB (not applied)", advice)
        self.assertIn("predicted peak -28.0 dBFS", advice)
        self.assertEqual((stage.all.energy, stage.all.peak, stage.speech.rms), before)

    def test_gain_rejected_for_noise_digital_silence_clipping_and_dc(self):
        cases = [self.stage(.005, .006), self.stage(0, .01)]
        clipped = self.stage()
        clipped.speech.clipped = 1
        cases.append(clipped)
        dc = self.stage()
        dc.speech.total = .01 * dc.speech.count
        cases.append(dc)
        for stage in cases:
            with self.subTest(stage=stage):
                self.assertIn("refused:", probe.gain_advice(stage))

    def test_gain_never_exceeds_peak_headroom_or_target(self):
        advice = probe.gain_advice(self.stage(.001, .6))
        self.assertIn("no positive boost", advice)


class FakeMicrophone:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.device_info = SimpleNamespace(index=7, name="Synthetic mono", host_api="Mock API")
        self.native_sample_rate = 16000
        self.position = 0
        self.closed = False
        self.last_frame = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def read_frame(self, timeout):
        positions = np.arange(self.position, self.position + 512)
        amplitudes = np.where(positions < 4000, .001, .01)
        pcm = np.rint(np.sin(2 * np.pi * 1000 * positions / 16000) * amplitudes * 32768).astype(np.int16)
        frame = pcm.astype(np.float32) / 32768
        self.kwargs["on_native_audio"](pcm.tobytes(), 16000)
        self.kwargs["on_resampled_audio"](frame.copy(), 16000)
        self.position += 512
        self.last_frame = frame.copy()
        return frame


class LevelProbeTests(unittest.TestCase):
    def setUp(self):
        metadata = patch.object(probe, "inspect_capture_endpoints", return_value={"available": False, "defaults": [], "devices": [], "errors": []})
        self.metadata = metadata.start()
        self.addCleanup(metadata.stop)
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Signal diagnostics must not use network/models/apps."))
            manager.start()
            self.addCleanup(manager.stop)

    def capture(self, function, **kwargs):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = function(**kwargs)
        return status, output.getvalue(), errors.getvalue()

    def test_default_measures_every_stage_without_saving_or_changing_gain(self):
        streams = []

        def factory(**kwargs):
            stream = FakeMicrophone(**kwargs)
            streams.append(stream)
            return stream

        with patch.object(probe, "MicrophoneStream", side_effect=factory) as constructor:
            status, output, errors = self.capture(probe.run_level_probe, seconds=1, quiet_seconds=.25)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(len(streams), 1)
        self.assertTrue(streams[0].closed)
        self.assertIn("on_native_audio", constructor.call_args.kwargs)
        self.assertIn("on_resampled_audio", constructor.call_args.kwargs)
        for label in ("1 Native mono PCM", "2 Native normalized float reference", "3 Resampled float BEFORE", "4 Actual wake-input float AFTER", "5 Simulated Whisper int16"):
            self.assertIn(label, output)
        self.assertIn("Native-to-wake aggregate RMS change: +0.000 dB", output)
        self.assertIn("Speak normally now", output)
        self.assertIn("not applied", output)
        self.assertIn("no models or actions run", output)
        self.assertNotIn("nan", output)

    def test_bad_arguments_do_not_open_microphone(self):
        invalid = ({"seconds": 0}, {"seconds": float("nan")}, {"quiet_seconds": 8}, {"quiet_seconds": -1}, {"sample_rate": True})
        with patch.object(probe, "MicrophoneStream") as stream:
            for options in invalid:
                with self.subTest(options=options):
                    status, _, errors = self.capture(probe.run_level_probe, **options)
                    self.assertEqual(status, 1)
                    self.assertIn("diagnostic error", errors)
            stream.assert_not_called()

    def test_input_error_and_interrupt_are_graceful(self):
        for failure, expected in ((AudioError("disconnected"), 1), (KeyboardInterrupt(), 130)):
            with self.subTest(failure=type(failure).__name__), patch.object(probe, "MicrophoneStream", side_effect=failure):
                status, _, errors = self.capture(probe.run_level_probe)
            self.assertEqual(status, expected)
            self.assertTrue(errors)

    def test_optional_stereo_reports_cancellation_without_using_production_mix(self):
        selected = SimpleNamespace(index=7, name="Synthetic array", host_api="Mock API", input_channels=2, default_sample_rate=16000)
        waveform = (np.sin(2 * np.pi * 1000 * np.arange(1600) / 16000) * 1000).astype(np.int16)
        interleaved = np.column_stack((waveform, -waveform)).astype(np.int16).tobytes()
        stream = Mock()
        stream.read.side_effect = lambda frames: (interleaved[:frames * 4], False)
        backend = SimpleNamespace(check_input_settings=Mock(), RawInputStream=Mock(return_value=stream), PortAudioError=RuntimeError)
        with patch.object(probe, "resolve_input_device", return_value=selected), patch.object(probe, "get_backend", return_value=backend):
            status, output, errors = self.capture(probe.run_level_probe, seconds=1, quiet_seconds=.2, stereo=True)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(backend.RawInputStream.call_args.kwargs["channels"], 2)
        backend.RawInputStream.assert_called_once()
        stream.close.assert_called_once()
        self.assertIn("Channel correlation: -1.0000", output)
        self.assertIn("-inf dB", output)
        self.assertIn("not Aqua's production mono capture", output)

    def test_stereo_requires_two_exposed_channels(self):
        with patch.object(probe, "resolve_input_device", return_value=SimpleNamespace(input_channels=1)), patch.object(probe, "get_backend") as backend:
            status, _, errors = self.capture(probe.run_level_probe, stereo=True)
        self.assertEqual(status, 1)
        self.assertIn("does not expose two channels", errors)
        backend.assert_not_called()

    def test_observer_failure_discards_diagnostic_report_without_changing_capture(self):
        stream = FakeMicrophone()
        stream.diagnostic_errors = ["native: RuntimeError"]
        with patch.object(probe, "MicrophoneStream", side_effect=lambda **kwargs: (setattr(stream, "kwargs", kwargs) or stream)):
            status, output, errors = self.capture(probe.run_level_probe, seconds=1, quiet_seconds=.25)
        self.assertEqual(status, 1)
        self.assertTrue(stream.closed)
        self.assertIn("incomplete measurements were discarded", errors)
        self.assertNotIn("Gain evaluation only:", output)

    def test_backend_comparison_requires_exact_endpoint_identity(self):
        self.metadata.return_value = {
            "defaults": [{"role": "console", "endpoint_id": "endpoint-A"}],
            "devices": [
                {"index": 0, "host_api": "MME", "endpoint_id": "endpoint-A"},
                {"index": 6, "host_api": "Windows WASAPI", "endpoint_id": "endpoint-A"},
                {"index": 8, "host_api": "Windows WASAPI", "endpoint_id": "endpoint-B"},
            ],
        }
        with patch.object(probe, "_mono_probe", return_value=0) as capture, patch.object(probe, "sleep") as pause:
            status, output, errors = self.capture(probe.run_level_probe, compare_backends=True)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual([call.kwargs["device"] for call in capture.call_args_list], [0, 6])
        pause.assert_called_once_with(3)
        self.assertIn("sequential", output)

    def test_unknown_explicit_device_never_falls_back_during_comparison(self):
        self.metadata.return_value = {"defaults": [{"role": "console", "endpoint_id": "endpoint-A"}], "devices": []}
        with patch.object(probe, "_mono_probe") as capture:
            status, _, errors = self.capture(probe.run_level_probe, compare_backends=True, device=99)
        self.assertEqual(status, 1)
        capture.assert_not_called()
        self.assertIn("not replaced with the default", errors)

    def test_wake_check_is_explicit_and_does_not_route_actions(self):
        detector = Mock()
        detector.feed.return_value = None
        with patch.object(probe, "MicrophoneStream", side_effect=FakeMicrophone), patch.object(probe, "_wake_detector", return_value=detector) as factory:
            status, output, errors = self.capture(probe.run_level_probe, seconds=1, quiet_seconds=.25, check_wake=True, wake_score=1.5, wake_threshold=.2)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        factory.assert_called_once_with(score=1.5, threshold=.2)
        self.assertEqual(detector.feed.call_count, 32)
        self.assertIn("Wake check events: quiet-window 0; speech-window 0", output)
        self.assertIn("no application route", output)

    def test_default_probe_does_not_load_keyword_model(self):
        with patch.object(probe, "MicrophoneStream", side_effect=FakeMicrophone), patch.object(probe, "_wake_detector") as factory:
            status, _, _ = self.capture(probe.run_level_probe, seconds=1, quiet_seconds=.25)
        self.assertEqual(status, 0)
        factory.assert_not_called()

    def test_conflicting_modes_and_bad_wake_settings_fail_before_metadata_or_capture(self):
        for options in ({"stereo": True, "check_wake": True}, {"stereo": True, "compare_backends": True}, {"wake_threshold": 0}, {"wake_score": float("nan")}):
            with self.subTest(options=options), patch.object(probe, "MicrophoneStream") as stream:
                self.metadata.reset_mock()
                status, _, errors = self.capture(probe.run_level_probe, **options)
                self.assertEqual(status, 1)
                self.assertTrue(errors)
                self.metadata.assert_not_called()
                stream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
