"""Focused 2B.1 tests: no real desktop input, processes or network."""

from copy import deepcopy
from io import BytesIO, StringIO
from types import SimpleNamespace
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from aqua.actions import tools
from aqua.actions.launcher import ALLOWED_APPS
from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import ChatEvent, DEFAULT_CHAT_MODEL, GenerationSettings, OllamaError
from aqua.desktop import backend, worker
from aqua.desktop.interaction import InteractionBackend, dispatch_interaction, valid_text
from aqua.desktop.interaction_session import InteractionSession, authorize_text_entry
from aqua.desktop.session import ObservationSession, run_worker
from test_desktop_backend import Element, Native, UI

IDENTITY = {"handle": 100, "pid": 10, "created": 123, "runtime_id": [42, 100]}
REQUEST = "Hey Aqua, write Hello from Aqua into my open Notepad window."
TEXT = "Hello from Aqua"


class Guarded(unittest.TestCase):
    def setUp(self):
        for name in ("subprocess.Popen", "socket.create_connection", "socket.socket.connect", "socket.getaddrinfo"):
            manager = patch(name, side_effect=AssertionError("No real UI effects or network in automated tests"))
            manager.start()
            self.addCleanup(manager.stop)


class Pattern:
    def __init__(self, value="", readonly=False):
        self.value, self.CurrentIsReadOnly = value, readonly
        self.calls, self.reads = [], 0
        self.fail, self.verify = False, True

    @property
    def CurrentValue(self):
        self.reads += 1
        return self.value

    def SetValue(self, text):
        self.calls.append(text)
        self.value = text if self.verify else "mismatch"
        if self.fail:
            raise OSError("PRIVATE provider failure after potential side effect")


class TextBackendTests(Guarded):
    def build(self, **options):
        self.edit = Element("Edit", AssertionError("Never read editor name/body"), handle=101, **options)
        self.root = Element(children=[self.edit])
        self.native = Native()
        self.native.entries[100]["image"] = ALLOWED_APPS["notepad"][0]
        self.native.interaction_allowed = Mock(return_value=True)
        self.pattern = Pattern()
        self.engine = InteractionBackend(mock_native=self.native, mock_ui=UI(self.root))
        self.engine._get_value_pattern = Mock(return_value=self.pattern)

    def enter(self, app="notepad", identity=None, rid=None):
        return self.engine.set_control_text(app, identity or IDENTITY, rid or [42, 101], TEXT)

    def test_verified_empty_edit_write_and_actual_readback(self):
        self.build()
        self.assertEqual(self.enter(), {"status": "ok", "attempted": True, "verified": True})
        self.assertEqual(self.pattern.calls, [TEXT])
        self.assertGreaterEqual(self.pattern.reads, 3)
        self.assertEqual(self.edit.name_reads, 0)

    def test_nonempty_including_whitespace_is_never_replaced_or_returned(self):
        for value in ("PRIVATE_CONTENT", " ", "\n"):
            with self.subTest(value=value):
                self.build()
                self.pattern.value = value
                result = self.enter()
                self.assertEqual(result["status"], "not_empty")
                self.assertFalse(result["attempted"])
                self.assertEqual(self.pattern.calls, [])
                self.assertNotIn("PRIVATE_CONTENT", json.dumps(result))

    def test_password_unknown_privacy_disabled_hidden_and_nonedit_rejected(self):
        for changes in ({"CurrentIsPassword": True}, {"CurrentIsPassword": None},
                        {"CurrentIsEnabled": False}, {"CurrentIsOffscreen": True}, {"kind": "Button"}):
            with self.subTest(changes=changes):
                self.build()
                for key, value in changes.items():
                    setattr(self.edit, key, value)
                result = self.enter()
                self.assertFalse(result["attempted"])
                self.assertEqual(self.pattern.calls, [])
                self.assertEqual(self.pattern.reads, 0)

    def test_readonly_or_unsupported_pattern_never_reads_or_writes_value(self):
        self.build()
        self.pattern.CurrentIsReadOnly = True
        self.assertEqual(self.enter()["status"], "not_editable")
        self.assertEqual(self.pattern.reads, 0)
        self.engine._get_value_pattern.side_effect = LookupError()
        self.assertEqual(self.enter()["status"], "unsupported_pattern")
        self.assertEqual(self.pattern.calls, [])

    def test_wrong_app_window_runtime_pid_and_privilege_fail_closed(self):
        for mode in ("app", "window", "control", "pid", "privileged"):
            with self.subTest(mode=mode):
                self.build()
                if mode == "pid": self.edit.CurrentProcessId = 999
                if mode == "privileged": self.native.interaction_allowed.return_value = False
                if mode == "app": self.native.entries[100]["image"] = ALLOWED_APPS["chrome"][0]
                result = self.enter(
                                    identity={**IDENTITY, "created": 456} if mode == "window" else None,
                                    rid=[42, 999] if mode == "control" else None)
                self.assertFalse(result["attempted"])
                self.assertEqual(self.pattern.calls, [])

    def test_field_changed_between_empty_checks_is_not_replaced(self):
        self.build()
        values = iter(("", "User typed concurrently"))
        class Changing(Pattern):
            @property
            def CurrentValue(self): return next(values)
        changed = Changing()
        self.engine._get_value_pattern.return_value = changed
        self.assertEqual(self.enter()["status"], "not_empty")
        self.assertEqual(changed.calls, [])

    def test_api_return_is_not_verification_and_exception_can_follow_effect(self):
        for mode in ("mismatch", "exception"):
            with self.subTest(mode=mode):
                self.build()
                self.pattern.verify = mode != "mismatch"
                self.pattern.fail = mode == "exception"
                result = self.enter()
                self.assertTrue(result["attempted"])
                self.assertFalse(result["verified"])
                self.assertNotIn("PRIVATE", json.dumps(result))
                self.assertEqual(len(self.pattern.calls), 1)

    def test_dispatch_exposes_only_set_value_and_rechecks_real_app(self):
        self.build()
        base = {"operation": "set_control_text", "scope": ["notepad"], "app_id": "notepad",
                "identity": deepcopy(IDENTITY), "runtime_id": [42, 101], "text": TEXT}
        for operation in ("focus_window", "invoke_control", "select_control", "run_shell"):
            result = dispatch_interaction({**base, "operation": operation}, native=self.native, ui=UI(self.root))
            self.assertFalse(result["attempted"])
        result = dispatch_interaction({**base, "scope": ["chrome"], "app_id": "chrome"},
                                      native=self.native, ui=UI(self.root))
        self.assertFalse(result["attempted"])

    def test_discovery_exports_private_edit_identity_only_when_enabled(self):
        self.build()
        with patch("aqua.desktop.interaction.value_pattern", return_value=self.pattern):
            plain, _ = backend.walk_controls(self.root, UI(self.root), self.native, "notepad", 10, float("inf"))
            editable, _ = backend.walk_controls(self.root, UI(self.root), self.native, "notepad", 10, float("inf"), discover_editable=True)
        self.assertNotIn("runtime_id", plain[1])
        self.assertEqual(editable[1]["runtime_id"], [42, 101])
        self.assertTrue(editable[1]["editable"])
        self.assertEqual(self.pattern.reads, 0)

    def test_document_value_pattern_discovery_and_verified_entry(self):
        self.build()
        self.edit.kind = "Document"
        with patch("aqua.desktop.interaction.value_pattern", return_value=self.pattern):
            plain, _ = backend.walk_controls(self.root, UI(self.root), self.native, "notepad", 10, float("inf"))
            enabled, _ = backend.walk_controls(self.root, UI(self.root), self.native, "notepad", 10, float("inf"), discover_editable=True)
        self.assertNotIn("editable", plain[1])
        self.assertTrue(enabled[1]["editable"])
        self.assertEqual(enabled[1]["runtime_id"], [42, 101])
        self.assertEqual(self.pattern.reads, 0)  # Discovery never reads a value.
        self.assertEqual(self.enter(), {"status": "ok", "attempted": True, "verified": True})
        self.assertEqual(self.pattern.calls, [TEXT])
        self.assertEqual(self.edit.name_reads, 0)

    def test_document_without_writable_value_has_no_input_fallback(self):
        for mode in ("text_only", "readonly"):
            with self.subTest(mode=mode):
                self.build()
                self.edit.kind = "Document"
                if mode == "text_only":
                    self.engine._get_value_pattern.side_effect = LookupError()
                else:
                    self.pattern.CurrentIsReadOnly = True
                with patch("aqua.desktop.interaction.value_pattern", self.engine._get_value_pattern):
                    rows, _ = backend.walk_controls(self.root, UI(self.root), self.native, "notepad", 10, float("inf"), discover_editable=True)
                self.assertNotIn("editable", rows[1])
                result = self.enter()
                self.assertEqual(result["status"], "unsupported_pattern" if mode == "text_only" else "not_editable")
                self.assertFalse(result["attempted"])
                self.assertEqual((self.pattern.calls, self.pattern.reads), ([], 0))

    def test_document_security_and_nonempty_guards_still_apply(self):
        for mode in ("password", "unknown_password", "disabled", "offscreen", "wrong_pid", "privileged", "nonempty", "whitespace"):
            with self.subTest(mode=mode):
                self.build()
                self.edit.kind = "Document"
                if mode == "password": self.edit.CurrentIsPassword = True
                if mode == "unknown_password": self.edit.CurrentIsPassword = None
                if mode == "disabled": self.edit.CurrentIsEnabled = False
                if mode == "offscreen": self.edit.CurrentIsOffscreen = True
                if mode == "wrong_pid": self.edit.CurrentProcessId = 999
                if mode == "privileged": self.native.interaction_allowed.return_value = False
                if mode == "nonempty": self.pattern.value = "PRIVATE_DOCUMENT"
                if mode == "whitespace": self.pattern.value = " "
                result = self.enter()
                self.assertFalse(result["attempted"])
                self.assertFalse(result["verified"])
                self.assertEqual(self.pattern.calls, [])
                self.assertNotIn("PRIVATE_DOCUMENT", json.dumps(result))

    def test_document_concurrent_edit_blocks_setvalue(self):
        self.build()
        self.edit.kind = "Document"
        values = iter(("", "PRIVATE concurrent edit"))
        class Changing(Pattern):
            @property
            def CurrentValue(self): return next(values)
        changed = Changing()
        self.engine._get_value_pattern.return_value = changed
        self.assertEqual(self.enter()["status"], "not_empty")
        self.assertEqual(changed.calls, [])

    def test_document_readback_failure_is_not_verified(self):
        self.build()
        self.edit.kind = "Document"
        self.pattern.verify = False
        self.assertEqual(self.enter(), {"status": "unverified", "attempted": True, "verified": False})


def raw_window():
    return {"app_id": "notepad", "identity": deepcopy(IDENTITY), "title": "Fixture", "active": True}


def raw_inspection():
    return {"status": "ok", "app_id": "notepad", "identity": deepcopy(IDENTITY), "controls": [
        {"control_type": "Edit", "password": False, "enabled": True, "visible": True,
         "depth": 1, "editable": True, "runtime_id": [42, 101], "name": "PRIVATE_CONTENT"}], "truncated": False}


class TextSessionTests(Guarded):
    def make_session(self, result=None):
        runner = Mock(side_effect=[{"status": "ok", "windows": [raw_window()]}, raw_inspection()])
        self.observer = ObservationSession(runner)
        self.observer.begin_task(("notepad",))
        self.observer.enable_editable_controls()
        self.writer = Mock(return_value=result or {"status": "ok", "attempted": True, "verified": True})
        self.session = InteractionSession(self.observer, self.writer)
        self.session.begin_task(("notepad",), TEXT)
        self.window = self.observer.list_windows()["data"]["windows"][0]["window_id"]
        self.public = self.observer.inspect_window(self.window)
        return self.session.register_controls(self.window)["c0"]

    def test_private_runtime_ids_never_cross_public_boundary(self):
        token = self.make_session()
        self.assertRegex(token, r"^ci_[A-Za-z0-9_-]+$")
        self.assertNotIn("runtime_id", json.dumps(self.public))
        self.assertNotIn("PRIVATE_CONTENT", json.dumps(self.public))
        self.assertNotIn("identity", json.dumps(self.public))
        result = self.session.set_control_text(token)
        self.assertTrue(result["data"]["verified"])
        self.assertEqual(self.writer.call_args.args[0]["runtime_id"], [42, 101])
        self.assertEqual(self.writer.call_args.args[0]["text"], TEXT)

    def test_begin_task_clear_and_ttl_expire_old_tokens(self):
        token = self.make_session()
        issued = self.session._control_refs[token]["issued_at"]
        with patch("aqua.desktop.interaction_session.perf_counter", return_value=issued + 61):
            self.assertEqual(self.session.control_tokens, ())
            self.assertEqual(self.session.set_control_text(token)["status"], "stale_control")
        self.session.begin_task(("notepad",), "Other")
        self.assertEqual(self.session.control_tokens, ())
        self.assertEqual(self.session.set_control_text(token)["status"], "stale_control")
        self.writer.assert_not_called()

    def test_new_grant_cannot_register_previous_task_inspection(self):
        self.make_session()
        self.session.begin_task(("notepad",), "Different authorized literal")
        self.assertEqual(self.session.register_controls(self.window), {})
        self.assertEqual(self.session.control_tokens, ())

    def test_stale_window_and_spoofed_binding_cannot_dispatch(self):
        token = self.make_session()
        self.observer._refs[self.window]["identity"]["created"] = 999
        self.assertEqual(self.session.set_control_text(token)["status"], "not_authorized")
        self.writer.assert_not_called()
        self.assertEqual(self.session.register_controls("w_unknown000"), {})

    def test_reinspection_changed_control_revokes_earlier_reference(self):
        token = self.make_session()
        self.observer._private_controls[self.window][0]["runtime_id"] = [42, 999]
        self.assertEqual(self.session.set_control_text(token)["status"], "stale_control")
        replacement = self.session.register_controls(self.window)["c0"]
        self.assertNotEqual(token, replacement)
        self.assertEqual(self.session.set_control_text(token)["status"], "stale_control")
        self.writer.assert_not_called()

    def test_unique_window_and_field_required(self):
        self.make_session()
        self.observer._private_controls[self.window] *= 2
        self.session.clear()
        self.session.begin_task(("notepad",), TEXT)
        self.assertEqual(self.session.register_controls(self.window), {})
        self.observer._private_controls[self.window] = self.observer._private_controls[self.window][:1]
        self.observer._refs["w_otherwindow"] = deepcopy(self.observer._refs[self.window])
        self.assertEqual(self.session.register_controls(self.window), {})

    def test_unknown_or_repeated_token_cannot_dispatch_again(self):
        token = self.make_session()
        self.assertEqual(self.session.set_control_text("ci_fake000000")["status"], "stale_control")
        self.session.set_control_text(token)
        self.assertEqual(self.session.set_control_text(token)["status"], "stale_control")
        self.writer.assert_called_once()

    def test_timeout_exception_and_unverified_success_are_unknown_not_verified(self):
        for raw in ({"status": "timeout"}, {"status": "ok", "verified": False},
                    {"status": "unverified", "attempted": True, "verified": False}):
            with self.subTest(raw=raw):
                token = self.make_session(raw)
                result = self.session.set_control_text(token)
                self.assertFalse(result["data"]["verified"])
                self.assertIsNot(result["data"]["attempted"], False)
                self.assertEqual(self.session.control_tokens, ())

    def test_runner_exception_consumes_token_without_claiming_no_effect(self):
        token = self.make_session()
        self.writer.side_effect = OSError("PRIVATE")
        result = self.session.set_control_text(token)
        self.assertIsNone(result["data"]["attempted"])
        self.assertFalse(result["data"]["verified"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(self.session.control_tokens, ())

    def test_current_task_consent_is_not_supplied_by_model_or_ui(self):
        self.assertTrue(authorize_text_entry(REQUEST, "notepad", TEXT, ("notepad",)))
        for request, app, text, enabled in (
            (REQUEST, "notepad", "Altered", ("notepad",)), (REQUEST, "notepad", TEXT, ()),
            ("Don't " + REQUEST, "notepad", TEXT, ("notepad",)),
            ('Say "' + REQUEST + '"', "notepad", TEXT, ("notepad",)),
            ("If I asked you to " + REQUEST, "notepad", TEXT, ("notepad",)),
            (REQUEST + " And delete my files.", "notepad", TEXT, ("notepad",)),
            (REQUEST, "chrome", TEXT, ("chrome",)), ("Yes", "notepad", TEXT, ("notepad",))):
            with self.subTest(request=request, app=app):
                self.assertFalse(authorize_text_entry(request, app, text, enabled))

    def test_bytes_not_character_count_and_no_control_input(self):
        self.assertTrue(valid_text("雪" * 300))
        for text in ("雪" * 400, "x" * 1025, "hello\n", "\x00", ""):
            self.assertFalse(valid_text(text))


def event(name, args):
    return [ChatEvent(done=True, tool_calls=({"function": {"name": name, "arguments": args}},))]


class TextAgentTests(Guarded):
    def engine(self, *, worker_status="ok", worker_verified=True, cancel=False, stop=False):
        self.worker_calls, self.model_calls = [], []
        def runner(request):
            self.worker_calls.append(deepcopy(request))
            if request["operation"] == "list_windows":
                return {"status": "ok", "windows": [raw_window()]}
            if request["operation"] == "inspect_window":
                return raw_inspection()
            if cancel: raise KeyboardInterrupt
            return {"status": worker_status, "attempted": True, "verified": worker_verified}
        self.conversation = Conversation()
        self.conversation.text_entry_apps = ("notepad",)
        self.conversation.desktop_observer = ObservationSession(runner)
        self.conversation._desktop_interaction = InteractionSession(self.conversation.desktop_observer, runner)
        client = Mock()
        def model(messages, **options):
            self.model_calls.append((deepcopy(messages), deepcopy(options)))
            number = len(self.model_calls)
            if number == 1: return iter(event("request_text_entry", {"app_id": "notepad", "text": TEXT}))
            if number == 2: return iter(event("list_windows", {}))
            if number == 3:
                window = json.loads(messages[-1]["content"])["data"]["windows"][0]["window_id"]
                return iter(event("inspect_window", {"window_id": window}))
            if number == 4:
                if stop: return iter([ChatEvent("I cannot continue."), ChatEvent(done=True)])
                token = json.loads(messages[-1]["content"])["data"]["controls"][0]["control_token"]
                return iter(event("set_control_text", {"control_id": token}))
            return iter([ChatEvent("I completed everything successfully!"), ChatEvent(done=True)])
        client.stream_chat.side_effect = model
        return ConversationController(client, self.conversation, model=DEFAULT_CHAT_MODEL, settings=GenerationSettings(temperature=.2))

    def test_end_to_end_model_selects_observed_control_and_receives_real_result(self):
        result = self.engine().respond(REQUEST)
        self.assertTrue(result.ok)
        self.assertEqual((result.task.decisions, len(result.task.operations)), (5, 4))
        self.assertEqual([r["operation"] for r in self.worker_calls], ["list_windows", "inspect_window", "set_control_text"])
        self.assertEqual(self.worker_calls[-1]["text"], TEXT)
        self.assertIn("verified by UIA readback", result.text)
        self.assertNotIn("completed everything", result.text)
        self.assertNotIn("runtime_id", json.dumps(self.conversation.history))
        self.assertEqual(self.conversation.turn_count, 1)
        self.assertEqual([m["role"] for m in self.conversation.history], ["user", "assistant", "tool", "assistant", "tool", "assistant", "tool", "assistant", "tool", "assistant"])
        self.assertEqual(json.loads(self.model_calls[-1][0][-1]["content"])["data"]["verified"], True)
        self.assertEqual(self.model_calls[-1][1]["tools"], [])
        self.assertEqual(self.conversation.desktop_interaction.control_tokens, ())

    def test_unverified_worker_never_becomes_fabricated_success(self):
        result = self.engine(worker_status="unverified", worker_verified=False).respond(REQUEST)
        self.assertFalse(result.ok)
        self.assertEqual(result.task.status, "unknown")
        self.assertIn("outcome was not verified", result.text)
        self.assertEqual(len([r for r in self.worker_calls if r["operation"] == "set_control_text"]), 1)

    def test_cancelled_write_records_unknown_and_expires_tokens(self):
        result = self.engine(cancel=True).respond(REQUEST)
        self.assertEqual(result.task.status, "cancelled")
        self.assertIn("not verified", result.text)
        self.assertEqual(self.conversation.desktop_interaction.control_tokens, ())
        self.assertEqual(len(result.task.unknown_operations), 1)
        tools_history = [json.loads(m["content"]) for m in self.conversation.history if m["role"] == "tool"]
        self.assertEqual(tools_history[-1]["status"], "unknown")

    def test_unknown_outcome_is_journaled_before_worker_can_have_an_effect(self):
        engine = self.engine()
        original = self.conversation.desktop_interaction.runner
        def check(request):
            pending = json.loads(self.conversation.history[-1]["content"])
            self.assertEqual(pending["status"], "unknown")
            self.assertIsNone(pending["data"]["attempted"])
            self.assertEqual(self.conversation.last_task.operations[-1].status, "text_entry_unknown")
            return original(request)
        self.conversation.desktop_interaction.runner = check
        self.assertTrue(engine.respond(REQUEST).ok)

    def test_document_without_value_reports_actual_limitation_without_auto_fallback(self):
        engine = self.engine()
        original = self.conversation.desktop_observer.runner
        def document(request):
            raw = original(request)
            if request["operation"] == "inspect_window":
                raw["controls"][0]["control_type"] = "Document"
                # Real discovery cannot mark a TextPattern-only Document editable.
                raw["controls"][0].pop("editable")
                raw["controls"][0].pop("runtime_id")
            return raw
        self.conversation.desktop_observer.runner = document
        result = engine.respond(REQUEST)
        self.assertFalse(result.ok)
        self.assertIn("no eligible writable ValuePattern Edit", result.text)
        self.assertFalse(any(r["operation"] == "set_control_text" for r in self.worker_calls))

    def test_document_value_reference_reaches_controller_and_preserves_history(self):
        engine = self.engine()
        original = self.conversation.desktop_observer.runner
        def document(request):
            raw = original(request)
            if request["operation"] == "inspect_window":
                raw["controls"][0]["control_type"] = "Document"
            return raw
        self.conversation.desktop_observer.runner = document
        result = engine.respond(REQUEST)
        self.assertTrue(result.ok)
        self.assertEqual((result.task.decisions, len(result.task.operations)), (5, 4))
        self.assertTrue(self.worker_calls[-1]["operation"] == "set_control_text")
        inspected = json.loads(self.model_calls[3][0][-1]["content"])
        self.assertEqual(inspected["data"]["controls"][0]["control_type"], "Document")
        self.assertTrue(inspected["data"]["controls"][0]["control_token"].startswith("ci_"))
        self.assertNotIn("runtime_id", json.dumps(self.conversation.history))
        self.assertIn("verified by UIA readback", result.text)

    def test_missing_open_window_is_not_misreported_as_a_pattern_limitation(self):
        engine = self.engine()
        self.conversation.desktop_observer.runner = lambda _r: {"status": "empty", "windows": []}
        result = engine.respond(REQUEST)
        self.assertFalse(result.ok)
        self.assertIn("No approved visible editor window", result.text)
        self.assertEqual(len(result.task.operations), 2)

    def test_model_refusal_before_write_does_not_auto_execute(self):
        result = self.engine(stop=True).respond(REQUEST)
        self.assertFalse(result.ok)
        self.assertIn("No text-entry operation", result.text)
        self.assertFalse(any(r["operation"] == "set_control_text" for r in self.worker_calls))

    def test_missing_session_opt_in_fails_before_any_desktop_read(self):
        engine = self.engine()
        self.conversation.text_entry_apps = ()
        result = engine.respond(REQUEST)
        self.assertFalse(result.ok)
        self.assertEqual(self.worker_calls, [])

    def test_limits_prevent_write_after_discovery_and_inspection(self):
        for name in ("max_tool_operations", "max_model_decisions"):
            with self.subTest(limit=name):
                engine = self.engine()
                setattr(engine, name, 3)
                result = engine.respond(REQUEST)
                self.assertFalse(result.ok)
                self.assertFalse(any(r["operation"] == "set_control_text" for r in self.worker_calls))
                self.assertLessEqual(result.task.decisions, 4)
                self.assertEqual(len(result.task.operations), 3)

    def test_duplicate_write_and_error_after_verification_do_not_repeat_effect(self):
        for fail in (False, True):
            with self.subTest(model_error=fail):
                engine = self.engine()
                original = engine.client.stream_chat.side_effect
                def followup(messages, **options):
                    if len(self.model_calls) == 4:
                        if fail: raise OllamaError("local test failure")
                        token = next(iter(self.conversation.desktop_interaction._control_refs))
                        return iter(event("set_control_text", {"control_id": token}))
                    return original(messages, **options)
                engine.client.stream_chat.side_effect = followup
                result = engine.respond(REQUEST)
                self.assertEqual(len([r for r in self.worker_calls if r["operation"] == "set_control_text"]), 1)
                self.assertIn("verified by UIA readback", result.text)
                self.assertEqual(self.conversation.desktop_interaction.control_tokens, ())

    def test_wrong_proposed_payload_and_stale_confirmation_fail_closed(self):
        engine = self.engine()
        engine.client.stream_chat.side_effect = lambda *_a, **_k: iter(event("request_text_entry", {"app_id": "notepad", "text": "UI changed payload"}))
        result = engine.respond(REQUEST)
        self.assertFalse(result.ok)
        self.assertEqual(self.worker_calls, [])
        result = engine.respond("Yes, go ahead.")
        self.assertEqual(result.outcome, "stale_confirmation")
        self.assertEqual(self.worker_calls, [])

    def test_wrong_literal_or_negated_user_cannot_authorize(self):
        result = self.engine().respond("Don't write Hello from Aqua into my open Notepad window.")
        self.assertFalse(result.ok)
        self.assertEqual(self.worker_calls, [])


class TextWorkerTests(Guarded):
    def test_journal_finalization_preserves_pairing_and_rejects_wrong_tool(self):
        conversation = Conversation()
        call = event("set_control_text", {"control_id": "ci_1234567890"})[0].tool_calls[0]
        assistant = {"role": "assistant", "content": "", "tool_calls": [call]}
        initial = {"role": "tool", "tool_name": "set_control_text", "content": '{"status":"unknown"}'}
        conversation.commit_tool_turn("Fixture", assistant, [initial])
        with self.assertRaises(ValueError):
            conversation.replace_last_tool_result({**initial, "tool_name": "open_application"})
        self.assertEqual(conversation.history[-1], initial)
        conversation.replace_last_tool_result({**initial, "content": '{"status":"ok","verified":true}'})
        self.assertEqual([m["role"] for m in conversation.history], ["user", "assistant", "tool"])
        self.assertTrue(json.loads(conversation.history[-1]["content"])["verified"])

    def test_cli_opt_in_is_explicit_and_passed_to_all_three_interfaces(self):
        from contextlib import redirect_stdout, redirect_stderr
        from aqua.cli import main
        for command, target in (("chat", "run_text_chat"), ("voice-chat", "run_voice_chat"), ("handsfree", "run_handsfree")):
            with self.subTest(command=command), patch("aqua.cli." + target, return_value=0) as run, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main([command, "--allow-text-entry", "notepad"]), 0)
                self.assertEqual(run.call_args.kwargs["text_entry_apps"], ("notepad",))
        with patch("aqua.cli.run_text_chat") as run, self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            main(["chat", "--allow-text-entry", "chrome"])
        run.assert_not_called()

    def test_worker_dispatch_is_narrow_and_utf8_size_is_checked_before_decode(self):
        for raw, expected in ((json.dumps({"operation": "set_control_text"}).encode(), True),
                              (("雪" * 3000).encode(), False)):
            output = BytesIO()
            with patch.object(worker.sys, "stdin", SimpleNamespace(buffer=BytesIO(raw))), \
                    patch.object(worker.sys, "stdout", SimpleNamespace(buffer=output)), \
                    patch("aqua.desktop.interaction.dispatch_interaction", return_value={"status": "not_authorized"}) as dispatch:
                worker.main()
            self.assertEqual(dispatch.called, expected)
            self.assertIn(json.loads(output.getvalue())["status"], {"not_authorized", "error"})

    def test_parent_uses_actual_utf8_bytes_for_request_limit(self):
        process = Mock(returncode=0)
        process.communicate.return_value = (b'{"status":"error"}', None)
        with patch("aqua.desktop.session.subprocess.Popen", return_value=process) as spawn:
            run_worker({"fixture": "雪" * 2000})
            spawn.assert_called_once()
            self.assertLess(len(process.communicate.call_args.args[0]), 8192)
        with patch("aqua.desktop.session.subprocess.Popen") as spawn:
            run_worker({"fixture": "雪" * 3000})
            spawn.assert_not_called()

    def test_closed_tool_schemas_and_no_invoke_or_text_override(self):
        for args in ({"control_id": "ci_1234567890", "text": "UI supplied override"},
                     {"control_id": "100"}, {"window_id": "w_1234567890"}):
            with self.assertRaises(tools.ToolValidationError):
                tools.validate_registered_call(event("set_control_text", args)[0].tool_calls[0])
        for name in ("focus_window", "invoke_control", "select_control"):
            with self.assertRaises(tools.ToolValidationError):
                tools.validate_registered_call(event(name, {})[0].tool_calls[0])


if __name__ == "__main__":
    unittest.main()
