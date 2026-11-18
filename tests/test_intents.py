
import unittest

from aqua.actions.intents import (
    parse_launch_request,
    is_launch_request,
)


class TestLaunchIntents(unittest.TestCase):


    def test_polite_vs_code_request(self):
        self.assertEqual(
            parse_launch_request(
                "Hey Aqua, can you please open VS Code?"
            ),
            "vs code",
        )

    def test_polite_chrome_request(self):
        self.assertEqual(
            parse_launch_request(
                "Could you launch Chrome?"
            ),
            "chrome",
        )

    def test_polite_unapproved_request(self):
        text = "Aqua, can you open PowerShell?"

        self.assertIsNone(parse_launch_request(text))
        self.assertTrue(is_launch_request(text))

    def test_polite_compound_request(self):
        text = "Can you please open Chrome and delete my files?"

        self.assertIsNone(parse_launch_request(text))
        self.assertTrue(is_launch_request(text))

    def test_negative_request(self):
        self.assertIsNone(
            parse_launch_request("Don't open Chrome")
        )


    def test_open_notepad(self):
        self.assertEqual(
            parse_launch_request("Open Notepad"),
            "notepad",
        )

    def test_aqua_open_calculator(self):
        self.assertEqual(
            parse_launch_request("Aqua, open Calculator"),
            "calculator",
        )

    def test_hey_aqua(self):
        self.assertEqual(
            parse_launch_request("Hey Aqua, please launch Notepad!"),
            "notepad",
        )

    def test_normal_question(self):
        self.assertIsNone(
            parse_launch_request("What is Notepad?"),
        )

    def test_negative_command(self):
        self.assertIsNone(
            parse_launch_request("Don't open Notepad"),
        )

    def test_unknown_application(self):
        self.assertIsNone(
            parse_launch_request("Open PowerShell"),
        )

    def test_reject_extra_instructions(self):
        self.assertIsNone(
            parse_launch_request(
                "Open Notepad and delete my files"
            )
        )

    def test_reject_embedded_command(self):
        self.assertIsNone(
            parse_launch_request(
                "Please explain how to open Notepad"
            )
        )


if __name__ == "__main__":
    unittest.main()
