"""No UI, real processes, network, or mutable desktop APIs in backend tests."""

import json
import subprocess
from time import perf_counter
import unittest
from unittest.mock import Mock, patch

from aqua.actions.launcher import ALLOWED_APPS
from aqua.desktop import backend
from aqua.desktop.session import run_worker


class Element:
    def __init__(self, kind="Window", name="Fixture", children=(), *, password=False, visible=True, pid=10, handle=100):
        self.kind, self._name, self.children = kind, name, list(children)
        self.CurrentProcessId, self.CurrentNativeWindowHandle = pid, handle
        self.CurrentIsPassword, self.CurrentIsOffscreen, self.CurrentIsEnabled = password, not visible, True
        self.name_reads = 0
        self.sibling = None
        for index, child in enumerate(self.children):
            child.sibling = self.children[index + 1] if index + 1 < len(self.children) else None

    @property
    def CurrentName(self):
        self.name_reads += 1
        if isinstance(self._name, BaseException):
            raise self._name
        return self._name

    def GetRuntimeId(self):
        return [42, self.CurrentNativeWindowHandle]


class Native:
    def __init__(self, entries=None):
        self.entries = entries or {100: {"handle": 100, "pid": 10, "created": 123,
                                        "image": ALLOWED_APPS["vs code"][0], "package_family": None}}
        self.truncated, self.title_reads = False, []

    def visible_handles(self, deadline):
        return list(self.entries)

    def window_info(self, handle):
        return self.entries.get(handle)

    def process_info(self, pid):
        return next((row for row in self.entries.values() if row["pid"] == pid), None)

    def title(self, handle):
        self.title_reads.append(handle)
        return "Fixture code window"

    def active(self, handle):
        return handle == 100


class UI:
    def __init__(self, root):
        self.root, self.binds = root, []

    def bind(self, handle):
        self.binds.append(handle)
        return self.root

    def control_type(self, element):
        return element.kind

    def first_child(self, element):
        return element.children[0] if element.children else None

    def next_sibling(self, element):
        return element.sibling


class DesktopBackendTests(unittest.TestCase):
    def setUp(self):
        for target in ("subprocess.Popen", "socket.create_connection", "socket.socket.connect", "socket.getaddrinfo"):
            manager = patch(target, side_effect=AssertionError("Unit tests must be read-only and offline"))
            manager.start()
            self.addCleanup(manager.stop)

    def inspect(self, root, native=None):
        return backend.dispatch({"operation": "inspect_window", "scope": ["vs code"], "app_id": "vs code",
                                 "identity": {"handle": 100, "pid": 10, "created": 123, "runtime_id": [42, 100]}},
                                native=native or Native(), ui=UI(root))

    def test_only_scoped_verified_processes_are_bound_or_have_titles_read(self):
        native = Native({100: {"handle": 100, "pid": 10, "created": 123,
                               "image": ALLOWED_APPS["vs code"][0]},
                         200: {"handle": 200, "pid": 20, "created": 234,
                               "image": r"C:\Other\Code.exe"}})
        ui = UI(Element())
        result = backend.dispatch({"operation": "list_windows", "scope": ["vs code"]}, native=native, ui=ui)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(ui.binds, [100])
        self.assertEqual(native.title_reads, [100])
        self.assertTrue(result["windows"][0]["active"])

    def test_package_identity_supports_real_windows_apps_without_basename_fallback(self):
        info = {"image": r"C:\Program Files\WindowsApps\Fixture\CalculatorApp.exe",
                "package_family": "Microsoft.WindowsCalculator_8wekyb3d8bbwe"}
        self.assertEqual(backend.match_application(info, ("calculator",)), "calculator")
        self.assertIsNone(backend.match_application({"image": info["image"]}, ("calculator",)))
        self.assertIsNone(backend.match_application(info, ("chrome",)))

    def test_password_and_edit_text_names_are_never_read_or_descended(self):
        secret = Element("Button", AssertionError("Password name must never be read"), password=True,
                         children=[Element("Button", "hidden descendant")])
        edit = Element("Edit", AssertionError("Edit content must never be read"),
                       children=[Element("Button", "input descendant")])
        text = Element("Text", AssertionError("Document text must never be read"))
        root = Element(children=[secret, edit, text])
        result = self.inspect(root)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["controls"]), 4)
        self.assertEqual((secret.name_reads, edit.name_reads, text.name_reads), (0, 0, 0))
        self.assertEqual(result["controls"][1]["redacted"], "password")

    def test_document_custom_names_omitted_but_structural_children_visible(self):
        button = Element("Button", "Save")
        document = Element("Document", AssertionError("Document name forbidden"), children=[button])
        custom = Element("Custom", AssertionError("Custom name forbidden"), children=[document])
        result = self.inspect(Element(children=[custom]))
        self.assertEqual((custom.name_reads, document.name_reads), (0, 0))
        self.assertIn("Save", [row.get("name") for row in result["controls"]])

    def test_unavailable_password_status_is_fail_closed(self):
        child = Element("Button", AssertionError("Unknown privacy state"), password=None)
        result = self.inspect(Element(children=[child]))
        self.assertEqual(child.name_reads, 0)
        self.assertEqual(result["controls"][1]["redacted"], "unverified_password_status")

    def test_offscreen_labels_and_unapproved_provider_processes_are_not_read(self):
        invisible = Element("Button", AssertionError("Invisible labels omitted"), visible=False)
        alien = Element("Button", AssertionError("Unapproved process"), pid=999)
        result = self.inspect(Element(children=[invisible, alien]))
        self.assertEqual((invisible.name_reads, alien.name_reads), (0, 0))
        self.assertEqual(len(result["controls"]), 2)

    def test_large_tree_and_labels_are_bounded(self):
        children = [Element("Button", "x" * 1000) for _ in range(100)]
        result = self.inspect(Element(children=children))
        self.assertLessEqual(len(result["controls"]), 32)
        self.assertTrue(result["truncated"])
        self.assertTrue(all(len(row.get("name", "")) <= 80 for row in result["controls"]))

    def test_depth_and_deadline_are_bounded(self):
        root = Element()
        for _ in range(12):
            root = Element(children=[root])
        result = self.inspect(root)
        self.assertLessEqual(max(row["depth"] for row in result["controls"]), 8)
        self.assertTrue(result["truncated"])
        controls, truncated = backend.walk_controls(root, UI(root), Native(), "vs code", 10, -1)
        self.assertEqual(controls, [])
        self.assertTrue(truncated)

    def test_stale_handle_pid_creation_and_runtime_id_fail_closed(self):
        for root, native in ((Element(pid=20), Native()),
                             (Element(handle=101), Native()),
                             (Element(), Native({100: {"handle": 100, "pid": 10, "created": 999,
                                                       "image": ALLOWED_APPS["vs code"][0]}}))):
            with self.subTest(root=root, native=native):
                self.assertEqual(self.inspect(root, native)["status"], "stale_window")

    def test_closed_window_is_stale(self):
        native = Native()
        native.entries.clear()
        self.assertEqual(self.inspect(Element(), native)["status"], "stale_window")

    def test_worker_timeout_kills_only_fixed_worker_and_does_not_expose_stderr(self):
        process = Mock(returncode=0)
        process.communicate.side_effect = [subprocess.TimeoutExpired("worker", 5), (b"", None)]
        with patch("aqua.desktop.session.subprocess.Popen", return_value=process) as spawn:
            result = run_worker({"operation": "list_windows", "scope": ["vs code"]})
        self.assertEqual(result, {"status": "timeout"})
        self.assertEqual(spawn.call_args.args[0][-2:], ["-m", "aqua.desktop.worker"])
        self.assertFalse(spawn.call_args.kwargs["shell"])
        self.assertEqual(spawn.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(process.communicate.call_args_list[0].kwargs["timeout"], 5)
        process.kill.assert_called_once_with()

    def test_native_visibility_excludes_minimized_cloaked_or_unknown_windows(self):
        for visible, iconic, cloaked, status, expected in (
            (True, False, 0, 0, True), (False, False, 0, 0, False),
            (True, True, 0, 0, False), (True, False, 1, 0, False),
            (True, False, 2, 0, False), (True, False, 4, 0, False),
            (True, False, 0, -1, False),
        ):
            with self.subTest(visible=visible, iconic=iconic, cloaked=cloaked, status=status):
                native = object.__new__(backend.NativeWindows)
                native.user = Mock()
                native.user.IsWindow.return_value = True
                native.user.IsWindowVisible.return_value = visible
                native.user.IsIconic.return_value = iconic
                native.dwm = Mock()

                def attribute(handle, key, pointer, size):
                    self.assertEqual(key, 14)
                    pointer._obj.value = cloaked
                    return status

                native.dwm.DwmGetWindowAttribute.side_effect = attribute
                self.assertIs(native.is_observable(100), expected)
                if not expected:
                    native.process_info = Mock()
                    self.assertIsNone(native.window_info(100))
                    native.process_info.assert_not_called()

    def test_native_discovery_uses_verified_visibility_and_fails_closed_on_dwm_error(self):
        native = object.__new__(backend.NativeWindows)
        native.truncated = False
        native.callback_type = lambda callback: callback
        native.user = Mock()
        native.user.EnumWindows.side_effect = lambda callback, parameter: [callback(handle, parameter) for handle in (100, 200)]
        native.is_observable = Mock(side_effect=[True, False])
        self.assertEqual(native.visible_handles(perf_counter() + 1), [100])
        native.is_observable = backend.NativeWindows.is_observable.__get__(native)
        native.user.IsWindow.return_value = True
        native.user.IsWindowVisible.return_value = True
        native.user.IsIconic.return_value = False
        native.dwm = Mock()
        native.dwm.DwmGetWindowAttribute.side_effect = OSError("not available")
        self.assertFalse(native.is_observable(100))

    def test_keyboard_interrupt_kills_and_reaps_only_worker_with_bounded_wait(self):
        for final in ((b"", None), subprocess.TimeoutExpired("worker", .5)):
            with self.subTest(final=final):
                process = Mock(returncode=0)
                process.poll.return_value = None
                process.communicate.side_effect = [KeyboardInterrupt(), final]
                with patch("aqua.desktop.session.subprocess.Popen", return_value=process), self.assertRaises(KeyboardInterrupt):
                    run_worker({"operation": "list_windows", "scope": ["vs code"]})
                process.kill.assert_called_once_with()
                self.assertEqual(process.communicate.call_args_list[1].kwargs, {"timeout": .5})
                process.stdin.close.assert_called_once_with()
                process.stdout.close.assert_called_once_with()

    def test_worker_valid_json_and_oversized_output_handled(self):
        for output, expected in ((b'{"status":"empty","windows":[]}', "empty"), (b"x" * 32769, "error"), (b"not-json", "error")):
            with self.subTest(expected=expected):
                process = Mock(returncode=0)
                process.communicate.return_value = (output, None)
                with patch("aqua.desktop.session.subprocess.Popen", return_value=process):
                    self.assertEqual(run_worker({"operation": "list_windows", "scope": ["vs code"]})["status"], expected)


if __name__ == "__main__":
    unittest.main()
