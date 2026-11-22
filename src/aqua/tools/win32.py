"""The real Windows desktop adapter: ctypes for windows, input and audio; pywin32 COM
for the Apps folder and Start-menu shortcuts. Nothing runs at import time."""

import ctypes
from ctypes import wintypes
import ntpath
import os
from pathlib import Path
import time
import warnings

from .adapter import AppEntry, Battery, Desktop, Window

TERMINALS = frozenset({"windowsterminal.exe", "conhost.exe", "openconsole.exe", "cmd.exe", "powershell.exe",
                       "pwsh.exe", "wt.exe", "powershell_ise.exe", "mintty.exe", "alacritty.exe"})
APPS_FOLDER = "shell:::{4234d49b-0245-4df3-b780-3893943456e1}"
MAX_WINDOWS_SCANNED = 512
MAX_SHORTCUTS = 3000
TYPE_CHUNK = 16

# Win32 constants
GW_OWNER, GA_ROOT = 4, 2
GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x80
SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
WM_CLOSE = 0x0010
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1}
PROCESS_QUERY_LIMITED_INFORMATION, SYNCHRONIZE = 0x1000, 0x00100000
DWMWA_CLOAKED = 14
CLSCTX_ALL = 23
TH32CS_SNAPPROCESS = 0x2

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", wintypes.BYTE), ("BatteryFlag", wintypes.BYTE),
                ("BatteryLifePercent", wintypes.BYTE), ("SystemStatusFlag", wintypes.BYTE),
                ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ULONG_PTR), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


class GUID(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16), ("data3", ctypes.c_uint16),
                ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value: str) -> "GUID":
        import uuid
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
IID_IAudioEndpointVolume = "5CDF2C82-841E-4546-9722-0CF74078229A"


def utf16_units(character: str) -> list[int]:
    data = character.encode("utf-16-le")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]


def unicode_inputs(text: str):
    """Key down/up pairs that type ``text`` as Unicode, independent of keyboard layout."""
    units = [unit for character in text for unit in utf16_units(character)]
    inputs = (INPUT * (2 * len(units)))()
    for index, unit in enumerate(units):
        for offset, flags in ((0, KEYEVENTF_UNICODE), (1, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
            item = inputs[2 * index + offset]
            item.type = INPUT_KEYBOARD
            item.ki = KEYBDINPUT(0, unit, flags, 0, 0)
    return inputs


def _vtable(pointer, slot: int, result, *arguments):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[slot])


def _release(pointer) -> None:
    if pointer:
        _vtable(pointer, 2, ctypes.c_ulong)(pointer)


def _check(result: int, what: str) -> None:
    if result < 0:
        raise OSError(f"{what} failed (HRESULT 0x{result & 0xffffffff:08x})")


class Win32Desktop(Desktop):
    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("The Windows desktop adapter needs Windows.")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.dwm = ctypes.WinDLL("dwmapi")
        self.ole = ctypes.WinDLL("ole32")
        self.ole.CoInitializeEx.argtypes, self.ole.CoInitializeEx.restype = [ctypes.c_void_p, wintypes.DWORD], ctypes.c_long
        self.ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        self.ole.CoCreateInstance.restype = ctypes.c_long
        u, k = self.user, self.kernel
        self._callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        for function, args, restype in (
            (u.EnumWindows, [self._callback, wintypes.LPARAM], wintypes.BOOL),
            (u.IsWindow, [wintypes.HWND], wintypes.BOOL),
            (u.IsWindowVisible, [wintypes.HWND], wintypes.BOOL),
            (u.IsIconic, [wintypes.HWND], wintypes.BOOL),
            (u.IsZoomed, [wintypes.HWND], wintypes.BOOL),
            (u.GetWindow, [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            (u.GetAncestor, [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            (u.GetWindowLongW, [wintypes.HWND, ctypes.c_int], wintypes.LONG),
            (u.GetWindowTextLengthW, [wintypes.HWND], ctypes.c_int),
            (u.GetWindowTextW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            (u.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (u.GetForegroundWindow, [], wintypes.HWND),
            (u.SetForegroundWindow, [wintypes.HWND], wintypes.BOOL),
            (u.BringWindowToTop, [wintypes.HWND], wintypes.BOOL),
            (u.ShowWindow, [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            (u.AttachThreadInput, [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL], wintypes.BOOL),
            (u.PostMessageW, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
            (u.SendInput, [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int], wintypes.UINT),
            (u.keybd_event, [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ULONG_PTR], None),
            (u.GetAsyncKeyState, [ctypes.c_int], ctypes.c_short),
            (u.LockWorkStation, [], wintypes.BOOL),
            (u.WaitForInputIdle, [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            (k.GetCurrentThreadId, [], wintypes.DWORD),
            (k.GetCurrentProcessId, [], wintypes.DWORD),
            (k.GetConsoleWindow, [], wintypes.HWND),
            (k.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (k.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
            (k.QueryFullProcessImageNameW, [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                            ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (k.GetSystemPowerStatus, [ctypes.POINTER(SYSTEM_POWER_STATUS)], wintypes.BOOL),
            (k.CreateToolhelp32Snapshot, [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            (k.Process32FirstW, [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)], wintypes.BOOL),
            (k.Process32NextW, [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)], wintypes.BOOL),
            (self.dwm.DwmGetWindowAttribute, [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD],
             ctypes.c_long),
        ):
            function.argtypes, function.restype = args, restype
        self._own = self._ancestor_pids()

    # -- processes ------------------------------------------------------------

    def _ancestor_pids(self) -> frozenset[int]:
        """Aqua's own process and its parents (the terminal or editor it runs in)."""
        parents = {}
        snapshot = self.kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot and snapshot != wintypes.HANDLE(-1).value:
            try:
                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(entry)
                ok = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
                while ok:
                    parents[entry.th32ProcessID] = entry.th32ParentProcessID
                    ok = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
            finally:
                self.kernel.CloseHandle(snapshot)
        pid, chain = self.kernel.GetCurrentProcessId(), set()
        while pid and pid not in chain and len(chain) < 32:
            chain.add(pid)
            pid = parents.get(pid, 0)
        return frozenset(chain)

    def _process_name(self, pid: int) -> str:
        handle = self.kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buffer))
            if not self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return ""
            return ntpath.basename(buffer.value).casefold()
        finally:
            self.kernel.CloseHandle(handle)

    # -- apps -----------------------------------------------------------------

    def app_entries(self) -> list[AppEntry]:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        entries = []
        try:
            folder = win32com.client.Dispatch("Shell.Application").NameSpace(APPS_FOLDER)
            items = folder.Items() if folder is not None else []
            for index in range(items.Count if items else 0):
                item = items.Item(index)
                name, path = str(item.Name or "").strip(), str(item.Path or "").strip()
                if name and path:
                    entries.append(AppEntry(name, path, "appsfolder",
                                            path if path.casefold().endswith(".exe") else None))
        except Exception:
            pass  # The Start-menu shortcuts below still work.
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
        except Exception:
            shell = None
        roots = [Path(os.environ.get("ProgramData", r"C:\ProgramData")), Path(os.environ.get("APPDATA", ""))]
        count = 0
        for root in roots:
            programs = root / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if not programs.is_dir():
                continue
            for link in programs.rglob("*.lnk"):
                count += 1
                if count > MAX_SHORTCUTS:
                    break
                target = None
                if shell is not None:
                    try:
                        target = str(shell.CreateShortcut(str(link)).TargetPath or "") or None
                    except Exception:
                        target = None
                entries.append(AppEntry(link.stem, str(link), "shortcut", target))
        return entries

    def launch(self, entry: AppEntry) -> None:
        if entry.kind == "shortcut":
            os.startfile(entry.target)
        else:
            os.startfile("shell:AppsFolder\\" + entry.target)

    # -- windows ----------------------------------------------------------------

    def _root(self, handle) -> int:
        return int(self.user.GetAncestor(handle, GA_ROOT) or 0) if handle else 0

    def _cloaked(self, handle) -> bool:
        value = wintypes.DWORD()
        result = self.dwm.DwmGetWindowAttribute(handle, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
        return result == 0 and value.value != 0

    def _window(self, handle, front: int) -> Window | None:
        u = self.user
        if not u.IsWindowVisible(handle) or u.GetWindow(handle, GW_OWNER) or self._cloaked(handle):
            return None
        if u.GetWindowLongW(handle, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return None
        length = u.GetWindowTextLengthW(handle)
        if length <= 0:
            return None
        buffer = ctypes.create_unicode_buffer(min(length, 512) + 1)
        u.GetWindowTextW(handle, buffer, len(buffer))
        title = buffer.value.strip()
        if not title or title == "Program Manager":
            return None
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        return Window(int(handle), title, self._process_name(pid.value), int(pid.value),
                      bool(u.IsIconic(handle)), bool(u.IsZoomed(handle)), int(handle) == front)

    def windows(self) -> list[Window]:
        handles = []

        def visit(handle, _parameter):
            handles.append(handle)
            return len(handles) < MAX_WINDOWS_SCANNED

        self.user.EnumWindows(self._callback(visit), 0)
        front = self._root(self.user.GetForegroundWindow())
        return [window for handle in handles if (window := self._window(handle, front)) is not None]

    def foreground(self) -> Window | None:
        front = self._root(self.user.GetForegroundWindow())
        return self._window(front, front) if front else None

    def focus(self, handle: int) -> bool:
        u, k = self.user, self.kernel
        if not u.IsWindow(handle):
            return False
        if u.IsIconic(handle):
            u.ShowWindow(handle, SW_RESTORE)
        if self._root(u.GetForegroundWindow()) == handle:
            return True
        current = k.GetCurrentThreadId()
        front_thread = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
        attached = bool(front_thread and front_thread != current and u.AttachThreadInput(current, front_thread, True))
        try:
            u.SetForegroundWindow(handle)
            u.BringWindowToTop(handle)
        finally:
            if attached:
                u.AttachThreadInput(current, front_thread, False)
        if not self._wait_foreground(handle, .5):
            # Windows only lets the process with the latest input take the
            # foreground; a tapped Alt key releases that lock.
            u.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY, 0)
            u.SetForegroundWindow(handle)
            u.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
        return self._wait_foreground(handle, 1.0)

    def _wait_foreground(self, handle: int, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while True:
            if self._root(self.user.GetForegroundWindow()) == handle:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(.05)

    def show(self, handle: int, state: str) -> None:
        command = {"minimise": SW_MINIMIZE, "maximise": SW_MAXIMIZE, "restore": SW_RESTORE}[state]
        if not self.user.IsWindow(handle):
            raise OSError("That window has closed.")
        self.user.ShowWindow(handle, command)

    def close_window(self, handle: int) -> None:
        if not self.user.IsWindow(handle) or not self.user.PostMessageW(handle, WM_CLOSE, 0, 0):
            raise OSError("That window has already closed.")

    def wait_ready(self, handle: int) -> None:
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        process = self.kernel.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if process:
            try:
                self.user.WaitForInputIdle(process, 3000)
            finally:
                self.kernel.CloseHandle(process)
        time.sleep(.5)  # New editors often take a moment before keystrokes land.

    def is_terminal(self, window: Window) -> bool:
        console = self._root(self.kernel.GetConsoleWindow())
        return window.process in TERMINALS or window.pid in self._own or bool(console and window.handle == console)

    # -- input -----------------------------------------------------------------

    def _wait_for_modifiers(self) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and any(
                self.user.GetAsyncKeyState(key) & 0x8000 for key in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)):
            time.sleep(.05)

    def type_text(self, handle: int, text: str) -> int:
        self._wait_for_modifiers()
        typed = 0
        for start in range(0, len(text), TYPE_CHUNK):
            if self._root(self.user.GetForegroundWindow()) != handle:
                break
            chunk = text[start:start + TYPE_CHUNK]
            inputs = unicode_inputs(chunk)
            sent = self.user.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT))
            if sent != len(inputs):
                break
            typed += len(chunk)
            time.sleep(.01)
        time.sleep(.15)  # Let the app process the queued keystrokes before readback.
        return typed

    def read_focused_text(self) -> str | None:
        """Read the focused control through UI Automation, never from a password field."""
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from pywinauto.uia_defines import IUIA, get_elem_interface
            element = IUIA().iuia.GetFocusedElement()
            if element is None or element.CurrentIsPassword:
                return None
            try:
                return str(get_elem_interface(element, "Value").CurrentValue)
            except Exception:
                pass
            document = get_elem_interface(element, "Text").DocumentRange
            return str(document.GetText(65536))
        except Exception:
            return None

    # -- system -----------------------------------------------------------------

    def _endpoint_volume(self):
        """IAudioEndpointVolume of the default speakers. The caller releases it."""
        self.ole.CoInitializeEx(None, 2)  # STA; an existing apartment of either kind is fine.
        enumerator, device, volume = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        clsid, iid = GUID.parse(CLSID_MMDeviceEnumerator), GUID.parse(IID_IMMDeviceEnumerator)
        _check(self.ole.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL, ctypes.byref(iid),
                                         ctypes.byref(enumerator)), "Open audio devices")
        try:
            _check(_vtable(enumerator, 4, ctypes.c_long, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                enumerator, 0, 0, ctypes.byref(device)), "Find the speakers")
            endpoint = GUID.parse(IID_IAudioEndpointVolume)
            _check(_vtable(device, 3, ctypes.c_long, ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_void_p))(device, ctypes.byref(endpoint), CLSCTX_ALL, None,
                                                            ctypes.byref(volume)), "Open the volume control")
        finally:
            _release(device)
            _release(enumerator)
        return volume

    def get_volume(self) -> tuple[int, bool]:
        volume = self._endpoint_volume()
        try:
            scalar, muted = ctypes.c_float(), wintypes.BOOL()
            _check(_vtable(volume, 9, ctypes.c_long, ctypes.POINTER(ctypes.c_float))(volume, ctypes.byref(scalar)),
                   "Read the volume")
            _check(_vtable(volume, 15, ctypes.c_long, ctypes.POINTER(wintypes.BOOL))(volume, ctypes.byref(muted)),
                   "Read mute")
            return round(scalar.value * 100), bool(muted.value)
        finally:
            _release(volume)

    def set_volume(self, level: int) -> None:
        volume = self._endpoint_volume()
        try:
            _check(_vtable(volume, 7, ctypes.c_long, ctypes.c_float, ctypes.c_void_p)(
                volume, max(0.0, min(1.0, level / 100)), None), "Set the volume")
        finally:
            _release(volume)

    def set_mute(self, muted: bool) -> None:
        volume = self._endpoint_volume()
        try:
            _check(_vtable(volume, 14, ctypes.c_long, wintypes.BOOL, ctypes.c_void_p)(volume, bool(muted), None),
                   "Set mute")
        finally:
            _release(volume)

    def media_key(self, key: str) -> None:
        code = MEDIA_KEYS[key]
        self.user.keybd_event(code, 0, KEYEVENTF_EXTENDEDKEY, 0)
        self.user.keybd_event(code, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)

    def lock(self) -> None:
        if not self.user.LockWorkStation():
            raise OSError("Windows did not lock the screen.")

    def battery(self) -> Battery:
        status = SYSTEM_POWER_STATUS()
        if not self.kernel.GetSystemPowerStatus(ctypes.byref(status)):
            return Battery(True)
        flag = status.BatteryFlag & 0xFF
        if flag == 128:
            return Battery(False)
        percent = status.BatteryLifePercent & 0xFF
        seconds = status.BatteryLifeTime
        return Battery(True, None if percent == 255 else percent,
                       None if flag == 255 else bool(flag & 8) or status.ACLineStatus == 1,
                       None if seconds == 0xFFFFFFFF else seconds // 60)

    # -- time -------------------------------------------------------------------

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
