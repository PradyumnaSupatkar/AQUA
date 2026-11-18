"""Bounded, selection-independent TOM2 end insertion; never read/SetValue/restore.

Only a private degenerate range with BOTH endpoints forward is writable. User
selection/focus is never changed. Old contents are verified privately and never
returned. Concurrent changes can make the outcome unknown, not safe to retry.
"""

from time import perf_counter

from .backend import NativeWindows, UIAutomation, BackendUnavailable, _property, _runtime_id
from .interaction import InteractionBackend, LOCAL_TEXT_APPS, WALK_DEADLINE_SECONDS, valid_text

MAX_DOCUMENT_UNITS = 16384
TOM_FORWARD = 0x3FFFFFFF
TOM_GRAVITY_FORE = 2


class AppendUnsupported(Exception):
    pass


def units(text):
    if type(text) is not str:
        raise AppendUnsupported()
    return len(text.encode("utf-16-le")) // 2


def canonical_eol(text):
    """Compare two provider representations; never used to rewrite old content."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def append_suffix(before, text, new_line):
    if not new_line or not before or before.endswith(("\r", "\n")):
        return text
    eol = "\r\n" if "\r\n" in before else "\n" if "\n" in before else "\r" if "\r" in before else "\r\n"
    return eol + text


def verifies_append(before, after, text, new_line):
    """Exact old prefix/literal; only a newly inserted EOL may normalize."""
    if not after.startswith(before):
        return False
    suffix = after[len(before):]
    if new_line and before and not before.endswith(("\r", "\n")):
        return suffix in {eol + text for eol in ("\r\n", "\n", "\r")}
    return suffix == text


def native_document(element, native, ui, identity, runtime_id):
    """Fixed read-only native-object lookup bound to this exact UIA control."""
    import ctypes
    from ctypes import wintypes
    from comtypes.automation import IDispatch
    from comtypes.client.dynamic import Dispatch
    handle = int(element.CurrentNativeWindowHandle)
    if handle <= 0 or _runtime_id(ui.bind(handle)) != runtime_id:
        raise AppendUnsupported()
    native.user.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
    native.user.IsChild.restype = wintypes.BOOL
    if handle != identity["handle"] and not native.user.IsChild(identity["handle"], handle):
        raise AppendUnsupported()
    pid = wintypes.DWORD()
    native.user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    if pid.value != identity["pid"]:
        raise AppendUnsupported()
    function = ctypes.WinDLL("oleacc").AccessibleObjectFromWindow
    function.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    function.restype = wintypes.LONG
    pointer, iid = ctypes.POINTER(IDispatch)(), IDispatch._iid_
    if function(handle, 0xFFFFFFF0, ctypes.byref(iid), ctypes.byref(pointer)) != 0 or not pointer:
        raise AppendUnsupported()
    if pointer.GetTypeInfo(0).GetDocumentation(-1)[0] != "ITextDocument2":
        raise AppendUnsupported()
    return Dispatch(pointer)


def outcome(status, attempted=False, verified=False):
    return {"status": status, "attempted": attempted, "verified": verified,
            "prefix_preserved": verified, "boundary_verified": verified}


class AppendBackend(InteractionBackend):
    def _get_document(self, element, native, ui, identity, runtime_id):
        return native_document(element, native, ui, identity, runtime_id)

    def _get_text_pattern(self, element):
        from pywinauto.uia_defines import get_elem_interface
        return get_elem_interface(element, "Text")

    def _snapshot(self, document, text_pattern):
        end = document.Range2(TOM_FORWARD, TOM_FORWARD)
        length = int(end.StoryLength)
        if not 0 <= length <= MAX_DOCUMENT_UNITS or int(end.Start) != length or int(end.End) != length:
            raise AppendUnsupported()  # Includes unsupported virtual-final-EOP providers.
        native_text = document.Range2(0, length).Text
        uia_text = text_pattern.DocumentRange.GetText(MAX_DOCUMENT_UNITS + 1)
        if (units(native_text) != length or units(uia_text) > MAX_DOCUMENT_UNITS
                or canonical_eol(native_text) != canonical_eol(uia_text)):
            raise AppendUnsupported()
        return native_text, uia_text

    def append_control_text(self, app, identity, runtime_id, text, new_line):
        attempted = False
        try:
            if app not in LOCAL_TEXT_APPS or not valid_text(text) or type(new_line) is not bool:
                return outcome("not_authorized")
            native, ui = self._mock_native or NativeWindows(), self._mock_ui or UIAutomation()
            root = self._validate_window(native, ui, app, identity)
            if root is None:
                return outcome("stale_window")
            element = self._find_element_by_runtime_id(root, ui, runtime_id, perf_counter() + WALK_DEADLINE_SECONDS)
            if element is None:
                return outcome("stale_control")
            if not self._security_gate(element, ui, identity, runtime_id):
                return outcome("ineligible_control")
            value = self._get_value_pattern(element)
            if _property(value, "CurrentIsReadOnly") is not False:
                return outcome("not_editable")
            document = self._get_document(element, native, ui, identity, runtime_id)
            pattern = self._get_text_pattern(element)
            before = self._snapshot(document, pattern)
            addition = append_suffix(before[0], text, new_line)
            if units(before[0]) + units(addition) > MAX_DOCUMENT_UNITS:
                return outcome("document_too_large")
            span = document.Range2(TOM_FORWARD, TOM_FORWARD)
            # This changes a PRIVATE range, never the visible user selection.
            # Equal endpoint gravity prevents concurrent insertions expanding
            # the zero-length range into text that SetText could replace.
            span.Gravity = TOM_GRAVITY_FORE
            if int(span.Gravity) != TOM_GRAVITY_FORE:
                return outcome("unsupported_append")
            if (self._validate_window(native, ui, app, identity) is None
                    or not self._security_gate(element, ui, identity, runtime_id)):
                return outcome("stale_control")
            if _property(value, "CurrentIsReadOnly") is not False:
                return outcome("not_editable")
            if self._snapshot(document, pattern) != before:
                return outcome("document_changed")
            if int(span.Start) != int(span.End) or int(span.End) != int(span.StoryLength):
                return outcome("document_changed")
            attempted = True
            span.Text = addition  # Insertion at a degenerate range, not SetValue.
            if (self._validate_window(native, ui, app, identity) is None
                    or not self._security_gate(element, ui, identity, runtime_id)):
                return outcome("unverified", True)
            after = self._snapshot(document, pattern)
            verified = all(verifies_append(old, current, text, new_line) for old, current in zip(before, after))
            return outcome("ok" if verified else "unverified", True, verified)
        except (AppendUnsupported, AttributeError):
            return outcome("unverified" if attempted else "unsupported_append", attempted)
        except BackendUnavailable:
            return outcome("unavailable", attempted)
        except Exception:
            return outcome("error", attempted)


def dispatch_append(request, *, native=None, ui=None):
    try:
        from .session import _identity
        expected = {"operation", "scope", "app_id", "identity", "runtime_id", "text", "new_line"}
        if type(request) is not dict or set(request) != expected:
            return outcome("not_authorized")
        app, rid = request["app_id"], request["runtime_id"]
        if (request["operation"] != "append_control_text" or app not in LOCAL_TEXT_APPS
                or request["scope"] != [app] or not valid_text(request["text"]) or type(request["new_line"]) is not bool):
            return outcome("not_authorized")
        _identity(request["identity"])
        if type(rid) is not list or not 1 <= len(rid) <= 32 or any(type(x) is not int for x in rid):
            return outcome("stale_control")
        return AppendBackend(mock_native=native, mock_ui=ui).append_control_text(
            app, request["identity"], rid, request["text"], request["new_line"])
    except Exception:
        return outcome("error")
