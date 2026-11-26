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
            "https", "web", "online", "youtube", "amazon", "github"),
    "knowledge": ("who", "what", "when", "where", "which", "why", "how many", "how much", "how old", "how tall",
                  "how long", "how far", "champion", "championship", "championships", "won", "win", "winner",
                  "population", "capital", "born", "died", "founded", "invented", "discovered", "president",
                  "prime minister", "wikipedia", "look up", "tell me about", "history of", "fact", "facts",
                  "record", "tallest", "largest", "biggest", "oldest", "first"),
    "undo": ("undo", "revert", "take back", "put it back", "change it back"),
    # Aqua's local memory (remember, forget, recall).
    "memory": ("remember", "forget", "from now on", "about me", "know about me", "by default", "my favourite",
               "my favorite", "i prefer", "call me", "my name is", "keep in mind", "going forward", "in future"),
    # Pages of Windows Settings (open_settings): things Windows lets only the user change.
    "settings": ("settings", "default browser", "default app", "default apps", "default program",
                 "bluetooth", "wifi", "wi-fi", "notifications", "notification", "windows update", "updates",
                 "control panel", "display settings", "sound settings", "power settings", "battery settings"),
    # YouTube in the browser, through the Aqua extension.
    "browser": ("youtube", "you tube", "video", "videos", "captions", "caption", "subtitles", "subtitle", "cc",
                "fullscreen", "full screen", "quality", "resolution", "1080p", "720p", "480p", "1440p", "4k",
                "playback speed", "speed", "faster", "slower", "skip ad", "skip the ad", "skip this ad", "ad",
                "ads", "what's playing", "what is playing", "now playing", "rewind", "fast forward",
                "seek", "jump to", "go back", "first one", "second one", "third one", "fourth one", "fifth one",
                "result", "results", "next video", "previous video", "channel",
                # Seeking is said without naming the video: "skip ahead 30 seconds", "jump to 1:30".
                "skip ahead", "skip forward", "skip back", "go forward", "minute mark", "seconds", "jump"),
    # Bare "pause"/"play" stay with the media keys (which pause YouTube too); "pause the video" adds youtube.
}
GROUP_ORDER = ("apps", "input", "browser", "windows", "system", "audio", "display", "power", "web", "knowledge",
               "undo", "settings", "memory")

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


# A direct command: an imperative verb first, after polite openers ("could you", "please",
# "don't forget to"). "lock the screen" is asked for, whatever the model marked it.
_POLITE_LEAD = re.compile(
    r"^(?:(?:hey|hi|ok|okay)\s+aqua\b[,\s]*)?(?:(?:please|kindly|just|now|quickly)\s+|"
    r"(?:could|can|would|will)\s+you\s+(?:please\s+)?(?:be\s+able\s+to\s+)?|don'?t\s+forget\s+to\s+|"
    r"go\s+ahead\s+and\s+|i\s+(?:want|need)\s+you\s+to\s+)*", re.I)
_DIRECT_VERB = re.compile(
    r"(?:open|launch|start|lock|mute|unmute|turn|set|close|quit|exit|minimi[sz]e|maximi[sz]e|restore|bring|switch|"
    r"pause|play|resume|skip|search|undo|raise|lower|dim|brighten|increase|decrease|type|write|put|show|go|"
    r"shut|restart|reboot|sign|log|force|kill|stop|rewind|jump|seek|fast|speed|enable|disable|replay|rewatch)\b", re.I)


def direct_command(request: str) -> bool:
    text = request.strip().casefold().replace("’", "'")
    return bool(_DIRECT_VERB.match(text[_POLITE_LEAD.match(text).end():]))


_CLAUSE_BREAK = re.compile(r"\s*(?:[,;?]|\band\b|\bthen\b)\s*", re.IGNORECASE)


def wants_explanation(text: str) -> bool:
    """A how/what/why question, unless a later clause is a command ("what's playing? and pause it")."""
    text = text.replace("’", "'")
    if not _EXPLAIN.search(text):
        return False
    return not any(direct_command(part) for part in _CLAUSE_BREAK.split(text)[1:] if part.strip())


_QUESTION_START = re.compile(r"^\W*(?:(?:hey\s+)?aqua\W+)?(?:what|how|is|are|was|does|do|which|why|when|where)\b",
                             re.IGNORECASE)


def asks_question(text: str) -> bool:
    text = text.replace("’", "'").strip()
    return text.endswith("?") or bool(_QUESTION_START.search(text))


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


def _has_reading(registry, name: str) -> bool:
    tool = registry.get(name)
    return tool is not None and callable(tool.tier) and tool.group == "browser"


# Replaying the video: "replay (the video)", "restart the video", "start (it) over", "rewatch it",
# "play it again (from the beginning)", "play the video from the start".
_VIDEO_WORDS = re.compile(r"\b(?:video|you\s?tube|clip)\b", re.I)
_REPLAY = re.compile(
    r"\b(?:replay|rewatch)\b|\bstart\s+(?:it\s+|this\s+|that\s+|the\s+video\s+)?(?:over|again)\b|"
    r"\bplay\s+(?:it|this|that|the\s+video|this\s+video)\s+(?:again|(?:over\s+)?from\s+the\s+(?:beginning|start|top))\b|"
    r"\brestart\s+(?:it|this|that|the\s+video|this\s+video|the\s+youtube\s+video)\b|"
    r"\b(?:play|watch)\b[^.?!]*\bfrom\s+the\s+(?:very\s+)?(?:beginning|start|top)\b", re.I)
# "restart the computer" and the like are never a video replay.
_NOT_VIDEO = re.compile(r"\b(?:computer|pc|laptop|windows|system|machine|device|router|app|program|song|track|"
                        r"music|podcast|spotify|playlist)\b", re.I)


def replay_request(request: str, *, video_active: bool) -> bool:
    """The user's words ask to replay the video: they name the video, or a YouTube tab is
    connected (``video_active``) and nothing else is named ("replay", "start it over")."""
    text = request.replace("’", "'")
    if not _REPLAY.search(text) or _NOT_VIDEO.search(text):
        return False
    return bool(_VIDEO_WORDS.search(text)) or video_active


def replay_groups(text: str, groups: tuple[str, ...], *, video_active: bool) -> tuple[str, ...]:
    """A video replay is the youtube tool's alone: never the power tools ("restart the video") nor
    the media keys (live: "yes replay" pressed play on no media session while YouTube played)."""
    if not replay_request(text, video_active=video_active):
        return groups
    return ("browser", *(group for group in groups if group not in {"browser", "power", "audio", "apps"}))


# A lasting preference or fact the user states ("always open YouTube in Brave", "from now on ...",
# "remember that ...", "my projects are in D:\Projects"). Such a statement is saved, not acted on:
# only the memory tools are shown, so "always open YouTube in Brave" cannot open YouTube now.
_LASTING = re.compile(
    r"^\W*(?:(?:hey\s+)?aqua\W+)?(?:please\s+)?(?:always|from\s+now\s+on|by\s+default|in\s+(?:the\s+)?future|"
    r"going\s+forward|remember\s+(?:that|this|:)|keep\s+in\s+mind|note\s+that|for\s+future\s+reference)\b|"
    r"\bfrom\s+now\s+on\b", re.IGNORECASE)
_FACT = re.compile(r"^\W*(?:remember\s+)?(?:my|our)\s+[\w' ]{2,40}?\s+(?:is|are|was|lives?|sits?)\b|"
                   r"^\W*(?:remember\s+)?i\s+(?:live|work|study)\s+(?:in|at|for)\b|^\W*(?:remember\s+)?call\s+me\b",
                   re.IGNORECASE)


def wants_memory(text: str) -> bool:
    """A statement to remember, not an action or a question. A plain "my X is Y" counts only when
    no other tool group matches ("my screen is too bright" stays a brightness request)."""
    text = text.replace("’", "'").strip()
    if asks_question(text) or wants_no_action(text):
        return False
    if _LASTING.search(text):
        return True
    return bool(_FACT.search(text)) and not (set(matched_groups(text)) - {"memory", "system", "knowledge"})


def select_tools(text: str, registry, *, negation_guard: bool = True, question_guard: bool = True,
                 previous: tuple[str, ...] = (), video_active: bool = False) -> tuple[str, ...]:
    """The core tools plus every selected group's tools, capped at eight. ``video_active``: a
    YouTube tab is connected, so "replay" or "start it over" means the video."""
    if negation_guard and wants_no_action(text):
        return ()
    if registry.in_group("memory") and wants_memory(text):
        return tuple(n for n in registry.in_group("memory") if n != "recall") + ("recall",)
    names = [name for name in CORE_TOOLS if registry.get(name) is not None]
    groups = replay_groups(text, selected_groups(text, previous), video_active=video_active)
    if "memory" in groups and re.search(r"\bdon'?t\s+forget\s+to\b", text.replace("’", "'"), re.I):
        groups = tuple(group for group in groups if group != "memory")  # A reminder phrasing, not memory.
    if "memory" in groups:
        # Remembering, forgetting or recalling: those tools first, so the cap never drops them.
        names += [name for name in registry.in_group("memory")]
    for group in groups:
        for name in registry.in_group(group):
            if name not in names:
                names.append(name)
    if set(groups) & _STATUS_GROUPS and registry.get("device_status") is not None and asks_question(text):
        # Only a status question ("what's the volume?") needs the reading; for a command
        # ("it's too loud") the model then just reports the state instead of acting.
        names.append("device_status")
    if not groups:
        # No keyword matched (misheard words, unusual phrasing): offer the likely actions
        # rather than only the core three. Visibility still grants no permission.
        names += [name for name in FALLBACK_TOOLS if registry.get(name) is not None and name not in names]
    if question_guard and wants_explanation(text):
        # Read-only tools, and tools with a read-only action (youtube's now_playing): the loop
        # makes any other call of theirs "unsure", so the host asks first.
        names = [name for name in names if _read_only(registry, name) or _has_reading(registry, name)]
    names = list(dict.fromkeys(names))
    # The cap keeps read-only status tools: they never act, and status answers need them.
    keep = [n for n in names if _read_only(registry, n)][:2]
    rest = [n for n in names if n not in keep]
    return tuple(n for n in names if n in keep or n in rest[:MAX_VISIBLE_TOOLS - len(keep)])
