"""An in-memory desktop for tests and dry runs. Nothing here touches Windows."""

from dataclasses import replace
from datetime import datetime

from .adapter import AppEntry, Battery, Desktop, Window

DEFAULT_APPS = (
    AppEntry("Notepad", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App", "appsfolder"),
    AppEntry("Calculator", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", "appsfolder"),
    AppEntry("Visual Studio Code", r"C:\ProgramData\Start Menu\Visual Studio Code.lnk", "shortcut",
             r"C:\Users\me\AppData\Local\Programs\Microsoft VS Code\Code.exe"),
    AppEntry("Spotify", "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "appsfolder"),
    AppEntry("Google Chrome", r"C:\ProgramData\Start Menu\Google Chrome.lnk", "shortcut",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    AppEntry("Command Prompt", r"C:\Users\me\Start Menu\System Tools\Command Prompt.lnk", "shortcut",
             r"C:\Windows\System32\cmd.exe"),
    AppEntry("Windows PowerShell", r"C:\Users\me\Start Menu\Windows PowerShell.lnk", "shortcut",
             r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
    AppEntry("Terminal", "Microsoft.WindowsTerminal_8wekyb3d8bbwe!App", "appsfolder"),
    AppEntry("Visual Studio 2022", r"C:\ProgramData\Start Menu\Visual Studio 2022.lnk", "shortcut",
             r"C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\devenv.exe"),
)

# What a launch of each app opens: (title, process).
LAUNCH_WINDOWS = {
    "Notepad": ("Untitled - Notepad", "notepad.exe"),
    "Calculator": ("Calculator", "applicationframehost.exe"),
    "Visual Studio Code": ("Welcome - Visual Studio Code", "code.exe"),
    "Spotify": ("Spotify Premium", "spotify.exe"),
    "Google Chrome": ("New Tab - Google Chrome", "chrome.exe"),
    "Visual Studio 2022": ("Visual Studio 2022", "devenv.exe"),
}
TERMINALS = frozenset({"windowsterminal.exe", "conhost.exe", "openconsole.exe", "cmd.exe", "powershell.exe",
                       "pwsh.exe", "wt.exe"})


class FakeDesktop(Desktop):
    def __init__(self, *, apps=DEFAULT_APPS, windows=None, volume: int = 50, muted: bool = False,
                 battery: Battery = Battery(True, 76, False, 190), focus_allowed: bool = True,
                 readback: bool = True, slow_apps=(), silent_apps=(), playback: str | None = "playing",
                 brightness: int | None = 70, stubborn_apps=()) -> None:
        self.apps = list(apps)
        # Front to back. By default Aqua's own terminal is in front, as in `aqua chat`.
        self._windows: list[Window] = list(windows) if windows is not None else [
            Window(1, "Aqua - Terminal", "windowsterminal.exe", 100)]
        self._next_handle = 1000
        self.volume, self.muted = volume, muted
        self._battery = battery
        self.focus_allowed, self.readback = focus_allowed, readback
        self.slow_apps, self.silent_apps = set(slow_apps), set(silent_apps)
        self.text: dict[int, str] = {}
        self.launched: list[str] = []
        self.typed: list[tuple[int, str]] = []
        self.media: list[str] = []
        self.closed: list[int] = []
        self.locked = False
        self.playback, self.brightness = playback, brightness
        self.stubborn_apps = set(stubborn_apps)   # processes whose windows ignore WM_CLOSE (unsaved work)
        self.terminated: list[int] = []
        self.power: list[tuple[str, int]] = []
        self.scheduled: str | None = None
        self.opened_urls: list[str] = []
        self.url_browsers: list[str | None] = []  # None: the default browser
        self.settings_opened: list[str] = []      # ms-settings: URIs
        self.controls: dict[int, list[dict]] = {}
        self.clock = 0.0
        self._pending: list[tuple[float, Window]] = []

    # helpers for tests
    def add_window(self, title: str, process: str, *, front: bool = True, pid: int | None = None) -> Window:
        handle = self._new_handle()
        window = Window(handle, title, process, pid if pid is not None else handle)
        self._windows.insert(0 if front else len(self._windows), window)
        return window

    def window(self, handle: int) -> Window | None:
        return next((w for w in self.windows() if w.handle == handle), None)

    def _new_handle(self) -> int:
        self._next_handle += 1
        return self._next_handle

    def _arrive(self) -> None:
        for when, window in list(self._pending):
            if self.clock >= when:
                self._pending.remove((when, window))
                self._windows.insert(0, window)

    # Desktop
    def app_entries(self):
        return list(self.apps)

    def launch(self, entry: AppEntry) -> None:
        self.launched.append(entry.name)
        if entry.name in self.silent_apps:
            return
        for window in self._windows:
            title, process = LAUNCH_WINDOWS.get(entry.name, (entry.name, ""))
            if window.process == process and process:
                self._windows.remove(window)
                self._windows.insert(0, replace(window, minimized=False))  # Already open: comes to the front.
                return
        title, process = LAUNCH_WINDOWS.get(entry.name, (entry.name, entry.name.casefold().replace(" ", "") + ".exe"))
        handle = self._new_handle()
        window = Window(handle, title, process, handle)
        if entry.name in self.slow_apps:
            self._pending.append((self.clock + 2.0, window))
        else:
            self._windows.insert(0, window)

    def windows(self):
        self._arrive()
        front = next((w.handle for w in self._windows if not w.minimized), None)
        return [replace(w, foreground=w.handle == front) for w in self._windows]

    def foreground(self):
        return next((w for w in self.windows() if w.foreground), None)

    def focus(self, handle: int) -> bool:
        if not self.focus_allowed:
            return False
        for window in self._windows:
            if window.handle == handle:
                self._windows.remove(window)
                self._windows.insert(0, replace(window, minimized=False))
                return True
        return False

    def show(self, handle: int, state: str) -> None:
        for index, window in enumerate(self._windows):
            if window.handle == handle:
                if state == "minimise":
                    self._windows[index] = replace(window, minimized=True)
                elif state == "maximise":
                    self._windows[index] = replace(window, minimized=False, maximized=True)
                else:
                    self._windows[index] = replace(window, minimized=False, maximized=False)

    def close_window(self, handle: int) -> None:
        self.closed.append(handle)
        self._windows = [w for w in self._windows if w.handle != handle or w.process in self.stubborn_apps]

    def wait_ready(self, handle: int) -> None:
        pass

    def is_terminal(self, window: Window) -> bool:
        return window.process in TERMINALS

    def type_text(self, handle: int, text: str) -> int:
        front = self.foreground()
        if front is None or front.handle != handle:
            return 0
        self.text[handle] = self.text.get(handle, "") + text
        self.typed.append((handle, text))
        return len(text)

    def read_focused_text(self):
        front = self.foreground()
        return self.text.get(front.handle, "") if self.readback and front is not None else None

    def get_volume(self):
        return self.volume, self.muted

    def set_volume(self, level: int) -> None:
        self.volume = level

    def set_mute(self, muted: bool) -> None:
        self.muted = muted

    def media_key(self, key: str) -> None:
        self.media.append(key)

    def media_state(self):
        return self.playback

    def lock(self) -> None:
        self.locked = True

    def battery(self):
        return self._battery

    def get_brightness(self):
        return self.brightness

    def set_brightness(self, level: int) -> None:
        if self.brightness is None:
            raise OSError("This display has no adjustable brightness.")
        self.brightness = level

    def terminate(self, pid: int) -> None:
        self.terminated.append(pid)
        self._windows = [w for w in self._windows if w.pid != pid]

    def schedule_shutdown(self, kind: str, seconds: int) -> None:
        self.power.append((kind, seconds))
        self.scheduled = kind

    def abort_shutdown(self) -> None:
        self.power.append(("abort", 0))
        self.scheduled = None

    def suspend(self) -> None:
        self.power.append(("sleep", 0))

    def sign_out(self) -> None:
        self.power.append(("sign_out", 0))

    def open_url(self, url: str, browser=None) -> None:
        self.opened_urls.append(url)
        self.url_browsers.append(browser.name if browser is not None else None)

    def open_settings(self, uri: str) -> None:
        from .settings import URIS
        if uri not in URIS:
            raise OSError("Only the allowlisted Settings pages can be opened.")
        self.settings_opened.append(uri)

    def inspect(self, handle: int) -> dict:
        if self.window(handle) is None:
            return {"status": "stale_window"}
        return {"status": "ok", "controls": list(self.controls.get(handle, ())), "truncated": False}

    def monotonic(self) -> float:
        return self.clock

    def sleep(self, seconds: float) -> None:
        self.clock += seconds

    def now(self) -> datetime:
        return datetime(2026, 11, 22, 15, 42).astimezone()
