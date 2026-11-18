"""Console polling is bounded and never reads redirected stdin."""

from collections import deque
from contextlib import redirect_stdout
from io import StringIO
import unittest

from aqua.audio.console import ConsoleCommands


class Keyboard:
    def __init__(self, characters=""):
        self.characters = deque(characters)
        self.reads = 0

    def kbhit(self):
        return bool(self.characters)

    def getwch(self):
        self.reads += 1
        return self.characters.popleft()


class InteractiveInput:
    def isatty(self):
        return True

    def read(self, *_args):
        raise AssertionError("poll must never block on stdin.read")


class ConsoleCommandTests(unittest.TestCase):
    def console(self, text="", **options):
        keyboard = Keyboard(text)
        return ConsoleCommands(keyboard=keyboard, stdin=InteractiveInput(), **options), keyboard

    def test_waits_for_complete_line_without_blocking_or_echo(self):
        console, keyboard = self.console("/cle")
        with redirect_stdout(StringIO()) as output:
            self.assertIsNone(console.poll())
            keyboard.characters.extend("ar\r")
            self.assertEqual(console.poll(), "/clear")
        self.assertEqual(output.getvalue(), "")

    def test_preserves_multiple_commands_for_subsequent_polls(self):
        console, _keyboard = self.console("/help\r/clear\r/quit\r")
        self.assertEqual(console.poll(), "/help")
        self.assertEqual(console.poll(), "/clear")
        self.assertEqual(console.poll(), "/quit")
        self.assertIsNone(console.poll())

    def test_backspace_blank_lines_and_whitespace(self):
        console, _keyboard = self.console("\r\n /cleax\br \n")
        self.assertEqual(console.poll(), "/clear")

    def test_redirected_stdin_never_reads_keyboard_or_stdin(self):
        keyboard = Keyboard("/quit\r")
        console = ConsoleCommands(keyboard=keyboard, stdin=StringIO("/clear\n"))
        self.assertIsNone(console.poll())
        self.assertEqual(keyboard.reads, 0)

    def test_closed_or_missing_stdin_is_ignored(self):
        closed = StringIO()
        closed.close()
        self.assertIsNone(ConsoleCommands(keyboard=Keyboard("x"), stdin=closed).poll())

    def test_ctrl_c_cancels_and_clears_partial_command(self):
        console, keyboard = self.console("/clear\x03")
        with self.assertRaises(KeyboardInterrupt):
            console.poll()
        keyboard.characters.extend("/quit\r")
        self.assertEqual(console.poll(), "/quit")

    def test_extended_keys_and_nonprintable_controls_are_not_command_text(self):
        console, _keyboard = self.console("\xe0H/cle\x00Kar\x1b\x07\r")
        self.assertEqual(console.poll(), "/clear")

    def test_oversized_input_is_discarded_instead_of_truncated(self):
        console, _keyboard = self.console("/clear-overflow\r/quit\r", max_length=6)
        self.assertEqual(console.poll(), "/")
        self.assertEqual(console.poll(), "/quit")

    def test_key_flood_is_bounded_per_poll(self):
        console, keyboard = self.console("x" * 1000)
        self.assertIsNone(console.poll())
        self.assertEqual(keyboard.reads, 128)

    def test_invalid_buffer_bounds_are_rejected(self):
        for value in (0, -1, True, 4097, 2.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ConsoleCommands(stdin=StringIO(), max_length=value)


if __name__ == "__main__":
    unittest.main()
