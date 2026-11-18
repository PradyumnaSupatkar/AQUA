"""Read-only probe regressions. All UI objects/processes are synthetic."""

from contextlib import redirect_stdout
from io import StringIO
import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aqua.actions.launcher import ALLOWED_APPS
from tools import diagnose_notepad as diagnostic
from test_desktop_backend import Element, Native, UI


class NoPatternInterfaceError(Exception):
    pass


class NotepadDiagnosticTests(unittest.TestCase):
    def editor(self, **options):
        item = Element("Document", AssertionError("Never read content/name"), handle=101, **options)
        item.CurrentIsKeyboardFocusable, item.CurrentHasKeyboardFocus = True, False
        return item

    def pattern(self, sample="", collapsed=True, value=""):
        span = Mock()
        span.GetText.return_value = sample
        span.CompareEndpoints.return_value = 0 if collapsed else -1
        value_pattern = SimpleNamespace(CurrentIsReadOnly=False, CurrentValue=value)
        pattern = Mock(side_effect=lambda _e, name: SimpleNamespace(DocumentRange=span) if name == "Text" else value_pattern)
        return pattern, span

    def fixture(self):
        document = self.editor()
        root = Element(children=[document])
        native = Native()
        native.entries[100]["image"] = ALLOWED_APPS["notepad"][0]
        native.interaction_allowed = Mock(return_value=True)
        return native, UI(root), document

    def test_empty_text_value_focus_and_private_identity_metadata_only(self):
        element = self.editor()
        getter, span = self.pattern()
        result = diagnostic.describe_editor(element, getter, ("Text", "Value"))
        self.assertEqual(result["text_empty_state"], "empty")
        self.assertEqual(result["value_empty_state"], "empty")
        self.assertTrue(result["private_runtime_identity_available"])
        self.assertTrue(result["IsKeyboardFocusable"])
        self.assertFalse(result["HasKeyboardFocus"])
        self.assertFalse(result["value_readonly"])
        span.GetText.assert_called_once_with(1)
        span.CompareEndpoints.assert_called_once_with(0, span, 1)
        self.assertEqual(element.name_reads, 0)
        self.assertNotIn("runtime_id", result)

    def test_nonempty_prefix_or_whitespace_never_logged_or_called_empty(self):
        for sample in ("P", " ", "\r"):
            with self.subTest(sample=sample):
                class UnreadValue:
                    CurrentIsReadOnly = False
                    @property
                    def CurrentValue(self):
                        raise AssertionError("No Value read when Text is nonempty")
                getter, span = self.pattern(sample=sample, collapsed=False)
                text = SimpleNamespace(DocumentRange=span)
                result = diagnostic.describe_editor(self.editor(), lambda _e, n: text if n == "Text" else UnreadValue(), ("Text", "Value"))
                self.assertEqual(result["text_empty_state"], "nonempty")
                self.assertNotIn("value_empty_state", result)
                self.assertNotIn("value_read_error", result)
                self.assertNotIn("sample", result)

    def test_inconsistent_text_range_and_concurrent_value_edit_not_empty(self):
        getter, _ = self.pattern(collapsed=False)
        result = diagnostic.describe_editor(self.editor(), getter, ("Text", "Value"))
        self.assertEqual(result["text_empty_state"], "inconsistent_or_unknown")
        self.assertNotIn("value_empty_state", result)
        getter, _ = self.pattern(value="PRIVATE concurrent document")
        result = diagnostic.describe_editor(self.editor(), getter, ("Text", "Value"))
        self.assertEqual(result["value_empty_state"], "nonempty")
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_unsupported_pattern_is_distinct_from_provider_error(self):
        for error, expected in ((NoPatternInterfaceError("PRIVATE"), "absent"), (OSError("PRIVATE"), "OSError")):
            getter = Mock(side_effect=error)
            result = diagnostic.describe_editor(self.editor(), getter, ("Text", "Value"))
            self.assertEqual(result["patterns"]["Text"], expected)
            self.assertEqual(result["text_empty_state"], "unknown")
            self.assertNotIn("PRIVATE", json.dumps(result))

    def test_sensitive_hidden_disabled_or_unknown_privacy_never_queries_patterns(self):
        for options in ({"password": True}, {"password": None}, {"visible": False}, {}):
            element = self.editor(**options)
            if not options:
                element.CurrentIsEnabled = False
            getter = Mock(side_effect=AssertionError("No pattern/content access"))
            result = diagnostic.describe_editor(element, getter, ("Text", "Value"))
            self.assertEqual(result["status"], "ineligible")
            getter.assert_not_called()

    def test_probe_revalidates_identity_and_never_returns_private_ids(self):
        native, ui, document = self.fixture()
        getter, _ = self.pattern()
        result = diagnostic.probe(native=native, ui=ui, get_pattern=getter, pattern_names=("Text", "Value"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["editors"][0]["control_type"], "Document")
        self.assertEqual(result["editors"][0]["depth"], 1)
        self.assertEqual(result["editors"][0]["value_empty_state"], "empty")
        self.assertEqual(document.name_reads, 0)
        self.assertNotIn('"runtime_id":', json.dumps(result))
        self.assertNotIn("[42, 101]", json.dumps(result))
        native.window_info = Mock(side_effect=[native.entries[100], {**native.entries[100], "created": 999}])
        result = diagnostic.probe(native=native, ui=ui, get_pattern=getter, pattern_names=("Text",))
        self.assertEqual(result, {"status": "stale_window"})

    def test_wrong_app_or_privilege_rejects_before_uia_binding(self):
        for mode in ("wrong_app", "privileged"):
            native, ui, _ = self.fixture()
            ui.bind = Mock(side_effect=AssertionError("Do not bind"))
            if mode == "wrong_app": native.entries[100]["image"] = r"C:\Unknown\Notepad.exe"
            else: native.interaction_allowed.return_value = False
            result = diagnostic.probe(native=native, ui=ui, get_pattern=Mock(), pattern_names=())
            self.assertEqual(result["status"], "empty" if mode == "wrong_app" else "protected")
            ui.bind.assert_not_called()

    def test_large_tree_is_bounded(self):
        native, _, _ = self.fixture()
        ui = UI(Element(children=[Element("Button") for _ in range(300)]))
        result = diagnostic.probe(native=native, ui=ui, get_pattern=Mock(), pattern_names=())
        self.assertTrue(result["truncated"])
        self.assertLessEqual(result["visited"], diagnostic.MAX_NODES)

    def test_probe_keeps_progress_and_emits_categorized_error_without_message(self):
        def fail(**kwargs):
            kwargs["progress"]("uia_binding")
            raise OSError("PRIVATE provider message")
        out = StringIO()
        with patch.object(diagnostic, "probe", side_effect=fail), patch.object(diagnostic, "emit_progress") as progress, redirect_stdout(out):
            diagnostic.child_probe()
        progress.assert_called_once_with("uia_binding")
        self.assertEqual(json.loads(out.getvalue())["result"], {"status": "error", "exception": "OSError"})
        self.assertNotIn("PRIVATE", out.getvalue())

    def test_killable_child_reports_timeout_and_last_stage_without_stderr_dump(self):
        process = Mock(returncode=-1)
        process.communicate.side_effect = [subprocess.TimeoutExpired("fixed worker", 6),
                                           (b'{"stage":"uia_binding"}\n', b'UserWarning: PRIVATE')]
        with patch.object(diagnostic.subprocess, "Popen", return_value=process) as start:
            result = diagnostic.isolated_probe()
        self.assertTrue(result["probe_process"]["timeout"])
        self.assertTrue(result["probe_process"]["warning_detected"])
        self.assertEqual(result["records"], [{"stage": "uia_binding"}])
        self.assertNotIn("PRIVATE", json.dumps(result))
        process.kill.assert_called_once()
        self.assertFalse(start.call_args.kwargs["shell"])
        self.assertIn("--_worker", start.call_args.args[0])

    def test_live_flag_required_before_any_desktop_access(self):
        with patch.object(diagnostic, "isolated_probe") as probe, patch.object(diagnostic, "discovery_trials") as trials, redirect_stdout(StringIO()):
            self.assertEqual(diagnostic.main([]), 2)
        probe.assert_not_called()
        trials.assert_not_called()

    def test_warning_only_or_failed_probe_is_not_a_successful_diagnosis(self):
        reports = ({"records": [{"stage": "uia_initialization"}], "probe_process": {"timeout": False, "returncode": 0}},
                   {"records": [{"result": {"status": "empty"}}], "probe_process": {"timeout": False, "returncode": 0}},
                   {"records": [{"result": {"status": "ok"}}], "probe_process": {"timeout": True, "returncode": -1}})
        for report in reports:
            with patch.object(diagnostic, "isolated_probe", return_value=report), patch.object(diagnostic, "discovery_trials"), redirect_stdout(StringIO()):
                self.assertEqual(diagnostic.main(["--live"]), 1)


if __name__ == "__main__":
    unittest.main()
