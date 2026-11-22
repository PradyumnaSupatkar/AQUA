"""type_text: type at the cursor of a target window, after focusing and checking it."""

from aqua.agent.context import ToolContext
from aqua.agent.policy import clarify, deny
from aqua.agent.registry import SAFE, Tool, ToolOutcome

from .adapter import Window
from .app_index import normalize
from .apps import window_matches_app
from .windows import find_windows, label

MAX_TEXT = 1000


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
    window = _launched_window(context, None)
    if window is not None:
        return window
    front = context.desktop.foreground()
    return None if front is None or context.desktop.is_terminal(front) else front


def _check(args: dict, context: ToolContext):
    text = args["text"]
    if "\n" in text or "\r" in text:
        return deny("Aqua never presses Enter, so it cannot type line breaks. Type it as one line instead.")
    if any(ord(c) < 32 and c != "\t" or ord(c) == 127 for c in text):
        return deny("The text contains control characters.")
    if not args.get("app") and _target(args, context) is None:
        return clarify("Which app should I type that into?")
    return None


def _run(args: dict, context: ToolContext) -> ToolOutcome:
    desktop, text = context.desktop, args["text"]
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
    if readback is not None and text in readback:
        return ToolOutcome("ok", f"Typed the text into {name}.", done="typed your text", data={"window": name})
    return ToolOutcome("unverified", f"Typed the text into {name} (not verified).", done="typed your text",
                       data={"window": name})


type_text = Tool(
    name="type_text", group="input", tier=SAFE,
    description=("Write (type) text into an app window that is already open, e.g. 'write hello in Notepad'. "
                 "Focuses the window itself; never opens apps or presses Enter."),
    parameters={"type": "object", "properties": {
        "text": {"type": "string", "description": "Exact text to type", "minLength": 1, "maxLength": MAX_TEXT},
        "app": {"type": "string", "description": "Window to type into, e.g. Notepad; omit for the app just opened",
                "maxLength": 100}},
        "required": ["text"]},
    run=_run, check=_check, private_args=("text",),
)
