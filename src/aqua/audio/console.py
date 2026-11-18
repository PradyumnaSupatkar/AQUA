"""Nonblocking Windows console controls for the continuous microphone loop."""

import os
import sys


class ConsoleCommands:
    """Collect bounded command lines without threads, echo, or blocking reads.

    Redirected stdin is deliberately ignored. ``keyboard`` and ``stdin`` are
    injectable so ordinary tests require neither a console nor Windows APIs.
    The caller decides how to handle completed text; this class executes none.
    """

    def __init__(self, *, keyboard=None, stdin=None, max_length: int = 128):
        if type(max_length) is not int or not 1 <= max_length <= 4096:
            raise ValueError("max_length must be an integer from 1 to 4096.")
        self._keyboard = None
        self._buffer: list[str] = []
        self._max_length = max_length
        self._discard = False
        self._extended = False
        stream = sys.stdin if stdin is None else stdin
        try:
            interactive = stream is not None and stream.isatty()
        except (AttributeError, OSError, ValueError):
            interactive = False
        if not interactive:
            return
        if keyboard is None:
            if os.name != "nt":
                return
            import msvcrt
            keyboard = msvcrt
        self._keyboard = keyboard

    def poll(self) -> str | None:
        """Return at most one complete line; consume a bounded number of keys."""
        if self._keyboard is None:
            return None
        # A paste/key flood cannot monopolize the real-time audio consumer.
        for _ in range(128):
            if not self._keyboard.kbhit():
                break
            character = self._keyboard.getwch()
            if character == "\x03":
                self._buffer.clear()
                self._discard = self._extended = False
                raise KeyboardInterrupt
            if self._extended:
                self._extended = False
                continue
            if character in ("\x00", "\xe0"):
                self._extended = True
                continue
            if character in ("\r", "\n"):
                # A submitted overflow still counts as a cancelling console
                # interaction. The existing handler treats bare '/' as an
                # unknown control and clears pending application permission.
                text = "/" if self._discard else "".join(self._buffer).strip()
                self._buffer.clear()
                self._discard = False
                if text:
                    return text
                continue
            if self._discard:
                continue
            if character == "\b":
                if self._buffer:
                    self._buffer.pop()
            elif character.isprintable():
                if len(self._buffer) == self._max_length:
                    # Never execute a truncated prefix of a longer input.
                    self._buffer.clear()
                    self._discard = True
                else:
                    self._buffer.append(character)
        return None
