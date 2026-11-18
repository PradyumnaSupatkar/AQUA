"""Exercise CLI output and exit statuses without accessing audio hardware."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from aqua.audio.backend import AudioError
from aqua.audio.devices import DeviceInfo
from aqua.audio.levels import AudioLevels
from aqua.audio.recording import Recording
from aqua.cli import main


class TerminalBuffer(StringIO):
    def isatty(self):
        return True


class CliTests(unittest.TestCase):
    def invoke(self, arguments, *, terminal=False):
        output = TerminalBuffer() if terminal else StringIO()
        errors = StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = main(arguments)
        return status, output.getvalue(), errors.getvalue()

    @staticmethod
    def recording(*, peak=0.5, rms=0.25, clipped=0, overflows=0):
        return Recording(
            pcm=b"\x00\x00" * 50, sample_rate=10,
            levels=AudioLevels(peak=peak, rms=rms, clipped_samples=clipped),
            overflow_count=overflows,
        )

    def test_device_listing_labels_default_and_passes_all_filter(self):
        microphone = DeviceInfo(2, "Test microphone", "WASAPI", 1, 0, 48000.0, True)
        speakers = DeviceInfo(5, "Test speakers", "MME", 0, 2, 44100.0, False)

        def listing(*, include_output):
            return [microphone, speakers] if include_output else [microphone]

        with patch("aqua.cli.list_devices", side_effect=listing) as devices:
            status, output, errors = self.invoke(["devices"])
            self.assertEqual(status, 0)
            self.assertEqual(errors, "")
            self.assertIn("Test microphone", output)
            self.assertIn("WASAPI", output)
            self.assertIn("48000", output)
            self.assertNotIn("Test speakers", output)
            mic_line = next(line for line in output.splitlines() if "Test microphone" in line)
            self.assertTrue(mic_line.startswith("*"))
            devices.assert_called_once_with(include_output=False)

            devices.reset_mock()
            status, output, errors = self.invoke(["devices", "--all"])
            self.assertEqual(status, 0)
            self.assertEqual(errors, "")
            self.assertIn("Test speakers", output)
            self.assertIn("MME", output)
            devices.assert_called_once_with(include_output=True)

    def test_empty_device_list_returns_nonzero_with_guidance(self):
        with patch("aqua.cli.list_devices", return_value=[]):
            status, _output, errors = self.invoke(["devices"])
        self.assertEqual(status, 1)
        self.assertIn("No audio inputs", errors)
        self.assertIn("microphone", errors)

    def test_audio_errors_are_friendly_without_tracebacks(self):
        cases = [("devices", "list_devices"), ("mic-test", "record_test")]
        for command, function in cases:
            with self.subTest(command=command):
                with patch(f"aqua.cli.{function}", side_effect=AudioError("Microphone access denied")):
                    status, output, errors = self.invoke([command])
                self.assertEqual(status, 1)
                self.assertIn("Audio error", errors)
                self.assertIn("Microphone access denied", errors)
                self.assertNotIn("Traceback", output + errors)

    def test_invalid_device_ids_and_rates_exit_before_audio_calls(self):
        invalid_options = [
            ["--device", "-1"], ["--device", "USB"], ["--device", "1.5"],
            ["--sample-rate", "0"], ["--sample-rate", "-16000"],
            ["--sample-rate", "44100.5"], ["--sample-rate", "fast"],
        ]
        with patch("aqua.cli.record_test") as record, patch("aqua.cli.list_devices") as devices:
            for options in invalid_options:
                with self.subTest(options=options):
                    output, errors = StringIO(), StringIO()
                    with redirect_stdout(output), redirect_stderr(errors):
                        with self.assertRaises(SystemExit) as exited:
                            main(["mic-test", *options])
                    self.assertEqual(exited.exception.code, 2)
                    self.assertIn("error:", errors.getvalue())
            record.assert_not_called()
            devices.assert_not_called()

    def test_successful_mic_test_displays_duration_peak_rms_and_privacy(self):
        with patch("aqua.cli.record_test", return_value=self.recording()) as record:
            status, output, errors = self.invoke(["mic-test", "--device", "2", "--sample-rate", "48000"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("5.0s", output)
        self.assertIn("Peak", output)
        self.assertIn("50.0%", output)
        self.assertIn("RMS", output)
        self.assertIn("25.0%", output)
        self.assertIn("dBFS", output)
        self.assertIn("process memory only", output)
        self.assertIn("no recording was saved", output)
        self.assertNotIn("Warning:", output)
        self.assertEqual(record.call_args.kwargs["device"], 2)
        self.assertEqual(record.call_args.kwargs["sample_rate"], 48000)
        self.assertTrue(callable(record.call_args.kwargs["on_progress"]))

    def test_silence_warning(self):
        with patch("aqua.cli.record_test", return_value=self.recording(peak=0, rms=0)):
            status, output, _errors = self.invoke(["mic-test"])
        self.assertEqual(status, 0)
        self.assertIn("no signal detected", output)
        self.assertIn("-inf", output)

    def test_clipping_and_overflow_warnings(self):
        with patch("aqua.cli.record_test", return_value=self.recording(peak=1, clipped=3, overflows=2)):
            status, output, _errors = self.invoke(["mic-test"])
        self.assertEqual(status, 0)
        self.assertIn("3 samples reached full scale", output)
        self.assertIn("lower microphone gain", output)
        self.assertIn("overflow", output)
        self.assertIn("audio was lost", output)

    def test_progress_displays_levels_in_an_interactive_terminal(self):
        result = self.recording()

        def capture(**kwargs):
            kwargs["on_progress"](2.5, result.levels)
            return result

        with patch("aqua.cli.record_test", side_effect=capture):
            status, output, _errors = self.invoke(["mic-test"], terminal=True)
        self.assertEqual(status, 0)
        self.assertIn("2.5/5s", output)
        self.assertGreaterEqual(output.count("Peak"), 2)
        self.assertGreaterEqual(output.count("RMS"), 2)

    def test_interruption_returns_130_with_cancel_message(self):
        with patch("aqua.cli.record_test", side_effect=KeyboardInterrupt):
            status, output, errors = self.invoke(["mic-test"])
        self.assertEqual(status, 130)
        self.assertIn("cancelled", errors)
        self.assertIn("microphone was released", errors)
        self.assertNotIn("Traceback", output + errors)


if __name__ == "__main__":
    unittest.main()
