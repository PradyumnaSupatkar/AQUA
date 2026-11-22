"""Installed-app index with forgiving name matching ("vs code", "chrome", ...)."""

from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
import ntpath
import re
import threading

from .adapter import AppEntry

ALIASES = {
    "vs code": "visual studio code", "vscode": "visual studio code", "code": "visual studio code",
    "visual code": "visual studio code", "vs": "visual studio",
    "chrome": "google chrome", "google": "google chrome",
    "edge": "microsoft edge", "firefox": "mozilla firefox",
    "calc": "calculator", "calculator app": "calculator",
    "explorer": "file explorer", "files": "file explorer", "my files": "file explorer",
    "word": "word", "excel": "excel", "powerpoint": "powerpoint", "outlook": "outlook",
    "teams": "microsoft teams", "store": "microsoft store", "settings": "settings",
    "paint": "paint", "notepad": "notepad", "spotify": "spotify",
    "command line": "command prompt", "cmd": "command prompt", "cmd exe": "command prompt",
}
_DROP_WORDS = {"the", "app", "application", "program", "window", "my"}
_NOISE = ("uninstall", "readme", "read me", "help", "documentation", "website", "release notes",
          "license", "manual", "safe mode")
REFRESH_SECONDS = 30.0


def normalize(value: str) -> str:
    words = re.sub(r"[^\w\s+#]", " ", value.casefold().replace("’", "'")).split()
    words = [w for w in words if w not in _DROP_WORDS] or words
    text = " ".join(words)
    return ALIASES.get(text, text)


def executable_stem(entry: AppEntry) -> str:
    source = entry.executable or (entry.target if entry.target.lower().endswith(".exe") else "")
    return ntpath.splitext(ntpath.basename(source))[0].casefold() if source else ""


@dataclass(frozen=True, slots=True)
class Match:
    kind: str                       # found | ambiguous | none
    entry: AppEntry | None = None
    candidates: tuple[AppEntry, ...] = ()


def _score(query: str, entry: AppEntry) -> int:
    name = normalize(entry.name)
    if name == query:
        return 100
    stem = executable_stem(entry)
    if stem and stem == query.replace(" ", ""):
        return 90
    q_words, n_words = query.split(), name.split()
    noisy = any(word in name for word in _NOISE) and not any(word in query for word in _NOISE)
    score = 0
    if n_words[:len(q_words)] == q_words:
        score = 80
    elif set(q_words) <= set(n_words):
        score = 70
    elif SequenceMatcher(None, query, name).ratio() >= .85:
        score = 60
    if score and noisy:
        score -= 40
    return score


class AppIndex:
    """Lazily built from a loader (the desktop adapter), refreshed when a name is missing."""

    def __init__(self, loader: Callable[[], list[AppEntry]], *, clock=None) -> None:
        self._loader = loader
        self._entries: list[AppEntry] | None = None
        self._loaded_at = float("-inf")
        self._clock = clock
        self._lock = threading.Lock()

    def prewarm(self) -> None:
        """Build the index in the background so the first "open ..." is quick."""
        threading.Thread(target=self.entries, name="aqua-app-index", daemon=True).start()

    def _now(self) -> float:
        if self._clock is not None:
            return self._clock()
        from time import monotonic
        return monotonic()

    def entries(self, *, refresh: bool = False) -> list[AppEntry]:
        with self._lock:
            return self._build() if self._entries is None or refresh else self._entries

    def _build(self) -> list[AppEntry]:
        try:
            loaded = list(self._loader())
        except Exception:
            loaded = [] if self._entries is None else self._entries
        # One entry per display name; a Start-menu shortcut wins over the Apps-folder copy.
        unique: dict[str, AppEntry] = {}
        for entry in sorted(loaded, key=lambda e: e.kind != "shortcut"):
            unique.setdefault(normalize(entry.name), entry)
        self._entries = list(unique.values())
        self._loaded_at = self._now()
        return self._entries

    def find(self, name: str) -> Match:
        query = normalize(name)
        if not query:
            return Match("none")
        match = self._find(query, self.entries())
        if match.kind == "none" and self._now() - self._loaded_at > REFRESH_SECONDS:
            match = self._find(query, self.entries(refresh=True))  # Maybe it was just installed.
        return match

    @staticmethod
    def _find(query: str, entries: list[AppEntry]) -> Match:
        scored = sorted(((score, len(normalize(e.name)), e) for e in entries if (score := _score(query, e)) > 0),
                        key=lambda item: (-item[0], item[1]))
        if not scored:
            return Match("none")
        best_score, best_length, best = scored[0]
        rivals = [e for score, length, e in scored[1:] if score == best_score and length == best_length]
        if best_score < 100 and rivals:
            return Match("ambiguous", None, (best, *rivals[:2]))
        return Match("found", best)
