"""Read-only microphone diagnostics stay explicit and isolated from inference."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from aqua import cli


class LevelProbeCliTests(unittest.TestCase):
    def invoke(self, args):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            result = cli.main(args)
        return result, output.getvalue(), errors.getvalue()

    def test_default_probe_uses_no_model_or_normal_recording_route(self):
        with patch("aqua.cli.run_level_probe", return_value=0) as probe, \
                patch("aqua.cli.record_test", side_effect=AssertionError("Wrong recording path")), \
                patch("aqua.cli.OfflineRecognizer", side_effect=AssertionError("No inference")):
            status, _, _ = self.invoke(["mic-levels"])
        self.assertEqual(status, 0)
        probe.assert_called_once_with(device=None, sample_rate=None, seconds=8.0, quiet_seconds=2.0, stereo=False)

    def test_explicit_device_rate_and_stereo_do_not_change_defaults(self):
        with patch("aqua.cli.run_level_probe", return_value=0) as probe:
            status, _, _ = self.invoke(["mic-levels", "--device", "6", "--sample-rate", "48000", "--seconds", "5", "--quiet-seconds", "1", "--stereo"])
        self.assertEqual(status, 0)
        probe.assert_called_once_with(device=6, sample_rate=48000, seconds=5.0, quiet_seconds=1.0, stereo=True)

    def test_invalid_duration_is_rejected_before_capture(self):
        for option, value in (("--seconds", "0"), ("--seconds", "31"), ("--seconds", "nan"), ("--quiet-seconds", "-1"), ("--device", "-1")):
            with self.subTest(option=option, value=value), patch("aqua.cli.run_level_probe") as probe:
                with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                    cli.main(["mic-levels", option, value])
                probe.assert_not_called()

    def test_help_does_not_open_microphone(self):
        with patch("aqua.cli.run_level_probe") as probe, redirect_stdout(StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cli.main(["mic-levels", "--help"])
        self.assertEqual(caught.exception.code, 0)
        probe.assert_not_called()

    def test_optional_backend_and_wake_check_flags_are_forwarded(self):
        with patch("aqua.cli.run_level_probe", return_value=0) as probe:
            status, _, _ = self.invoke(["mic-levels", "--compare-backends", "--check-wake", "--wake-score", "1.5", "--wake-threshold", ".2"])
        self.assertEqual(status, 0)
        probe.assert_called_once_with(device=None, sample_rate=None, seconds=8.0, quiet_seconds=2.0, stereo=False,
                                     compare_backends=True, check_wake=True, wake_score=1.5, wake_threshold=.2)

    def test_wake_tuning_without_explicit_model_check_is_not_silently_ignored(self):
        with patch("aqua.cli.run_level_probe") as probe, redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(["mic-levels", "--wake-score", "2"])
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
