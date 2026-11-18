"""Validate the answer channel without rewriting legitimate answer text."""

from aqua.brain.ollama import OllamaProtocolError


class AnswerChannelGuard:
    """Fail closed if an answer starts with a raw reasoning-channel marker.

    Ollama's separate ``message.thinking`` field is discarded by the backend.
    Partial prefixes are held before output, and a leading ``<think>`` or
    ``</think>`` is a protocol failure. Successful answers are never rewritten.
    Literal tags after ordinary text or inside fenced code pass unchanged.
    Unmarked prose cannot be classified as reasoning by this structural check.
    """

    _MARKERS = ("<think>", "</think>")

    def __init__(self) -> None:
        self._pending = ""
        self._accepted = False
        self._finished = False
        self._rejected = False

    def _reject(self, *, incomplete: bool = False) -> None:
        self._pending = ""
        self._rejected = True
        detail = "an incomplete reasoning marker" if incomplete else "a raw reasoning marker"
        raise OllamaProtocolError(
            f"Ollama returned {detail} in its answer channel. "
            "No answer was displayed or retained; check the local model's thinking compatibility."
        )

    def feed(self, text: str) -> str:
        """Return answer text unchanged once its initial channel is accepted."""
        if self._finished:
            raise ValueError("This answer guard has already finished.")
        if self._rejected:
            self._reject()
        if not isinstance(text, str):
            raise ValueError("Stream content must be text.")
        if self._accepted:
            return text
        self._pending += text
        prefix = self._pending.lstrip().lower()
        if not prefix:
            return ""
        if any(prefix.startswith(marker) for marker in self._MARKERS):
            self._reject()
        if any(marker.startswith(prefix) for marker in self._MARKERS):
            return ""
        self._accepted = True
        visible, self._pending = self._pending, ""
        return visible

    def finish(self) -> str:
        """Flush harmless whitespace, rejecting ambiguous unfinished markers."""
        if self._rejected:
            self._reject()
        if self._finished:
            return ""
        self._finished = True
        prefix = self._pending.lstrip().lower()
        if prefix and any(marker.startswith(prefix) for marker in self._MARKERS):
            self._reject(incomplete=True)
        remaining, self._pending = self._pending, ""
        return remaining
