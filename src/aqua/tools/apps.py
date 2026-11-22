"""launch_app: open any installed app from the app index."""

from aqua.agent.context import Launch, ToolContext
from aqua.agent.policy import clarify, deny
from aqua.agent.registry import SAFE, Tool, ToolOutcome, UndoRecord

from .adapter import AppEntry, Window
from .app_index import executable_stem, normalize

LAUNCH_WAIT_SECONDS = 8.0
RUNNING_WAIT_SECONDS = 3.0
POLL_SECONDS = .2


def window_matches_app(window: Window, entry: AppEntry | str) -> bool:
    """Match a window to an app by process name or title words."""
    name = normalize(entry if isinstance(entry, str) else entry.name)
    stem = "" if isinstance(entry, str) else executable_stem(entry)
    process = window.process.rsplit(".", 1)[0].casefold()
    compact = name.replace(" ", "")
    if process and (process == stem or process == compact or (len(compact) >= 4 and compact in process)):
        return True
    title = " ".join(window.title.casefold().split())
    return bool(name) and (title == name or title.endswith(" - " + name) or title.startswith(name + " - "))


def _blocked(context: ToolContext, query: str, entry: AppEntry | None) -> bool:
    policy = context.policy
    if policy.blocked(name=query):
        return True
    if entry is None:
        return False
    return policy.blocked(name=entry.name, executable=entry.executable or entry.target,
                          app_id=entry.target if entry.kind == "appsfolder" else None)


def _check(args: dict, context: ToolContext):
    query = args["app"]
    match = context.apps.find(query)
    if _blocked(context, query, match.entry) or any(_blocked(context, "", e) for e in match.candidates):
        label = match.entry.name if match.entry is not None else query
        return deny(f"{label} is blocked by Aqua's safety settings.")
    if match.kind == "ambiguous":
        names = [entry.name for entry in match.candidates]
        listed = ", ".join(names[:-1]) + " or " + names[-1]
        return clarify(f"Which one did you mean: {listed}?")
    return None


def _run(args: dict, context: ToolContext) -> ToolOutcome:
    match = context.apps.find(args["app"])
    if match.entry is None:
        return ToolOutcome("failed", f"No installed app called {args['app']} was found.")
    entry, desktop = match.entry, context.desktop
    existing = desktop.windows()
    before = {w.handle for w in existing}
    # Already open: bring that window forward instead of starting another copy,
    # so "write ... into Notepad" lands in the window the user means.
    for window in existing:
        if window_matches_app(window, entry) and desktop.focus(window.handle):
            context.launches.append(Launch(entry.name, window.handle))
            return ToolOutcome("ok", f"{entry.name} was already open; brought it to the front.",
                               done=f"brought {entry.name} to the front", data={"app": entry.name, "window": window.title})
    try:
        desktop.launch(entry)
    except OSError:
        return ToolOutcome("failed", f"Windows could not start {entry.name}.")
    window = None
    # An already-running app usually just comes to the front, so wait less for it.
    running = any(window_matches_app(w, entry) for w in existing)
    deadline = desktop.monotonic() + (RUNNING_WAIT_SECONDS if running else LAUNCH_WAIT_SECONDS)
    while window is None:
        for candidate in desktop.windows():
            new = candidate.handle not in before
            if window_matches_app(candidate, entry) and (new or candidate.foreground):
                window = candidate
                break
        if window is None:
            if desktop.monotonic() >= deadline:
                break
            desktop.sleep(POLL_SECONDS)
    if window is None:
        context.launches.append(Launch(entry.name, None))
        return ToolOutcome("unverified", f"Started {entry.name}, but its window has not appeared yet.",
                           done=f"started {entry.name}", data={"app": entry.name})
    new_window = window.handle not in before
    if new_window:
        desktop.wait_ready(window.handle)
    context.launches.append(Launch(entry.name, window.handle))
    undo = None
    if new_window:
        handle, name = window.handle, entry.name

        def close() -> str:
            desktop.close_window(handle)
            return f"closed {name}"
        undo = UndoRecord(f"opening {name}", close)
    message = f"Opened {entry.name}." if new_window else f"{entry.name} was already open; brought it to the front."
    return ToolOutcome("ok", message, done=f"opened {entry.name}",
                       data={"app": entry.name, "window": window.title}, undo=undo)


launch_app = Tool(
    name="launch_app", group="apps",
    description="Open (start) an installed app by name, e.g. Notepad, Spotify, Calculator, VS Code. Not for typing.",
    parameters={"type": "object", "properties": {
        "app": {"type": "string", "description": "App name as the user said it", "minLength": 1, "maxLength": 100}},
        "required": ["app"]},
    run=_run, tier=SAFE, check=_check,
)
