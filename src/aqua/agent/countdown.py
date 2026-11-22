"""The spoken 30-second countdown before a confirmed restart or shutdown."""

from dataclasses import dataclass, field
import re

COUNTDOWN_SECONDS = 30
ANNOUNCE_AT = (20, 10, 5)  # Seconds left at which hands-free mode speaks again.
_CANCEL = re.compile(r"\b(?:cancel|stop|abort|halt|no|nope|don'?t|do\s+not|wait|never\s*mind)\b", re.IGNORECASE)
MAX_CANCEL_WORDS = 8


@dataclass(slots=True)
class Countdown:
    kind: str                   # restart | shutdown
    deadline: float             # desktop.monotonic() time when Windows acts
    fresh: bool = True          # Not yet announced with "say cancel"
    announced: set = field(default_factory=set)

    def remaining(self, now: float) -> float:
        return max(0.0, self.deadline - now)

    def active(self, now: float) -> bool:
        return now < self.deadline

    def due_announcement(self, now: float) -> str | None:
        """The next spoken reminder ("20 seconds."), once each, while active."""
        left = self.remaining(now)
        for mark in sorted(ANNOUNCE_AT):  # The nearest mark; earlier ones are skipped, not replayed.
            if left <= mark and mark not in self.announced and left > 0:
                self.announced.update(m for m in ANNOUNCE_AT if m >= mark)
                return f"{self.kind.capitalize()} in {mark} seconds. Say cancel to stop it."
        return None


def is_cancel(text: str) -> bool:
    """Any short utterance with cancel/stop/no aborts; aborting wrongly is the safe mistake."""
    if not isinstance(text, str):
        return False
    text = text.replace("’", "'")
    return len(text.split()) <= MAX_CANCEL_WORDS and bool(_CANCEL.search(text))
