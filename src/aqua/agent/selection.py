"""Keyword-based tool-group selection. It controls visibility only, never permission."""

import re

CORE_TOOLS = ("launch_app", "type_text", "undo_last")
MAX_VISIBLE_TOOLS = 8  # OllamaClient accepts at most eight tool definitions.

GROUP_KEYWORDS = {
    "apps": ("open", "launch", "start", "run", "app", "apps", "application", "program", "close", "quit",
             "exit", "kill", "force", "frozen", "not responding"),
    "input": ("type", "write", "enter", "text", "dictate", "put"),
    "windows": ("window", "windows", "minimise", "minimize", "maximise", "maximize", "restore",
                "focus", "switch to", "bring up", "bring back", "front", "foreground", "hide",
                "full screen", "fullscreen", "what's open", "what is open", "open apps", "inspect",
                "buttons", "button", "menu", "menus", "tabs", "controls", "what's on", "what is on"),
    "system": ("time", "clock", "date", "day", "battery", "charge", "charging", "power left"),
    "audio": ("volume", "louder", "quieter", "softer", "mute", "unmute", "sound", "audio", "loud", "quiet",
              "music", "song", "track", "play", "pause", "resume", "skip", "next", "previous", "media",
              "playback", "video", "podcast"),
    "display": ("brightness", "brighter", "dimmer", "dim", "screen bright", "too bright", "too dark", "darker"),
    "power": ("lock", "sleep", "restart", "reboot", "shut down", "shutdown", "sign out", "log off", "log out",
              "turn off the computer", "turn off my computer", "turn off the pc", "power off"),
    "web": ("search", "google", "website", "web site", "site", "browse", "browser", "url", "link", "http",
            "https", "web", "online"),
    "knowledge": ("who", "what", "when", "where", "which", "why", "how many", "how much", "how old", "how tall",
                  "how long", "how far", "champion", "championship", "championships", "won", "win", "winner",
                  "population", "capital", "born", "died", "founded", "invented", "discovered", "president",
                  "prime minister", "wikipedia", "look up", "tell me about", "history of", "fact", "facts",
                  "record", "tallest", "largest", "biggest", "oldest", "first"),
    "undo": ("undo", "revert", "take back", "put it back", "change it back"),
}
GROUP_ORDER = ("apps", "input", "windows", "system", "audio", "display", "power", "web", "knowledge", "undo")

_PATTERNS = {group: re.compile(r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b", re.IGNORECASE)
             for group, words in GROUP_KEYWORDS.items()}
# A bare domain ("open bbc.co.uk") also selects the web tools.
_DOMAIN = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|gov|edu|co|uk|in|de|dev|app|info|ai|me|tv)\b",
                     re.IGNORECASE)

# Requests that ask for no action at all: a leading "don't ...", or a
# hypothetical ("what would happen if ..."). Tools are hidden for these, so the
# model can only talk. Hiding tools can only remove capability, never add it.
# "Never mind ..." and "don't forget to ..." are not negations: they precede a request.
_NEGATED = re.compile(
    r"^\W*(?:(?:hey\s+)?aqua\W+)?(?:please\s+)?"
    r"(?:(?:don'?t|do\s+not)(?!\s+forget\b)|never(?!\s+mind\b)|no\s+need\s+to)\b", re.IGNORECASE)
_REDIRECT = re.compile(r"\b(?:instead|but|rather|then)\b", re.IGNORECASE)
# Polite requests ("would you be able to open Chrome?") are requests, not hypotheticals.
_HYPOTHETICAL = re.compile(
    r"\bwhat\s+(?:would|will|could|might)\s+(?:happen|you\s+do)\b|\bwhat\s+happens\s+(?:if|when)\b|"
    r"\bwhat\s+if\b|\bhypothetical(?:ly)?\b|\bif\s+i\s+(?:said|asked|told|were\s+to|wanted)\b|"
    r"\bimagine\b|\bpretend\b",
    re.IGNORECASE)


def wants_no_action(text: str) -> bool:
    text = text.replace("’", "'")
    if _HYPOTHETICAL.search(text):
        return True
    return bool(_NEGATED.search(text)) and not _REDIRECT.search(text)


def matched_groups(text: str) -> tuple[str, ...]:
    text = text.replace("’", "'")
    return tuple(group for group in GROUP_ORDER
                 if _PATTERNS[group].search(text) or (group == "web" and _DOMAIN.search(text)))


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
