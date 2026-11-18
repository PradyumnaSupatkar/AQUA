"""Windows output encoding must preserve streamed answers without rewriting."""

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO, TextIOWrapper, UnsupportedOperation
import sys
import unittest
from unittest.mock import patch

from aqua import cli


class ConsoleOutputTests(unittest.TestCase):
    def test_chat_uses_utf8_before_parser_or_response_output(self):
        output_bytes, error_bytes = BytesIO(), BytesIO()
        output = TextIOWrapper(output_bytes, encoding="cp1252", newline="")
        errors = TextIOWrapper(error_bytes, encoding="cp1252", newline="")
        answer = "Aqua: Hello 🙂 — नमस्ते"
        status_text = "Status: café ✓"
        original_parser = cli._parser

        def parser_after_configuration():
            self.assertEqual(sys.stdout.encoding, "utf-8")
            self.assertEqual(sys.stderr.encoding, "utf-8")
            return original_parser()

        def respond(**_kwargs):
            print(answer)
            print(status_text, file=sys.stderr)
            return 0

        with redirect_stdout(output), redirect_stderr(errors), patch(
            "aqua.cli._parser", side_effect=parser_after_configuration
        ), patch("aqua.cli.run_text_chat", side_effect=respond):
            self.assertEqual(cli.main(["chat"]), 0)
        output.flush()
        errors.flush()
        self.assertEqual(output_bytes.getvalue(), (answer + "\n").encode("utf-8"))
        self.assertEqual(error_bytes.getvalue(), (status_text + "\n").encode("utf-8"))

    def test_voice_chat_preserves_unicode_and_leaves_stdin_unchanged(self):
        output_bytes = BytesIO()
        output = TextIOWrapper(output_bytes, encoding="cp1252", newline="")
        stdin = StringIO("unchanged input")
        text = "Recognized text: नमस्ते — Aqua 🙂"

        def respond(**_kwargs):
            self.assertIs(sys.stdin, stdin)
            print(text)
            return 0

        with patch.object(sys, "stdin", stdin), redirect_stdout(output), redirect_stderr(StringIO()), patch(
            "aqua.cli.run_voice_chat", side_effect=respond
        ):
            self.assertEqual(cli.main(["voice-chat"]), 0)
        output.flush()
        self.assertEqual(output_bytes.getvalue().decode("utf-8"), text + "\n")
        self.assertEqual(stdin.tell(), 0)
        self.assertEqual(stdin.getvalue(), "unchanged input")

    def test_stringio_streams_work_without_reconfigure(self):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors), patch(
            "aqua.cli.run_text_chat", side_effect=lambda **_kwargs: print("Hello 🙂") or 0
        ):
            self.assertEqual(cli.main(["chat"]), 0)
        self.assertEqual(output.getvalue(), "Hello 🙂\n")
        self.assertEqual(errors.getvalue(), "")

    def test_unsupported_reconfiguration_does_not_abort_cli(self):
        class UnsupportedStream(StringIO):
            def reconfigure(self, **_kwargs):
                raise failure

        for failure in (OSError("unsupported"), ValueError("unavailable"), UnsupportedOperation("unsupported"), TypeError("unsupported signature")):
            with self.subTest(error=type(failure).__name__):
                output, errors = UnsupportedStream(), UnsupportedStream()
                with redirect_stdout(output), redirect_stderr(errors), patch(
                    "aqua.cli.run_text_chat", side_effect=lambda **_kwargs: print("Hello 🙂") or 0
                ):
                    self.assertEqual(cli.main(["chat"]), 0)
                self.assertEqual(output.getvalue(), "Hello 🙂\n")

    def test_noncallable_hook_is_ignored(self):
        class EmbeddedStream(StringIO):
            reconfigure = None

        output = EmbeddedStream()
        with redirect_stdout(output), redirect_stderr(StringIO()), patch(
            "aqua.cli.run_text_chat", return_value=0
        ):
            self.assertEqual(cli.main(["chat"]), 0)


if __name__ == "__main__":
    unittest.main()
