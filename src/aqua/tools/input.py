"""type_text: type at the cursor of a target window, after focusing and checking it."""

import re

from aqua.agent.context import ToolContext
from aqua.agent.policy import ask, clarify, deny
from aqua.agent.registry import SAFE, Tool, ToolOutcome

from .adapter import Window
from .app_index import normalize
from .apps import window_matches_app
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
        return _launched_window(context, app) or next(iter(find_windows(context.desktop, app, titles=False)), None)
    window = _launched_window(context, None) or _named_in_request(context)
    if window is not None:
        return window
    front = context.desktop.foreground()
    return None if front is None or context.desktop.is_terminal(front) else front


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
    if not args.get("app") and _target(args, context) is None:
        return clarify("Which app should I type that into?")
    target = f" into {args['app']}" if args.get("app") else ""
    if "\n" in text or "\r" in text:
        # Aqua never presses Enter: line breaks become spaces, and the user hears it first.
        return ask(f"That has line breaks and I never press Enter, so I'll type it as one line{target}: "
                   f"'{readback(one_line(text))}'. Go ahead?")
    if not dictated(text, context.request, context.earlier):
        # Text the model wrote (a poem, an email) is read back first; dictation is typed at once.
        return ask(f"I'll type{target}: '{readback(text)}'. Go ahead?")
    return None


def one_line(text: str) -> str:
    """Line breaks as single spaces; nothing ever presses Enter. Other text is unchanged."""
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


def _run(args: dict, context: ToolContext) -> ToolOutcome:
    desktop, text = context.desktop, one_line(args["text"])
    window = _target(args, context)
    if window is None:
        return ToolOutcome("failed", f"No open window matches {args.get('app')}. Open the app first, then type.")
    name = label(window)
    if desktop.is_terminal(window):
        # Text left at a shell prompt runs on the next Enter, so never type there.
        return ToolOutcome("failed", f"Aqua does not type into terminals or the window it runs in ({name}).")
    front = desktop.foreground()
    if (front is None or front.handle != window.handle) and not desktop.focus(window.handle):
        return ToolOutcome("failed", f"Could not bring {name} to the front, so nothing was typed.")
    typed = desktop.type_text(window.handle, text)
    if typed < len(text):
        detail = "Nothing was typed" if typed == 0 else f"Only {typed} of {len(text)} characters were typed"
        return ToolOutcome("failed", f"{detail} because focus moved away from {name}.")
    readback = desktop.read_focused_text()
    if readback is not None and text not in readback:
        # The editor can be read and the exact text is not there: say so, never claim success.
        return ToolOutcome("failed", f"The text did not arrive correctly in {name}; please check it.",
                           data={"window": name})
    if readback is not None and text in readback:
        return ToolOutcome("ok", f"Typed the text into {name}.", done="typed your text", data={"window": name})
    return ToolOutcome("unverified", f"Typed the text into {name} (not verified).", done="typed your text",
                       data={"window": name})


type_text = Tool(
    name="type_text", group="input", tier=SAFE,
    description=("Write (type) text into an app window that is already open, e.g. 'write hello in Notepad'. "
                 "Focuses the window itself; never opens apps or presses Enter. Text you compose yourself "
                 "(a poem, an email) is read back to the user first; write it as one line."),
    parameters={"type": "object", "properties": {
        "text": {"type": "string", "description": "Exact text to type", "minLength": 1, "maxLength": MAX_TEXT},
        "app": {"type": "string", "description": "Window to type into, e.g. Notepad; omit for the app just opened",
                "maxLength": 100}},
        "required": ["text"]},
    run=_run, check=_check, private_args=("text",), describe=lambda args: describe_typing(args),
)
