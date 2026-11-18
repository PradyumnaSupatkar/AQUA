
import unittest
from contextlib import chdir
from pathlib import Path
import tempfile
from unittest.mock import patch

from aqua.actions.launcher import ALLOWED_APPS, SYSTEM_DIRECTORY, open_application


class TestApplicationLauncher(unittest.TestCase):

    @patch("aqua.actions.launcher.subprocess.Popen")
    def test_open_notepad(self, mock_popen):
        result = open_application("notepad")

        self.assertTrue(result.success)
        mock_popen.assert_called_once_with(
            list(ALLOWED_APPS["notepad"]),
            shell=False,
        )
        executable = Path(mock_popen.call_args.args[0][0])
        self.assertTrue(executable.is_absolute())
        self.assertEqual(executable.parent, SYSTEM_DIRECTORY)
        self.assertEqual(executable.parts[-2:], ("System32", "notepad.exe"))

    @patch("aqua.actions.launcher.subprocess.Popen")
    def test_open_calculator(self, mock_popen):
        result = open_application("calculator")

        self.assertTrue(result.success)
        mock_popen.assert_called_once_with(
            list(ALLOWED_APPS["calculator"]),
            shell=False,
        )
        executable = Path(mock_popen.call_args.args[0][0])
        self.assertTrue(executable.is_absolute())
        self.assertEqual(executable.parent, SYSTEM_DIRECTORY)
        self.assertEqual(executable.parts[-2:], ("System32", "calc.exe"))

    @patch("aqua.actions.launcher.subprocess.Popen")
    def test_reject_powershell(self, mock_popen):
        result = open_application("powershell")

        self.assertFalse(result.success)
        mock_popen.assert_not_called()

    @patch("aqua.actions.launcher.subprocess.Popen")
    def test_reject_unknown_application(self, mock_popen):
        result = open_application("unknown_app")

        self.assertFalse(result.success)
        mock_popen.assert_not_called()

    @patch("aqua.actions.launcher.subprocess.Popen")
    def test_case_insensitive_names(self, mock_popen):
        result = open_application("  NOTEPAD  ")

        self.assertTrue(result.success)
        mock_popen.assert_called_once()

    @patch("aqua.actions.launcher.subprocess.Popen")
    def test_launch_failure(self, mock_popen):
        mock_popen.side_effect = OSError("App not found")

        result = open_application("notepad")

        self.assertFalse(result.success)

    @patch("aqua.actions.launcher.subprocess.Popen")
    def test_nontext_names_fail_safely_without_launch_or_raw_echo(self, mock_popen):
        values = (None, 42, ["chrome"], {"app": "chrome"}, Path("notepad"), b"chrome")
        for value in values:
            with self.subTest(value_type=type(value).__name__):
                result = open_application(value)
                self.assertFalse(result.success)
                self.assertEqual(result.message, "That application is not approved.")
        mock_popen.assert_not_called()

    @patch("aqua.actions.launcher.subprocess.Popen")
    def test_paths_executable_names_arguments_and_shell_payloads_are_not_names(self, mock_popen):
        values = (
            "notepad.exe", "calc.exe", r"C:\Windows\System32\notepad.exe",
            r".\notepad.exe", r"\\server\share\chrome.exe", "chrome --incognito",
            "notepad & calc", "chrome; powershell", "chrome | powershell",
            "$(calc)", "chrome\x00",
        )
        for value in values:
            with self.subTest(value=value):
                result = open_application(value)
                self.assertFalse(result.success)
                self.assertEqual(result.message, "That application is not approved.")
        mock_popen.assert_not_called()

    @patch("aqua.actions.launcher.subprocess.Popen")
    def test_current_directory_cannot_replace_the_approved_system_executable(self, mock_popen):
        with tempfile.TemporaryDirectory() as directory:
            impostor = Path(directory) / "notepad.exe"
            impostor.write_bytes(b"synthetic non-executable fixture")
            with chdir(directory):
                result = open_application("notepad")
            self.assertTrue(result.success)
            executable = Path(mock_popen.call_args.args[0][0])
            self.assertTrue(executable.is_absolute())
            self.assertEqual(executable, SYSTEM_DIRECTORY / "notepad.exe")
            self.assertNotEqual(executable, impostor)
        mock_popen.assert_called_once_with(list(ALLOWED_APPS["notepad"]), shell=False)

    def test_approved_app_registry_cannot_gain_an_unapproved_command(self):
        self.assertEqual(set(ALLOWED_APPS), {"notepad", "calculator", "vs code", "chrome"})
        with self.assertRaises(TypeError):
            ALLOWED_APPS["powershell"] = ("powershell.exe",)


if __name__ == "__main__":
    unittest.main()
