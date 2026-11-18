
import unittest
from unittest.mock import patch

from aqua.actions.launcher import (
    ALLOWED_APPS,
    APP_ALIASES,
    open_application,
)


class TestApplicationAliases(unittest.TestCase):

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


if __name__ == "__main__":
    unittest.main()
