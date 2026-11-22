"""The small interface every Windows effect goes through.

The real implementation is tools.win32.Win32Desktop; tests use tools.fake.FakeDesktop.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AppEntry:
    """One launchable app from the Start menu or the Apps folder."""
    name: str                   # display name, e.g. "Visual Studio Code"
    target: str                 # .lnk path, or an Apps-folder ID (AUMID or path-like ID)
    kind: str = "shortcut"      # shortcut | appsfolder
    executable: str | None = None  # resolved target executable, when known


@dataclass(frozen=True, slots=True)
class Window:
    handle: int
    title: str
    process: str                # executable file name, lowercased, e.g. "notepad.exe"
    pid: int = 0
    minimized: bool = False
    maximized: bool = False
    foreground: bool = False


@dataclass(frozen=True, slots=True)
class Battery:
    present: bool
    percent: int | None = None
    charging: bool | None = None
    minutes_left: int | None = None


class Desktop(ABC):
    # Apps
    @abstractmethod
    def app_entries(self) -> list[AppEntry]: ...
    @abstractmethod
    def launch(self, entry: AppEntry) -> None: ...

    # Windows (visible top-level app windows, front to back)
    @abstractmethod
    def windows(self) -> list[Window]: ...
    @abstractmethod
    def foreground(self) -> Window | None: ...
    @abstractmethod
    def focus(self, handle: int) -> bool:
        """Bring a window to the front; True only if it is then the foreground window."""
    @abstractmethod
    def show(self, handle: int, state: str) -> None:
        """state: minimise | maximise | restore."""
    @abstractmethod
    def close_window(self, handle: int) -> None:
        """Ask a window to close (WM_CLOSE); the app may prompt to save."""
    @abstractmethod
    def wait_ready(self, handle: int) -> None:
        """Wait briefly until a new window accepts input."""
    @abstractmethod
    def is_terminal(self, window: Window) -> bool:
        """True for console/terminal windows, including the one Aqua runs in."""

    # Input
    @abstractmethod
    def type_text(self, handle: int, text: str) -> int:
        """Type Unicode text at the cursor of the foreground window ``handle``.
        Never presses Enter. Stops if focus moves away; returns characters typed."""
    @abstractmethod
    def read_focused_text(self) -> str | None:
        """UI Automation readback of the focused control, or None if unsupported."""

    # System
    @abstractmethod
    def get_volume(self) -> tuple[int, bool]:
        """(level 0-100, muted)."""
    @abstractmethod
    def set_volume(self, level: int) -> None: ...
    @abstractmethod
    def set_mute(self, muted: bool) -> None: ...
    @abstractmethod
    def media_key(self, key: str) -> None:
        """key: play_pause | next | previous."""
    @abstractmethod
    def lock(self) -> None: ...
    @abstractmethod
    def battery(self) -> Battery: ...

    # Time
    @abstractmethod
    def monotonic(self) -> float: ...
    @abstractmethod
    def sleep(self, seconds: float) -> None: ...
