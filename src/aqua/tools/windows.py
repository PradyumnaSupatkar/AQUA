"""Window tools: list_windows and window_action (focus, minimise, maximise, restore)."""

from aqua.agent.context import ToolContext
from aqua.agent.registry import READ, SAFE, Tool, ToolOutcome, UndoRecord

from .adapter import Window
from .apps import window_matches_app

MAX_LISTED = 12
ACTIONS = ("focus", "minimise", "maximise", "restore")
_SPELLING = {"minimize": "minimise", "maximize": "maximise"}
_PAST = {"focus": "brought {} to the front", "minimise": "minimised {}", "maximise": "maximised {}",
         "restore": "restored {}"}


def find_windows(desktop, query: str, *, titles: bool = True) -> list[Window]:
    """Windows of the named app, front to back. With ``titles``, fall back to
    windows whose title contains the words (never used for typing)."""
    windows = desktop.windows()
    matches = [w for w in windows if window_matches_app(w, query)]
    folded = " ".join(query.casefold().split())
    if matches or not titles or len(folded) < 3:
        return matches
    return [w for w in windows if folded in w.title.casefold()]


def label(window: Window) -> str:
    title = window.title.strip()
    if " - " in title:
        title = title.rsplit(" - ", 1)[-1]  # "Untitled - Notepad" -> "Notepad"
    return title[:60] or window.process.rsplit(".", 1)[0]


def _list(args: dict, context: ToolContext) -> ToolOutcome:
    windows = [w for w in context.desktop.windows() if not context.desktop.is_terminal(w)][:MAX_LISTED]
    if not windows:
        return ToolOutcome("empty", "No app windows are open.")
    rows = [{"title": w.title[:80], "app": w.process.rsplit(".", 1)[0],
             **({"state": "minimised"} if w.minimized else {}), **({"focused": True} if w.foreground else {})}
            for w in windows]
    return ToolOutcome("ok", f"{len(rows)} windows are open.", data={"windows": rows})


def _state(window: Window) -> str:
    return "minimise" if window.minimized else "maximise" if window.maximized else "restore"


def _action(args: dict, context: ToolContext) -> ToolOutcome:
    desktop, action = context.desktop, _SPELLING.get(args["action"], args["action"])
    matches = find_windows(desktop, args["window"])
    if not matches:
        return ToolOutcome("failed", f"No open window matches {args['window']}.")
    window, name = matches[0], label(matches[0])
    previous_state = _state(window)
    previous_front = desktop.foreground()
    if action == "focus":
        if not desktop.focus(window.handle):
            return ToolOutcome("failed", f"Windows did not let Aqua bring {name} to the front.")
    else:
        desktop.show(window.handle, action)
    handle = window.handle

    def undo() -> str:
        desktop.show(handle, previous_state)
        if previous_front is not None and previous_front.handle != handle and action in {"focus", "maximise", "restore"}:
            desktop.focus(previous_front.handle)
        return f"put {name} back"

    return ToolOutcome("ok", _PAST[action].format(name).capitalize() + ".", done=_PAST[action].format(name),
                       undo=UndoRecord(f"{action} {name}", undo))


list_windows = Tool(
    name="list_windows", group="windows", tier=READ,
    description="List the open app windows.",
    parameters={"type": "object", "properties": {}},
    run=_list,
)

window_action = Tool(
    name="window_action", group="windows", tier=SAFE,
    description="Focus (bring to front), minimise, maximise or restore an open window.",
    parameters={"type": "object", "properties": {
        "action": {"type": "string", "enum": [*ACTIONS, *_SPELLING]},
        "window": {"type": "string", "description": "App name or window title, e.g. Notepad", "minLength": 1,
                   "maxLength": 100}},
        "required": ["action", "window"]},
    run=_action,
)

