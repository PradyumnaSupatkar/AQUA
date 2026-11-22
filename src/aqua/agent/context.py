"""Session and per-request state shared by tools."""

from dataclasses import dataclass, field

from .countdown import Countdown
from .policy import Policy
from .undo import UndoStack


@dataclass(slots=True)
class Launch:
    app: str                    # display name, e.g. "Notepad"
    handle: int | None          # window handle once seen, or None


@dataclass(slots=True)
class ToolContext:
    desktop: object             # a tools.adapter.Desktop
    apps: object                # a tools.app_index.AppIndex
    policy: Policy = field(default_factory=Policy)
    undo: UndoStack = field(default_factory=UndoStack)
    # Apps launched during the current request, newest last. type_text uses the
    # newest one when the user did not name a target window.
    launches: list[Launch] = field(default_factory=list)
    # A confirmed restart/shutdown waiting out its countdown.
    countdown: Countdown | None = None
    # Session privacy record for look_up: window titles and app/process names
    # that tools reported, and text the user asked to type. Never sent anywhere.
    observed: set[str] = field(default_factory=set)
    typed: list[str] = field(default_factory=list)
    request: str = ""           # The current user request (stays on this PC).
    # Earlier requests this one answers (Aqua asked a question in between), for grounding.
    earlier: tuple[str, ...] = ()

    def begin_request(self, request: str = "", earlier: tuple[str, ...] = ()) -> None:
        self.launches.clear()
        self.request = request
        self.earlier = tuple(earlier)

    def observe(self, *values) -> None:
        for value in values:
            if isinstance(value, str) and len(value.strip()) >= 3:
                self.observed.add(value.strip().casefold())
