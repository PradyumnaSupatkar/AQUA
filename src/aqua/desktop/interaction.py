"""Stage 2B.1: one empty UIA value, with no input/focus/invoke fallbacks."""

from collections import deque
from time import perf_counter

from .backend import (NativeWindows, UIAutomation, BackendUnavailable, _runtime_id,
                      _identity, _property, match_application, VALUE_CONTROL_TYPES)

INTERACTION_OPERATIONS = frozenset({"set_control_text"})
# Capability policy for this first milestone, not an application workflow.
# Browser/remote/editor extension fields are not enabled by this slice.
LOCAL_TEXT_APPS = frozenset({"notepad"})
MAX_TEXT_BYTES = 1024
MAX_WALK_ELEMENTS = 128
WALK_DEADLINE_SECONDS = 3.5


def valid_text(text):
    try:
        return (type(text) is str and 0 < len(text.encode("utf-8")) <= MAX_TEXT_BYTES
                and not any(ord(c) < 32 or ord(c) == 127 for c in text))
    except UnicodeError:
        return False


def value_pattern(element):
    """Installed pywinauto API: pattern_ids + QueryInterface, no duck fallback."""
    from pywinauto.uia_defines import get_elem_interface
    return get_elem_interface(element, "Value")


def result(status, attempted=False, verified=False):
    # Never return old field contents, exception strings, or requested text.
    return {"status": status, "attempted": attempted, "verified": verified}


class InteractionBackend:
    """Retains the partial implementation's bounded finder and identity gates."""

    def __init__(self, *, mock_native=None, mock_ui=None):
        self._mock_native, self._mock_ui = mock_native, mock_ui

    def _get_value_pattern(self, element):
        return value_pattern(element)

    def _find_element_by_runtime_id(self, root, ui, target_runtime_id, deadline):
        queue, visited = deque([root]), 0
        while queue and visited < MAX_WALK_ELEMENTS and perf_counter() < deadline:
            element = queue.popleft()
            visited += 1
            if _property(element, "CurrentIsPassword") is not False:
                continue
            try:
                if _runtime_id(element) == target_runtime_id:
                    return element
                child = ui.first_child(element)
                while child and len(queue) + visited < MAX_WALK_ELEMENTS and perf_counter() < deadline:
                    queue.append(child)
                    child = ui.next_sibling(child)
            except Exception:
                continue
        return None

    def _validate_window(self, native, ui, app, identity):
        info = native.window_info(identity["handle"])
        if (match_application(info, (app,)) != app or
                not native.interaction_allowed(info["pid"])):
            return None
        root = ui.bind(identity["handle"])
        return root if _identity(info, root) == identity else None

    def _security_gate(self, element, ui, identity, runtime_id):
        return (_property(element, "CurrentIsPassword") is False
                and _property(element, "CurrentIsEnabled") is True
                and _property(element, "CurrentIsOffscreen") is False
                and int(element.CurrentProcessId) == identity["pid"]
                and ui.control_type(element) in VALUE_CONTROL_TYPES
                and _runtime_id(element) == runtime_id)

    def set_control_text(self, app, identity, runtime_id, text):
        attempted = False
        try:
            if app not in LOCAL_TEXT_APPS or not valid_text(text):
                return result("not_authorized")
            native = self._mock_native or NativeWindows()
            ui = self._mock_ui or UIAutomation()
            root = self._validate_window(native, ui, app, identity)
            if root is None:
                return result("stale_window")
            element = self._find_element_by_runtime_id(root, ui, runtime_id,
                                                       perf_counter() + WALK_DEADLINE_SECONDS)
            if element is None:
                return result("stale_control")
            if not self._security_gate(element, ui, identity, runtime_id):
                return result("ineligible_control")
            try:
                pattern = self._get_value_pattern(element)
            except Exception:
                return result("unsupported_pattern")
            if _property(pattern, "CurrentIsReadOnly") is not False:
                return result("not_editable")
            # This narrowly authorized precondition never leaves the worker.
            if pattern.CurrentValue != "":
                return result("not_empty")
            if self._validate_window(native, ui, app, identity) is None:
                return result("stale_window")
            if not self._security_gate(element, ui, identity, runtime_id):
                return result("stale_control")
            if _property(pattern, "CurrentIsReadOnly") is not False or pattern.CurrentValue != "":
                return result("not_empty")
            attempted = True  # A failing SetValue can still have had an effect.
            pattern.SetValue(text)
            if (self._validate_window(native, ui, app, identity) is None or
                    not self._security_gate(element, ui, identity, runtime_id)):
                return result("unverified", True)
            verified = pattern.CurrentValue == text
            return result("ok" if verified else "unverified", True, verified)
        except BackendUnavailable:
            return result("unavailable", attempted)
        except Exception:
            return result("error", attempted)


def dispatch_interaction(request, *, native=None, ui=None):
    """No focus, invoke, selection, keyboard, or arbitrary operation dispatcher."""
    try:
        from .session import _identity as validate_identity
        if type(request) is not dict or set(request) != {"operation", "scope", "app_id", "identity", "runtime_id", "text"}:
            return result("not_authorized")
        app = request["app_id"]
        if (request["operation"] != "set_control_text" or app not in LOCAL_TEXT_APPS
                or request["scope"] != [app] or not valid_text(request["text"])):
            return result("not_authorized")
        validate_identity(request["identity"])
        rid = request["runtime_id"]
        if type(rid) is not list or not 1 <= len(rid) <= 32 or any(type(x) is not int for x in rid):
            return result("stale_control")
        return InteractionBackend(mock_native=native, mock_ui=ui).set_control_text(
            app, request["identity"], rid, request["text"])
    except Exception:
        return result("error")
