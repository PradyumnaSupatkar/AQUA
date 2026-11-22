"""Keyword-based tool-group selection. It controls visibility only, never permission."""

import re

CORE_TOOLS = ("launch_app", "type_text", "undo_last")
# Shown beside the core tools when no keyword group matches.
FALLBACK_TOOLS = ("volume", "media", "window_action", "power", "look_up")
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


# Questions asking how/what/why about doing something want an explanation, not the action:
# "how do I lock my screen?", "what does force quit do?", "do you know how to restart a PC?".
# They see only read-only tools (time, battery, windows, look-up), so they cannot act; an
# offer the model makes in its answer still becomes a host yes/no question. "How about ...",
# "why don't you ..." are suggestions, i.e. requests, and are excluded.
_EXPLAIN = re.compile(
    r"^\W*(?:(?:hey\s+)?aqua\W+)?(?:please\s+)?(?:"
    r"how(?!\s+about\b)|what|why(?!\s+(?:don'?t|not|won'?t)\b)|"
    r"(?:do|does)\s+(?:you|anyone)\s+know\s+(?:how|what|why)|"
    r"(?:can|could)\s+you\s+(?:explain|tell\s+me)\s+(?:how|what|why)|"
    r"is\s+there\s+(?:a|any)\s+way\s+to"
    r")\b", re.IGNORECASE)
# Device-state tools that a status question needs beside its group's action tools.
_STATUS_GROUPS = {"audio", "display"}


def wants_explanation(text: str) -> bool:
    return bool(_EXPLAIN.search(text.replace("’", "'")))


def matched_groups(text: str) -> tuple[str, ...]:
    text = text.replace("’", "'")
    return tuple(group for group in GROUP_ORDER
                 if _PATTERNS[group].search(text) or (group == "web" and _DOMAIN.search(text)))


def selected_groups(text: str, previous: tuple[str, ...] = ()) -> tuple[str, ...]:
    """The request's keyword groups; a follow-up with none ("check again", "yes", "do it
    again") inherits the previous request's groups."""
    return matched_groups(text) or tuple(previous)


def _read_only(registry, name: str) -> bool:
    tool = registry.get(name)
    return tool is not None and not callable(tool.tier) and tool.tier == 0


def select_tools(text: str, registry, *, negation_guard: bool = True, question_guard: bool = True,
                 previous: tuple[str, ...] = ()) -> tuple[str, ...]:
    """The core tools plus every selected group's tools, capped at eight."""
    if negation_guard and wants_no_action(text):
        return ()
    names = [name for name in CORE_TOOLS if registry.get(name) is not None]
    groups = selected_groups(text, previous)
    for group in groups:
        for name in registry.in_group(group):
            if name not in names:
                names.append(name)
    if set(groups) & _STATUS_GROUPS and registry.get("device_status") is not None:
        names.append("device_status")
    if not groups:
        # No keyword matched (misheard words, unusual phrasing): offer the likely actions
        # rather than only the core three. Visibility still grants no permission.
        names += [name for name in FALLBACK_TOOLS if registry.get(name) is not None and name not in names]
    if question_guard and wants_explanation(text):
        names = [name for name in names if _read_only(registry, name)]
    names = list(dict.fromkeys(names))
    # The cap keeps read-only status tools: they never act, and status answers need them.
    keep = [n for n in names if _read_only(registry, n)][:2]
    rest = [n for n in names if n not in keep]
    return tuple(n for n in names if n in keep or n in rest[:MAX_VISIBLE_TOOLS - len(keep)])
