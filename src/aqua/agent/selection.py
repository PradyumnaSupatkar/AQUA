"""Keyword-based tool-group selection. It controls visibility only, never permission."""

import re

CORE_TOOLS = ("launch_app", "type_text", "undo_last")
MAX_VISIBLE_TOOLS = 8  # OllamaClient accepts at most eight tool definitions.

GROUP_KEYWORDS = {
    "apps": ("open", "launch", "start", "run", "app", "apps", "application", "program"),
    "input": ("type", "write", "enter", "text", "dictate", "put"),
    "windows": ("window", "windows", "minimise", "minimize", "maximise", "maximize", "restore",
                "focus", "switch to", "bring up", "bring back", "front", "foreground", "hide",
                "full screen", "fullscreen", "what's open", "what is open", "open apps"),
    "system": ("volume", "louder", "quieter", "softer", "mute", "unmute", "sound", "audio",
               "loud", "quiet", "time", "clock", "date", "day", "battery", "charge", "charging",
               "power left", "music", "song", "track", "play", "pause", "resume", "skip", "next",
               "previous", "media", "playback"),
    "power": ("lock", "sleep", "restart", "reboot", "shut down", "shutdown", "sign out",
              "log off", "log out"),
    "web": ("search", "google", "look up", "website", "browse", "url", "http"),
    "undo": ("undo", "revert", "take back", "put it back", "change it back"),
}
GROUP_ORDER = ("apps", "input", "windows", "system", "power", "web", "undo")

_PATTERNS = {group: re.compile(r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b", re.IGNORECASE)
             for group, words in GROUP_KEYWORDS.items()}

# Requests that ask for no action at all: a leading "don't ...", or a
# hypothetical ("what would happen if ..."). Tools are hidden for these, so the
# model can only talk. Hiding tools can only remove capability, never add it.
_NEGATED = re.compile(r"^\W*(?:(?:hey\s+)?aqua\W+)?(?:please\s+)?(?:don'?t|do\s+not|never|no\s+need\s+to)\b", re.IGNORECASE)
_REDIRECT = re.compile(r"\b(?:instead|but|rather|then)\b", re.IGNORECASE)
_HYPOTHETICAL = re.compile(
    r"\bwhat\s+(?:would|will|could|might)\s+(?:happen|you\s+do)\b|\bwhat\s+happens\s+(?:if|when)\b|"
    r"\bwhat\s+if\b|\bhypothetical(?:ly)?\b|\bif\s+i\s+(?:said|asked|told|were\s+to|wanted)\b|"
    r"\bwould\s+you\s+(?:be\s+able\s+to|ever)\b|\bimagine\b|\bpretend\b",
    re.IGNORECASE)


def wants_no_action(text: str) -> bool:
    text = text.replace("’", "'")
    if _HYPOTHETICAL.search(text):
        return True
    return bool(_NEGATED.search(text)) and not _REDIRECT.search(text)


def matched_groups(text: str) -> tuple[str, ...]:
    text = text.replace("’", "'")
    return tuple(group for group in GROUP_ORDER if _PATTERNS[group].search(text))


def select_tools(text: str, registry) -> tuple[str, ...]:
    """The core tools plus every group whose keywords appear, capped at eight."""
    if wants_no_action(text):
        return ()
    names = [name for name in CORE_TOOLS if registry.get(name) is not None]
    for group in matched_groups(text):
        for name in registry.in_group(group):
            if name not in names:
                names.append(name)
    return tuple(names[:MAX_VISIBLE_TOOLS])
