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


_YES_WORDS = ("yes", "yeah", "yep", "yup", "sure", "okay")
_NO_WORDS = ("no", "nope", "nah")
# Real words one edit away from yes/no are not typos ("now" is closer to "do it now" than to "no").
_REAL_WORDS = {"now", "new", "nor", "not", "yet", "yen", "yew", "ye", "nap", "noon", "know", "yell", "yeast"}


def _collapse(word: str) -> str:
    """Repeated letters as one: "yess" -> "yes", "noo" -> "no", "okk" -> "ok"."""
    return re.sub(r"(.)\1+", r"\1", word)


def _one_edit(a: str, b: str) -> bool:
    """Within one insertion, deletion, substitution or swap of neighbours."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _fuzzy(word: str) -> str | None:
    """A typo'd one-word answer ("ys", "yse", "yess", "noo"). A "yes" needs a word of 2+ letters
    that starts with y; a wrong "no" only cancels, so it is the more forgiving side."""
    if word in _REAL_WORDS or _collapse(word) in _REAL_WORDS:
        return None
    word = _collapse(word)
    if word in ("ok", "okay", "okey"):
        return "yes"
    if word.startswith("y") and len(word) >= 2 and any(_one_edit(word, yes) for yes in _YES_WORDS):
        return "yes"
    if word.startswith("n") and any(_one_edit(word, no) for no in _NO_WORDS):
        return "no"
    return None


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
    meaningful = [word for word in words if word not in _FILLER]
    if len(meaningful) == 1:
        return _fuzzy(meaningful[0])
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
    summary: str = ""           # e.g. "force quit Spotify", for the "not done" note

    def expired(self, now: float) -> bool:
        return now - self.created > self.expires_after


class ConfirmationGate:
    """Holds at most one pending action. Each answer consumes it.

    Actions that end without a yes (declined, expired, or replaced by another
    request) are kept in ``dropped`` until the next request reads them, so the
    model is told they were not done and does not propose them again.
    """

    def __init__(self) -> None:
        self.pending: PendingAction | None = None
        self.dropped: list[PendingAction] = []

    def offer(self, action: PendingAction) -> None:
        self.pending = action

    def clear(self) -> None:
        self.pending = None
        self.dropped.clear()

    def take_dropped(self) -> list[PendingAction]:
        dropped, self.dropped = self.dropped, []
        return dropped

    def answer(self, text: str, now: float) -> tuple[str, PendingAction | None]:
        """("yes"|"no", action) for an answer, otherwise ("none", None).

        Any other utterance, or one after the expiry, cancels the pending action.
        """
        action, self.pending = self.pending, None
        if action is None:
            return "none", None
        verdict = None if action.expired(now) else classify_reply(text)
        if verdict != "yes":
            self.dropped.append(action)
        if verdict is None:
            return "none", None
        return verdict, action
