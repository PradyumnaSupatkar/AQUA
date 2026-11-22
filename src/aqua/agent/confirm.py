"""Natural yes/no answers for one exact pending action."""

from dataclasses import dataclass, field
import re

CONFIRM_SECONDS = 60.0

AFFIRMATIVE = (
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go ahead", "do it", "confirm",
    "please do", "yes please", "affirmative", "please", "go", "yes do it", "sure thing",
    "ya", "yea", "alright", "all right", "of course", "definitely", "absolutely", "correct",
    "go for it", "yes go ahead", "confirmed",
)
NEGATIVE = (
    "no", "nope", "don't", "do not", "cancel", "stop", "never mind", "nevermind", "no thanks",
    "no thank you", "don't do it", "do not do it", "not now", "nah", "abort", "forget it",
    "cancel that", "no don't", "no cancel",
)
_FILLER = {"aqua", "hey", "please", "thanks", "thank", "you", "just", "um", "uh", "well", "oh"}


def _phrases(options):
    return sorted((tuple(option.split()) for option in options), key=len, reverse=True)


_YES, _NO = _phrases(AFFIRMATIVE), _phrases(NEGATIVE)


def _words(text: str) -> list[str]:
    text = text.casefold().replace("’", "'")
    return re.findall(r"[a-z]+(?:'[a-z]+)?", text)


def _covered(words: list[str], phrases) -> bool:
    """True when the words are entirely made of the given phrases (plus filler),
    with at least one phrase. A small search, since replies are a few words."""
    def cover(index: int) -> bool | None:
        if index == len(words):
            return False  # Covered, but no phrase seen on this path yet.
        results = []
        for phrase in phrases:
            if tuple(words[index:index + len(phrase)]) == phrase:
                rest = cover(index + len(phrase))
                if rest is not None:
                    return True
        if words[index] in _FILLER:
            rest = cover(index + 1)
            if rest is not None:
                results.append(rest)
        return results[0] if results else None

    return cover(0) is True


def classify_reply(text: str) -> str | None:
    """Return "yes", "no", or None when the reply is something else."""
    if not isinstance(text, str):
        return None
    words = _words(text)
    if not words or len(words) > 8:
        return None
    if _covered(words, _NO):
        return "no"
    if _covered(words, _YES):
        return "yes"
    return None


@dataclass(slots=True)
class PendingAction:
    """The exact tool call awaiting a yes, plus the loop state to resume after it."""
    tool: str
    args: dict
    question: str
    created: float
    request: str
    messages: list = field(default_factory=list)
    tool_names: tuple = ()
    decisions: int = 0
    operations: list = field(default_factory=list)
    start: int = 0
    expires_after: float = CONFIRM_SECONDS

    def expired(self, now: float) -> bool:
        return now - self.created > self.expires_after


class ConfirmationGate:
    """Holds at most one pending action. Each answer consumes it."""

    def __init__(self) -> None:
        self.pending: PendingAction | None = None

    def offer(self, action: PendingAction) -> None:
        self.pending = action

    def clear(self) -> None:
        self.pending = None

    def answer(self, text: str, now: float) -> tuple[str, PendingAction | None]:
        """("yes"|"no", action) for an answer, otherwise ("none", None).

        Any other utterance, or one after the expiry, cancels the pending action.
        """
        action, self.pending = self.pending, None
        if action is None or action.expired(now):
            return "none", None
        verdict = classify_reply(text)
        if verdict is None:
            return "none", None
        return verdict, action
