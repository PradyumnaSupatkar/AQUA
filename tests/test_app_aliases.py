
import unittest
from unittest.mock import patch

from aqua.actions.intents import parse_launch_request
from aqua.actions.launcher import (
    ALLOWED_APPS,
    APP_ALIASES,
    open_application,
)
from aqua.actions.router import route_launch_request


class TestApplicationAliases(unittest.TestCase):

    def test_vs_code_intent(self):
        self.assertEqual(
            parse_launch_request("Hey Aqua, open VS Code"),
            "vs code",
        )

    def test_visual_studio_code_intent(self):
        self.assertEqual(
            parse_launch_request("Open Visual Studio Code"),
            "vs code",
        )

    def test_chrome_intent(self):
        self.assertEqual(
            parse_launch_request("Aqua, launch Chrome"),
            "chrome",
        )

    def test_google_chrome_intent(self):
        self.assertEqual(
            parse_launch_request("Open Google Chrome"),
            "chrome",
        )

    @patch("aqua.actions.launcher.subprocess.Popen")
    def test_launch_vs_code(self, mock_popen):
        result = open_application("Visual Studio Code")

        self.assertTrue(result.success)
        mock_popen.assert_called_once_with(
            list(ALLOWED_APPS["vs code"]),
            shell=False,
        )

    @patch("aqua.actions.launcher.subprocess.Popen")
    def test_launch_chrome(self, mock_popen):
        result = open_application("Google Chrome")

        self.assertTrue(result.success)
        mock_popen.assert_called_once_with(
            list(ALLOWED_APPS["chrome"]),
            shell=False,
        )

    @patch("aqua.actions.launcher.subprocess.Popen")
    def test_reject_unapproved_app(self, mock_popen):
        result = open_application("powershell")

        self.assertFalse(result.success)
        mock_popen.assert_not_called()

    @patch("aqua.actions.launcher.subprocess.Popen")
    def test_aliases_do_not_accept_model_supplied_executable_arguments(self, mock_popen):
        for name in ("Google Chrome --incognito", "Visual Studio Code /tmp/file", "calc.exe"):
            with self.subTest(name=name):
                self.assertFalse(open_application(name).success)
        mock_popen.assert_not_called()

    def test_alias_registry_cannot_redirect_an_alias(self):
        with self.assertRaises(TypeError):
            APP_ALIASES["google chrome"] = "powershell"

    @patch("aqua.actions.router.open_application")
    def test_reject_compound_command(self, mock_launch):
        result = route_launch_request(
            "Open Chrome and delete my files"
        )

        self.assertFalse(result.success)
        mock_launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
