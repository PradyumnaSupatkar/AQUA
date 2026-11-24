"""type_text: type at the cursor of a target window, after focusing and checking it."""

import re

from aqua.agent.context import ToolContext
from aqua.agent.policy import ask, clarify, deny
from aqua.agent.registry import SAFE, Tool, ToolOutcome

from .adapter import Window
from .app_index import normalize
from .apps import app_named, window_matches_app
from .windows import find_windows, label

MAX_TEXT = 1000
READBACK_CHARS = 200  # Longer text is read back shortened, with its word count.


def readback(text: str) -> str:
    if len(text) <= READBACK_CHARS:
        return text
    return text[:READBACK_CHARS].rsplit(" ", 1)[0] + f"... ({len(text.split())} words)"


def describe_typing(args: dict) -> str:
    target = f" into {args['app']}" if args.get("app") else ""
    return f"type '{readback(args['text'])}'{target}"


def _launched_window(context: ToolContext, app: str | None) -> Window | None:
    """The newest window Aqua opened in this request (matching ``app`` if given)."""
    open_windows = {w.handle: w for w in context.desktop.windows()}
    for launch in reversed(context.launches):
        window = open_windows.get(launch.handle)
        if window is not None and (app is None or window_matches_app(window, app)
                                   or normalize(app) == normalize(launch.app)):
            return window
    return None


def _target(args: dict, context: ToolContext) -> Window | None:
    app = args.get("app")
    if app:
        for name in dict.fromkeys((app, clean_app(app))):  # "the notepad window" -> "notepad"
            window = _launched_window(context, name) or next(iter(find_windows(context.desktop, name, titles=False)),
                                                             None)
            if window is not None:
                return window
        return None
    window = _launched_window(context, None) or _named_in_request(context)
    if window is not None:
        return window
    requested = requested_app(context)
    if requested is not None:
        return _launched_window(context, requested) or next(iter(find_windows(context.desktop, requested,
                                                                              titles=False)), None)
    front = context.desktop.foreground()
    return None if front is None or context.desktop.is_terminal(front) else front


# Where to type: "into the notepad window", "in notepad", "to my notepad", "inside VS Code".
_PLACE = re.compile(r"\b(?:into|in|on|onto|to|inside)\s+(?:the\s+|my\s+|a\s+|your\s+|our\s+)?(?:new\s+|open\s+)?",
                    re.I)
_PLACE_FILLER = frozenset({"the", "my", "a", "your", "our", "new", "open", "window", "app", "application", "file",
                           "document", "tab", "editor", "please", "now", "for", "me"})


def clean_app(app: str) -> str:
    """A spoken target without filler: "the notepad window" -> "notepad"."""
    words = _plain(app).split()
    while words and words[0] in _PLACE_FILLER:
        words.pop(0)
    while words and words[-1] in _PLACE_FILLER:
        words.pop()
    return " ".join(words) or app


def requested_app(context: ToolContext) -> str | None:
    """The installed app the request names as the place to type, open or not, e.g. Notepad
    for "write hello into the notepad window". The name must appear in the request itself."""
    for said in (context.request, *reversed(context.earlier)):
        for match in reversed(list(_PLACE.finditer(said))):
            words = clean_app(said[match.end():]).split()
            for size in (3, 2, 1):
                candidate = [w for w in words[:size]]
                while candidate and candidate[-1] in _PLACE_FILLER:
                    candidate.pop()
                for name in dict.fromkeys((" ".join(candidate), "".join(candidate))):
                    if not name:
                        continue
                    found = context.apps.find(name)
                    if found.kind == "found" and found.entry is not None and app_named(said, name, found.entry):
                        return found.entry.name
    return None


# "write meeting at noon" / "type hello": dictation, as opposed to "write a poem" (composing).
_DICTATE = re.compile(r"^(?:(?:please|now|just|ok|okay)\s+|(?:could|can|would|will)\s+you\s+(?:please\s+)?)*"
                      r"(?:write|type)(?:\s+(?:down|out))?\s+(?P<text>.+?)\s*[.!]?$", re.I)
_COMPOSE = re.compile(r"^(?:a|an|me|some|the|my|your|another|something|anything|this|that|it|what|how|why|"
                      r"about|up|back|to|for|in|on|into)\b", re.I)


def unplaced_dictation(context: ToolContext) -> str | None:
    """The words of "write <text>" when nothing says where: no app named, no window opened or
    named, and nothing to type into in front. The host then asks which app (and keeps the text)."""
    match = _DICTATE.match(context.request.strip())
    if match is None or _COMPOSE.match(match.group("text")) or _PLACE.search(match.group("text")):
        return None
    if requested_app(context) is not None or _target({"text": match.group("text")}, context) is not None:
        return None
    return match.group("text")


def _named_in_request(context: ToolContext) -> Window | None:
    """An open window whose app the user named ("... into the notepad window"), front-most first,
    for when the model left out the app."""
    tokens = _plain(" ".join((*context.earlier, context.request))).split()
    words = f" {' '.join(tokens)} "
    joined = {"".join(tokens[i:i + n]) for n in (2, 3) for i in range(len(tokens) - n + 1)}
    for window in context.desktop.windows():
        if context.desktop.is_terminal(window):
            continue
        names = {window.process.rsplit(".", 1)[0].casefold(), _plain(label(window))}
        if any(name and (f" {name} " in words or name.replace(" ", "") in joined) for name in names):
            return window
    return None


def _check(args: dict, context: ToolContext):
    text = args["text"]
    if any((ord(c) < 32 and c not in "\t\r\n") or ord(c) == 127 for c in text):
        return deny("The text contains control characters.")
    app = args.get("app") or requested_app(context)
    if not app and _target(args, context) is None:
        return clarify("Which app should I type that into?")
    target = f" into {app}" if app else ""
    if "\n" in text or "\r" in text or not dictated(text, context.request, context.earlier):
        # Text the model wrote (a poem, an email) is read back first; dictation is typed at once.
        return ask(*read_back(text, context.request, target, keep_lines=plain_editor(args, context)))
    return None


SPOKEN_WORDS = 12  # Longer text is summarised when spoken; the full text is shown on screen.
# Plain editors take Enter as a line break; elsewhere Enter can send a message or run a command.
PLAIN_EDITORS = frozenset({"notepad.exe", "code.exe"})
_PLAIN_EDITOR_NAMES = frozenset({"notepad", "vs code", "vscode", "visual studio code", "code"})
_WHAT = re.compile(r"\b(?:write|type|compose|draft|put|jot|add)\s+(?:down\s+|out\s+|up\s+)?(?:me\s+)?(.+)", re.I)
# The place at the end: "... into (the) Notepad (window)". Only the last such phrase, so
# "an email to my boss in Notepad" keeps "to my boss".
_WHERE = re.compile(r"\s+(?:into|in|on|onto|to)\s+(?:the\s+|my\s+|a\s+)?(?:new\s+)?"
                    r"(?:(?!(?:into|in|on|onto|to)\b)[\w.]+\s*){1,3}?(?:\s*(?:window|app|file|document|tab))?\s*$",
                    re.I)


def plain_editor(args: dict, context: ToolContext) -> bool:
    window = _target(args, context)
    if window is not None:
        return window.process.casefold() in PLAIN_EDITORS
    return normalize(args.get("app") or "") in _PLAIN_EDITOR_NAMES


def what_to_type(request: str) -> str:
    """"write a short poem about rain into Notepad" -> "a short poem about rain"; "" if unclear."""
    match = _WHAT.search(request)
    if match is None:
        return ""
    words = _WHERE.sub("", match.group(1).strip().rstrip(".!?"), count=1).strip(" ,")
    return words if 0 < len(words.split()) <= 10 else ""


def read_back(text: str, request: str, target: str, *, keep_lines: bool) -> tuple[str, str]:
    """(spoken question, text shown on screen). Short one-line text is read in full; longer
    text is summarised ("I'll type a short poem about rain, 38 words. Go ahead?") and shown."""
    lines = [part.strip() for part in _lines(text).split("\n") if part.strip()]
    breaks = len(lines) > 1
    shown = "\n".join(lines) if keep_lines else one_line(text)
    if not breaks and len(text.split()) <= SPOKEN_WORDS:
        return f"I'll type{target}: '{text}'. Go ahead?", ""
    count = len(text.split())
    what = what_to_type(request) or "the text"
    layout = f", on {len(lines)} lines" if breaks and keep_lines else ", as one line" if breaks else ""
    return f"I'll type {what}{target}, {count} word{'s' if count != 1 else ''}{layout}. Go ahead?", shown


def one_line(text: str) -> str:
    """Line breaks as single spaces, for apps where Enter could send or run something."""
    if "\n" not in text and "\r" not in text:
        return text
    return " ".join(part.strip() for part in text.replace("\r", "\n").split("\n") if part.strip())


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold().replace("’", "'").replace("'", "")).split())


def dictated(text: str, request: str, earlier: tuple[str, ...] = ()) -> bool:
    """The text to type is the user's own words (ignoring case, punctuation and spacing), in
    this request or one it answers ("write X into notepad" -> "Which app?" -> "Notepad")."""
    words = _plain(text)
    return bool(words) and any(f" {words} " in f" {_plain(said)} " for said in (request, *earlier))


def _lines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def inserted(before: str | None, after: str, text: str) -> bool:
    """The typed text went in exactly at one place (the caret) and nothing else changed:
    ``after`` is ``before`` with ``text`` inserted. Without a "before" reading, the text
    must at least appear in the document."""
    after, text = _lines(after), _lines(text)
    if before is None:
        return text in after
    before = _lines(before)
    if len(after) != len(before) + len(text):
        return False
    index = after.find(text)
    while index != -1:
        if after[:index] + after[index + len(text):] == before:
            return True
        index = after.find(text, index + 1)
    return False


def _run(args: dict, context: ToolContext) -> ToolOutcome:
    desktop = context.desktop
    window, opened, undo = _target(args, context), "", None
    if window is None:
        # The app was named ("into the notepad window") but is not open: open it, then type.
        window, opened, undo, failure = _open_for_typing(args, context)
        if failure:
            return ToolOutcome("failed", failure)
    plain = window.process.casefold() in PLAIN_EDITORS
    # Line ends normalised in plain editors (each line break is one Enter press); one line elsewhere.
    text = _lines(args["text"]) if plain else one_line(args["text"])
    name = label(window)
    if desktop.is_terminal(window):
        # Text left at a shell prompt runs on the next Enter, so never type there.
        return ToolOutcome("failed", f"Aqua does not type into terminals or the window it runs in ({name}).")
    front = desktop.foreground()
    if (front is None or front.handle != window.handle) and not desktop.focus(window.handle):
        return ToolOutcome("failed", f"Could not bring {name} to the front, so nothing was typed.")
    before = desktop.read_focused_text()  # The document may already hold text (e.g. restored tabs).
    if plain and before and not before[-1].isspace() and text[:1] and not text[0].isspace():
        # "meeting at noon" + "hello" must not become "meeting at noonhello": a new line first.
        # (The caret is taken to be at the end of the document, where Notepad puts it.)
        text = "\n" + text
    typed = desktop.type_text(window.handle, text)
    if typed < len(text):
        detail = "Nothing was typed" if typed == 0 else f"Only {typed} of {len(text)} characters were typed"
        return ToolOutcome("failed", f"{detail} because focus moved away from {name}.")
    readback = desktop.read_focused_text()
    if readback is not None and not inserted(before, readback, text):
        # The editor can be read and the text did not go in exactly: say so, never claim success.
        return ToolOutcome("failed", f"The text did not arrive correctly in {name}; please check it.",
                           data={"window": name})
    lead = f"Opened {name}, then typed" if opened else "Typed"
    done = f"{opened} and typed your text" if opened else f"typed your text into {name}"
    if readback is not None:
        return ToolOutcome("ok", f"{lead} the text into {name}.", done=done, data={"window": name}, undo=undo)
    return ToolOutcome("unverified", f"{lead} the text into {name} (not verified).",
                       done=f"{opened} and typed your text" if opened else "typed your text",
                       data={"window": name}, undo=undo)


def _open_for_typing(args: dict, context: ToolContext):
    """(window, done phrase, undo, failure message) after opening the named app to type into."""
    from aqua.agent.policy import CLARIFY, DENY

    from .apps import launch_app
    app = clean_app(args["app"]) if args.get("app") else requested_app(context)
    if not app or context.apps.find(app).entry is None:
        return None, "", None, f"No open window matches {args.get('app')}. Open the app first, then type."
    decision = launch_app.check({"app": app}, context)
    if decision is not None and decision.action in {DENY, CLARIFY}:
        return None, "", None, f"Did not open {app}: {decision.reason or decision.question}"
    launched = launch_app.run({"app": app}, context)
    window = _launched_window(context, None) if launched.succeeded else None
    if window is None:
        return None, "", None, f"{app} did not open a window to type into, so nothing was typed."
    return window, launched.done, launched.undo, ""


type_text = Tool(
    name="type_text", group="input", tier=SAFE,
    description=("Write (type) text into an app window that is already open, e.g. 'write hello in Notepad'. "
                 "Focuses the window itself; never opens apps. Text you compose yourself (a poem, an email) "
                 "is read back to the user first. Line breaks are kept only in Notepad and VS Code."),
    parameters={"type": "object", "properties": {
        "text": {"type": "string", "description": "Exact text to type", "minLength": 1, "maxLength": MAX_TEXT},
        "app": {"type": "string", "description": "Window to type into, e.g. Notepad; omit for the app just opened",
                "maxLength": 100}},
        "required": ["text"]},
    run=_run, check=_check, private_args=("text",), describe=lambda args: describe_typing(args),
)
