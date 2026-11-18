"""Wake diagnostics remain opt-in, memory-only, and effect-free in tests."""

import base64
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

from aqua.wake.models import WakeError
from tools import smoke_wake


def wav_payload(*, channels=1, width=2, rate=16000):
    target = BytesIO()
    with wave.open(target, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        if width == 2:
            values = np.zeros((1600, channels), dtype=np.int16)
            if channels == 2:
                values[:, 0], values[:, 1] = 1000, -1000
            wav.writeframes(values.tobytes())
        else:
            wav.writeframes(bytes(1600 * channels))
    return base64.b64encode(target.getvalue()).decode("ascii")


class WakeSmokeTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Offline wake tests must not use network or processes."))
            manager.start()
            self.addCleanup(manager.stop)

    def invoke(self, args):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = smoke_wake.main(args)
        return status, output.getvalue(), errors.getvalue()

    def test_no_mode_opens_neither_model_nor_microphone(self):
        with patch.object(smoke_wake, "_detector") as detector, patch.object(smoke_wake, "MicrophoneStream") as microphone, patch.object(smoke_wake, "_synthesize") as synthesize:
            status, output, errors = self.invoke([])
        self.assertEqual(status, 2)
        self.assertIn("--synthetic", output)
        self.assertIn("No microphone or model was opened", errors)
        detector.assert_not_called()
        microphone.assert_not_called()
        synthesize.assert_not_called()

    def test_synthesis_uses_fixed_code_and_json_stdin_without_shell(self):
        phrase = "Hey Aqua'; this is fixture data."
        with patch.object(smoke_wake.subprocess, "run", return_value=SimpleNamespace(stdout=wav_payload())) as run:
            audio = smoke_wake._synthesize(phrase)
        args, kwargs = run.call_args
        self.assertEqual(args[0][-1], smoke_wake.SYNTHESIS_SCRIPT)
        self.assertNotIn(phrase, args[0][-1])
        self.assertEqual(json.loads(kwargs["input"]), phrase)
        self.assertFalse(kwargs["shell"])
        self.assertEqual(audio.shape, (1600,))
        self.assertEqual(audio.dtype, np.float32)

    def test_stereo_wave_is_downmixed_in_memory(self):
        with patch.object(smoke_wake.subprocess, "run", return_value=SimpleNamespace(stdout=wav_payload(channels=2))):
            audio = smoke_wake._synthesize("Hey Aqua")
        np.testing.assert_array_equal(audio, np.zeros(1600, dtype=np.float32))

    def test_bad_synthetic_data_fails_without_echoing_raw_payload(self):
        for payload in ("private invalid payload", base64.b64encode(b"").decode("ascii"), wav_payload(width=1)):
            with self.subTest(payload=payload[:10]):
                with patch.object(smoke_wake.subprocess, "run", return_value=SimpleNamespace(stdout=payload)):
                    with self.assertRaises(WakeError) as caught:
                        smoke_wake._synthesize("Hey Aqua")
                self.assertNotIn("private", str(caught.exception))

    def test_paused_fixture_has_actual_one_and_half_second_silence(self):
        with patch.object(smoke_wake, "_synthesize", return_value=np.ones(512, dtype=np.float32)) as synthesize:
            fixtures = smoke_wake._fixtures()
        self.assertEqual(len(fixtures), 6)
        self.assertEqual([fixture.expected_wakes for fixture in fixtures], [1, 1, 1, 0, 0, 0])
        paused = fixtures[2].samples
        self.assertEqual(paused.size, 512 + 24000 + 512)
        np.testing.assert_array_equal(paused[512:-512], np.zeros(24000))
        self.assertIn("Hey Aqua.", [call.args[0] for call in synthesize.call_args_list])
        self.assertIn("Hey Jarvis, open Chrome.", [call.args[0] for call in synthesize.call_args_list])

    def test_synthetic_checks_match_counts_and_do_not_open_microphone(self):
        fixtures = (smoke_wake.Fixture("exact phrase", np.zeros(512), 1), smoke_wake.Fixture("ordinary speech", np.zeros(512), 0))
        detector = Mock()
        state = {"fixture": -1, "frames": 0}

        def reset():
            state["fixture"] += 1
            state["frames"] = 0

        def feed(_frame):
            state["frames"] += 1
            return SimpleNamespace(keyword="Hey Aqua") if state["fixture"] == 0 and state["frames"] == 1 else None

        detector.reset.side_effect = reset
        detector.feed.side_effect = feed
        with patch.object(smoke_wake, "_detector", return_value=detector), patch.object(smoke_wake, "_fixtures", return_value=fixtures), patch.object(smoke_wake, "_resident_memory_mib", return_value=None), patch.object(smoke_wake, "MicrophoneStream") as microphone:
            status, output, errors = self.invoke(["--synthetic"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("PASS exact phrase", output)
        self.assertIn("PASS ordinary speech", output)
        self.assertIn("event audio positions", output)
        self.assertIn("do not establish microphone accuracy", output)
        microphone.assert_not_called()

    def test_repeated_detections_for_one_phrase_are_reported_as_failure(self):
        detector = Mock()
        detector.feed.return_value = SimpleNamespace(keyword="Hey Aqua")
        fixture = smoke_wake.Fixture("single phrase", np.zeros(512), 1)
        with patch.object(smoke_wake, "_detector", return_value=detector), patch.object(smoke_wake, "_fixtures", return_value=(fixture,)), patch.object(smoke_wake, "_resident_memory_mib", return_value=None):
            status, output, _errors = self.invoke(["--synthetic"])
        self.assertEqual(status, 1)
        self.assertIn("FAIL single phrase", output)

    def test_missing_model_is_reported_without_capture_or_synthesis(self):
        with patch.object(smoke_wake, "_detector", side_effect=WakeError("Missing local model")), patch.object(smoke_wake, "MicrophoneStream") as microphone, patch.object(smoke_wake, "_synthesize") as synthesize:
            status, _output, errors = self.invoke(["--synthetic"])
        self.assertEqual(status, 1)
        self.assertIn("Missing local model", errors)
        microphone.assert_not_called()
        synthesize.assert_not_called()

    def test_ambient_mode_runs_one_stream_and_reports_resources_without_synthesis(self):
        clock = [0.0]
        cpu = [0.0]
        microphone = Mock(native_sample_rate=44100)
        microphone.__enter__ = Mock(return_value=microphone)
        microphone.__exit__ = Mock(return_value=False)

        def read_frame(**_kwargs):
            clock[0] += .1
            cpu[0] += .001
            return np.zeros(512, dtype=np.float32)

        microphone.read_frame.side_effect = read_frame
        detector = Mock()
        detector.feed.return_value = None
        with patch.object(smoke_wake, "_detector", return_value=detector), patch.object(smoke_wake, "MicrophoneStream", return_value=microphone) as factory, patch.object(smoke_wake, "_synthesize") as synthesize, patch.object(smoke_wake, "perf_counter", side_effect=lambda: clock[0]), patch.object(smoke_wake, "process_time", side_effect=lambda: cpu[0]), patch.object(smoke_wake, "_resident_memory_mib", return_value=100.0):
            status, output, errors = self.invoke(["--microphone", "--seconds", ".5", "--device", "7"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        factory.assert_called_once_with(device=7, sample_rate=None)
        microphone.__exit__.assert_called_once()
        self.assertIn("process CPU=", output)
        self.assertIn("100.0 MiB", output)
        self.assertIn("wakes=0", output)
        self.assertIn("only if nobody said Hey Aqua", output)
        synthesize.assert_not_called()

    def test_duration_is_bounded_before_loading_model(self):
        with patch.object(smoke_wake, "_detector") as detector, redirect_stderr(StringIO()):
            for seconds in ("0", "61", "nan", "inf"):
                with self.subTest(seconds=seconds), self.assertRaises(SystemExit):
                    smoke_wake.main(["--microphone", "--seconds", seconds])
        detector.assert_not_called()


if __name__ == "__main__":
    unittest.main()
