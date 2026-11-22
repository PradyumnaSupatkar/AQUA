"""Session and per-request state shared by tools."""

from dataclasses import dataclass, field

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

    def begin_request(self) -> None:
        self.launches.clear()
