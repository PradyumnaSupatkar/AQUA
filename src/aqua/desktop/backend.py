"""Read-only Win32 identity checks and bounded native UI Automation queries.

Imported by the isolated worker. There are deliberately no UI input/control
patterns here: no Invoke, SetValue, focus, keys, clicks, screenshots, or text APIs.
"""

from collections import deque
import ctypes
from ctypes import wintypes
import ntpath
import os
import sys
from time import perf_counter
import unicodedata

from aqua.actions.launcher import ALLOWED_APPS

MAX_WINDOWS = 8
MAX_ELEMENTS = 32
MAX_DEPTH = 8
SOFT_SECONDS = 3.5
LABEL_TYPES = frozenset({"Button", "CheckBox", "RadioButton", "Menu", "MenuBar",
                         "MenuItem", "Tab", "TabItem", "ToolBar", "TitleBar", "Window"})
PRUNED_TYPES = frozenset({"Edit", "Text"})
# Modern editors may expose Document, but type alone never grants write access.
# Discovery and execution still require the real writable ValuePattern.
VALUE_CONTROL_TYPES = frozenset({"Edit", "Document"})
PACKAGE_FAMILIES = {
    "calculator": "Microsoft.WindowsCalculator_8wekyb3d8bbwe",
    "notepad": "Microsoft.WindowsNotepad_8wekyb3d8bbwe",
}


class BackendUnavailable(RuntimeError):
    pass


def clean_label(value, maximum=80):
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if unicodedata.category(ch) not in {"Cc", "Cf"})[:maximum]


def _path(value):
    return ntpath.normcase(ntpath.normpath(value.removeprefix("\\\\?\\")))


def match_application(info, scope):
    """Paths or OS-authenticated package identity, never captions/basenames."""
    if not isinstance(info, dict) or not isinstance(info.get("image"), str):
        return None
    for app in scope:
        if app not in ALLOWED_APPS:
            continue
        if _path(info["image"]) == _path(ALLOWED_APPS[app][0]):
            return app
        family = PACKAGE_FAMILIES.get(app)
        if family and info.get("package_family") == family:
            return app
    return None


class _FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


class NativeWindows:
    def interaction_allowed(self, pid):
        """Reject elevated/protected targets even if Aqua itself is elevated."""
        try:
            import win32api
            import win32con
            import win32security
            process = win32api.OpenProcess(0x1000, False, pid)
            try:
                token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
                try:
                    sid = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)[0]
                    return sid.GetSubAuthority(sid.GetSubAuthorityCount() - 1) <= 0x2000
                finally:
                    token.Close()
            finally:
                process.Close()
        except Exception:
            return False

    def __init__(self):
        if os.name != "nt":
            raise BackendUnavailable("Windows UI Automation requires Windows.")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            self.dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        except OSError as exc:
            raise BackendUnavailable("Window visibility cannot be verified.") from exc
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = (
            (self.user.EnumWindows, [self.callback_type, wintypes.LPARAM], wintypes.BOOL),
            (self.user.IsWindow, [wintypes.HWND], wintypes.BOOL),
            (self.user.IsWindowVisible, [wintypes.HWND], wintypes.BOOL),
            (self.user.IsIconic, [wintypes.HWND], wintypes.BOOL),
            (self.dwm.DwmGetWindowAttribute, [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p,
                                            wintypes.DWORD], wintypes.LONG),
            (self.user.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (self.user.GetWindowTextW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user.GetForegroundWindow, [], wintypes.HWND),
            (self.kernel.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
            (self.kernel.QueryFullProcessImageNameW, [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                   ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (self.kernel.GetProcessTimes, [wintypes.HANDLE, *[ctypes.POINTER(_FILETIME)] * 4], wintypes.BOOL),
            (self.kernel.GetPackageFamilyName, [wintypes.HANDLE, ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR], wintypes.LONG),
        )
        for function, args, result in signatures:
            function.argtypes, function.restype = args, result
        self.truncated = False
        self.all_handles = []  # Private pre-launch identity guard, never UI labels.

    def is_observable(self, handle):
        """Fail closed for minimized, cloaked/other-desktop, or unknown windows."""
        try:
            if (not self.user.IsWindow(handle) or not self.user.IsWindowVisible(handle)
                    or self.user.IsIconic(handle)):
                return False
            cloaked = wintypes.DWORD()
            status = self.dwm.DwmGetWindowAttribute(handle, 14, ctypes.byref(cloaked),
                                                    ctypes.sizeof(cloaked))  # DWMWA_CLOAKED
            return status == 0 and cloaked.value == 0
        except (OSError, ValueError, TypeError):
            return False

    def visible_handles(self, deadline):
        handles, scanned = [], 0
        self.all_handles = []

        def visit(handle, _parameter):
            nonlocal scanned
            scanned += 1
            if scanned > 512 or perf_counter() >= deadline:
                self.truncated = True
                return False
            # Include minimized/cloaked windows in the private baseline so that
            # restoring an existing window cannot masquerade as a new target.
            self.all_handles.append(int(handle))
            if self.is_observable(handle):
                handles.append(int(handle))
            return True

        if not self.user.EnumWindows(self.callback_type(visit), 0):
            self.truncated = True
        return handles

    def process_info(self, pid):
        process = self.kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION only.
        if not process:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if not self.kernel.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                return None
            creation, exit_time, kernel_time, user_time = (_FILETIME() for _ in range(4))
            if not self.kernel.GetProcessTimes(process, ctypes.byref(creation), ctypes.byref(exit_time),
                                               ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return None
            family_size = wintypes.UINT(0)
            family = None
            if self.kernel.GetPackageFamilyName(process, ctypes.byref(family_size), None) == 122 and family_size.value <= 256:
                family_buffer = ctypes.create_unicode_buffer(family_size.value)
                if self.kernel.GetPackageFamilyName(process, ctypes.byref(family_size), family_buffer) == 0:
                    family = family_buffer.value
            return {"pid": int(pid), "created": (int(creation.high) << 32) | int(creation.low),
                    "image": buffer.value, "package_family": family}
        finally:
            self.kernel.CloseHandle(process)

    def window_info(self, handle):
        if not self.is_observable(handle):
            return None
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        info = self.process_info(pid.value)
        if info is not None:
            info["handle"] = int(handle)
        return info

    def title(self, handle):
        buffer = ctypes.create_unicode_buffer(121)
        self.user.GetWindowTextW(handle, buffer, len(buffer))
        return clean_label(buffer.value, 120)

    def active(self, handle):
        return int(self.user.GetForegroundWindow() or 0) == handle


class UIAutomation:
    def __init__(self):
        try:
            # Lazy MTA initialization happens only in the isolated, windowless worker.
            sys.coinit_flags = 0  # COINIT_MULTITHREADED before either COM library loads.
            from pywinauto.uia_defines import IUIA
            self.api = IUIA()
            self.walker = self.api.iuia.ControlViewWalker
        except (ImportError, OSError) as exc:
            raise BackendUnavailable("Install the approved desktop dependency.") from exc

    def bind(self, handle):
        return self.api.iuia.ElementFromHandle(handle)

    def control_type(self, element):
        return self.api.known_control_type_ids.get(element.CurrentControlType, "Unknown")

    def first_child(self, element):
        return self.walker.GetFirstChildElement(element)

    def next_sibling(self, element):
        return self.walker.GetNextSiblingElement(element)


def _runtime_id(element):
    values = tuple(element.GetRuntimeId())
    if not values or len(values) > 32 or any(type(x) is not int for x in values):
        raise ValueError("Invalid runtime identity.")
    return list(values)


def _identity(info, element):
    if int(element.CurrentProcessId) != info["pid"] or int(element.CurrentNativeWindowHandle) != info["handle"]:
        raise ValueError("The UIA window no longer matches its native identity.")
    return {key: info[key] for key in ("handle", "pid", "created")} | {"runtime_id": _runtime_id(element)}


def _property(element, name):
    try:
        value = getattr(element, name)
        return bool(value) if type(value) is bool or (type(value) is int and value in (0, 1)) else None
    except Exception:
        return None


def safe_control(element, ui, native, app_id, root_pid, depth, parent_index, *, discover_editable=False):
    """Password status precedes *all* names; input/document values are never read."""
    try:
        pid = int(element.CurrentProcessId)
        if pid != root_pid and match_application(native.process_info(pid), (app_id,)) != app_id:
            return None, False
        password = _property(element, "CurrentIsPassword")
        kind = ui.control_type(element)
    except Exception:
        return None, False
    row = {"control_type": kind, "depth": depth, "parent_index": parent_index,
           "password": password, "enabled": _property(element, "CurrentIsEnabled"),
           "visible": None}
    offscreen = _property(element, "CurrentIsOffscreen")
    row["visible"] = None if offscreen is None else not offscreen
    if password is not False:
        row["redacted"] = "password" if password else "unverified_password_status"
        return row, False
    if row["visible"] is not True:
        row["redacted"] = "offscreen" if row["visible"] is False else "unavailable"
        return row, False
    if kind in LABEL_TYPES:
        try:
            name = element.CurrentName
            row["name"] = clean_label(name)
            row["name_truncated"] = isinstance(name, str) and len(name) > 80
        except Exception:
            row["redacted"] = "unavailable"
    else:
        row["redacted"] = "text_input" if kind == "Edit" else "document_text" if kind == "Text" else "content_name"
    if discover_editable and kind in VALUE_CONTROL_TYPES and row["enabled"] is True and pid == root_pid:
        try:
            from .interaction import LOCAL_TEXT_APPS, value_pattern
            if app_id in LOCAL_TEXT_APPS and native.interaction_allowed(root_pid):
                pattern = value_pattern(element)
                if _property(pattern, "CurrentIsReadOnly") is False:
                    row["runtime_id"], row["editable"] = _runtime_id(element), True
        except Exception:
            pass  # Unsupported ValuePattern is a limitation, never a keyboard fallback.
    return row, kind not in PRUNED_TYPES


def walk_controls(root, ui, native, app_id, root_pid, deadline, *, discover_editable=False):
    queue, controls, truncated = deque([(root, 0, None)]), [], False
    visited = 0
    while queue and visited < MAX_ELEMENTS:
        if perf_counter() >= deadline:
            return controls, True
        element, depth, parent = queue.popleft()
        visited += 1
        row, descend = safe_control(element, ui, native, app_id, root_pid, depth, parent,
                                    discover_editable=discover_editable)
        if row is None:
            continue
        index = len(controls)
        controls.append(row)
        truncated = truncated or bool(row.get("name_truncated"))
        if not descend:
            continue
        if depth >= MAX_DEPTH:
            truncated = True
            continue
        try:
            child = ui.first_child(element)
            while child:
                if len(queue) + visited >= MAX_ELEMENTS or perf_counter() >= deadline:
                    truncated = True
                    break
                queue.append((child, depth + 1, index))
                child = ui.next_sibling(child)
        except Exception:
            truncated = True
    return controls, truncated or bool(queue)


def inspect_handle(request, *, native=None, ui=None):
    """Read-only snapshot of any visible top-level window, for the new action core.

    Reuses walk_controls/safe_control unchanged: names only for label-type
    controls, never Edit/Document values or password fields, and child
    elements from other processes are skipped (no approved app id is given).
    """
    try:
        handle = request.get("handle") if type(request) is dict else None
        if type(handle) is not int or handle <= 0:
            return {"status": "error"}
        native = native or NativeWindows()
        info = native.window_info(handle)
        if info is None:
            return {"status": "stale_window"}
        ui = ui or UIAutomation()
        root = ui.bind(handle)
        if int(root.CurrentProcessId) != info["pid"] or int(root.CurrentNativeWindowHandle) != handle:
            return {"status": "stale_window"}
        controls, truncated = walk_controls(root, ui, native, "", info["pid"], perf_counter() + SOFT_SECONDS)
        return {"status": "ok", "controls": controls, "truncated": truncated}
    except BackendUnavailable:
        return {"status": "unavailable"}
    except Exception:
        return {"status": "error"}


def dispatch(request, *, native=None, ui=None):
    """One bounded read-only worker operation; all exceptions become safe categories."""
    try:
        if type(request) is not dict or request.get("operation") not in {"list_windows", "inspect_window"}:
            return {"status": "error"}
        scope = request.get("scope")
        if type(scope) is not list or not scope or len(scope) > len(ALLOWED_APPS) or any(type(app) is not str or app not in ALLOWED_APPS for app in scope):
            return {"status": "not_authorized"}
        native = native or NativeWindows()
        deadline = perf_counter() + SOFT_SECONDS
        if request["operation"] == "list_windows":
            if request.get("launch_baseline") is True:
                from .interaction import LOCAL_TEXT_APPS
                if len(scope) != 1 or scope[0] not in LOCAL_TEXT_APPS:
                    return {"status": "not_authorized"}
                native.visible_handles(deadline)
                return {"status": "ok", "baseline_handles": list(native.all_handles),
                        "truncated": native.truncated or perf_counter() >= deadline}
            windows, failed_binding = [], False
            for handle in native.visible_handles(deadline):
                if perf_counter() >= deadline or len(windows) >= MAX_WINDOWS:
                    native.truncated = True
                    break
                info = native.window_info(handle)
                app = match_application(info, scope)
                if app is None:
                    continue
                ui = ui or UIAutomation()
                try:
                    element = ui.bind(handle)
                    identity = _identity(info, element)
                    # Window captions are bounded native metadata, queried only
                    # after approved identity and a successful password check.
                    password = _property(element, "CurrentIsPassword")
                    title = native.title(handle) if password is False else "[protected or unavailable]"
                    windows.append({"app_id": app, "identity": identity, "title": title,
                                    "active": native.active(handle)})
                except Exception:
                    failed_binding = True
                    native.truncated = True
                    continue
            return {"status": "ok" if windows else "error" if failed_binding else "empty", "windows": windows,
                    "truncated": native.truncated}
        app, expected = request.get("app_id"), request.get("identity")
        if app not in scope or type(expected) is not dict:
            return {"status": "not_authorized"}
        handle = expected.get("handle")
        if type(handle) is not int or handle <= 0:
            return {"status": "stale_window"}
        info = native.window_info(handle)
        if match_application(info, (app,)) != app:
            return {"status": "stale_window"}
        ui = ui or UIAutomation()
        try:
            root = ui.bind(handle)
            matches = _identity(info, root) == expected
        except Exception:
            matches = False
        if not matches:
            return {"status": "stale_window"}
        controls, truncated = walk_controls(root, ui, native, app, info["pid"], deadline,
                                            discover_editable=request.get("editable_controls") is True)
        after = native.window_info(handle)
        try:
            matches = match_application(after, (app,)) == app and _identity(after, root) == expected
        except Exception:
            matches = False
        if not matches:
            return {"status": "stale_window"}
        return {"status": "ok", "app_id": app, "identity": expected,
                "controls": controls, "truncated": truncated}
    except BackendUnavailable:
        return {"status": "unavailable"}
    except Exception:
        return {"status": "error"}
