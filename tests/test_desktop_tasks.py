"""Fresh desktop evidence and combined consent. Every executor/worker is fake."""

from copy import deepcopy
import unittest
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aqua.actions import tools
from aqua.actions.launcher import ALLOWED_APPS
from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings
from aqua.desktop.authorization import observation_permission
from aqua.desktop.session import ObservationSession
from aqua.desktop import backend
from aqua.desktop.interaction_session import InteractionSession, authorize_launch_text_entry
import test_text_entry as entry
import test_handsfree as speech
import test_desktop_backend as desktop_fixture

INVENTORY = "What applications can you currently see open on my desktop?"
COMBINED = "Open Notepad and write Hello from Aqua into it."


class DesktopWorker:
    def __init__(self):
        self.calls, self.windows, self.baseline = [], [entry.raw_window()], []
        self.baseline_status, self.truncated, self.write_status, self.post_truncated = "ok", False, "ok", False

    def __call__(self, request):
        self.calls.append(deepcopy(request))
        if request.get("launch_baseline"):
            return {"status": self.baseline_status, "baseline_handles": self.baseline, "truncated": self.truncated}
        if request["operation"] == "list_windows":
            return {"status": "ok" if self.windows else "empty", "windows": deepcopy(self.windows), "truncated": self.post_truncated}
        if request["operation"] == "inspect_window":
            result = entry.raw_inspection()
            result["identity"] = deepcopy(request["identity"])
            result["controls"][0]["control_type"] = "Document"
            return result
        return {"status": self.write_status, "attempted": self.write_status != "not_empty", "verified": self.write_status == "ok"}


class CombinedModel:
    def __init__(self, *, app="notepad", text=entry.TEXT, first_launch=False):
        self.app, self.text, self.first_launch, self.calls = app, text, first_launch, []

    def __call__(self, messages, **options):
        self.calls.append((deepcopy(messages), deepcopy(options)))
        if len(self.calls) == 1:
            name, arguments = (("open_application", {"app_id": self.app}) if self.first_launch else
                               ("request_text_entry", {"app_id": self.app, "text": self.text}))
            return iter(entry.event(name, arguments))
        available = {d["function"]["name"]: d["function"] for d in options["tools"]}
        if "request_text_entry" in available:
            return iter(entry.event("request_text_entry", {"app_id": self.app, "text": self.text}))
        for name, key in (("open_application", "app_id"), ("list_windows", None),
                          ("inspect_window", "window_id"), ("set_control_text", "control_id")):
            if name in available:
                arguments = {} if key is None else {key: available[name]["parameters"]["properties"][key]["enum"][0]}
                return iter(entry.event(name, arguments))
        return iter(speech.native_reply("All windows are ready and writing succeeded!"))


class DesktopTaskTests(unittest.TestCase):
    def setUp(self):
        self.worker = DesktopWorker()
        for target in ("aqua.desktop.session.run_worker", "aqua.desktop.interaction_session.run_worker"):
            manager = patch(target, self.worker)
            manager.start()
            self.addCleanup(manager.stop)
        manager = patch.object(tools, "execute_application", return_value=tools.ToolResult("notepad", "launch_attempt_succeeded", True))
        self.launch = manager.start()
        self.addCleanup(manager.stop)

    def engine(self, model, *, enabled=("notepad",), **limits):
        conversation = Conversation()
        conversation.text_entry_apps = enabled
        return ConversationController(Mock(stream_chat=Mock(side_effect=model)), conversation,
            model=DEFAULT_CHAT_MODEL, settings=GenerationSettings(temperature=.2), **limits)

    def test_current_desktop_inventory_grants_only_approved_metadata_scope(self):
        permission = observation_permission(INVENTORY)
        self.assertEqual(permission.status, "approved")
        self.assertEqual(set(permission.apps), set(ALLOWED_APPS))
        self.assertFalse(permission.requires_inspection)

    def test_current_app_state_and_desktop_description_do_not_escape_freshness_gate(self):
        for request in ("Is VS Code open?", "Is my VS Code currently open?"):
            permission = observation_permission(request)
            self.assertEqual(permission.status, "approved")
            self.assertEqual(permission.apps, ("vs code",))
            self.assertFalse(permission.requires_inspection)
        self.assertEqual(observation_permission("Describe my current desktop.").status, "clarification")
        permission = observation_permission("What can you currently see in my VS Code window?")
        self.assertEqual(permission.status, "approved")
        self.assertTrue(permission.requires_inspection)

    def test_no_current_desktop_claim_without_fresh_successful_read(self):
        engine = self.engine(lambda *_a, **_k: iter(speech.native_reply("Only Notepad is currently open.")))
        engine.conversation.commit("What was open earlier?", "Notepad was open earlier.")
        result = engine.respond(INVENTORY)
        self.assertFalse(result.ok)
        self.assertFalse(result.task.operations)
        self.assertIn("unknown", result.text.lower())
        self.assertNotIn("Only Notepad is currently open", result.text)
        self.assertEqual(self.worker.calls, [])

    def test_inventory_lists_again_on_each_new_task(self):
        def model(messages, **options):
            if messages[-1]["role"] == "user": return iter(entry.event("list_windows", {}))
            return iter(speech.native_reply("Only Notepad is open."))
        engine = self.engine(model)
        first = engine.respond(INVENTORY)
        self.assertTrue(first.ok)
        self.assertEqual(len(first.task.operations), 1)
        self.worker.windows[0]["app_id"] = "calculator"
        second = engine.respond(INVENTORY)
        self.assertTrue(second.ok)
        self.assertIn("calculator: title", second.text)
        self.assertNotIn("notepad: title", second.text)
        self.assertEqual(len(self.worker.calls), 2)
        self.assertIn("approved", second.text.lower())
        self.launch.assert_not_called()

    def test_vscode_no_call_does_not_assume_window_existed(self):
        result = self.engine(lambda *_a, **_k: iter(speech.native_reply("I cannot inspect that."))).respond("What can you see in my VS Code window?")
        self.assertFalse(result.ok)
        self.assertFalse(result.task.operations)
        self.assertIn("unknown", result.text.lower())
        self.assertNotIn("only window metadata was observed", result.text)
        self.launch.assert_not_called()

    def test_inaccessible_approved_window_is_error_not_fresh_empty_evidence(self):
        native = desktop_fixture.Native()
        ui = Mock()
        ui.bind.side_effect = OSError("provider not available")
        raw = backend.dispatch({"operation": "list_windows", "scope": ["vs code"]}, native=native, ui=ui)
        self.assertEqual(raw["status"], "error")
        self.assertTrue(raw["truncated"])
        self.assertEqual(raw["windows"], [])

    def test_combined_model_chosen_steps_fit_five_operations_six_decisions(self):
        result = self.engine(CombinedModel()).respond(COMBINED)
        self.assertTrue(result.ok)
        self.assertEqual((len(result.task.operations), result.task.decisions), (5, 6))
        self.assertEqual([o.tool_name for o in result.task.operations], ["request_text_entry", "open_application", "list_windows", "inspect_window", "set_control_text"])
        self.launch.assert_called_once_with("notepad")
        self.assertTrue(self.worker.calls[0].get("launch_baseline"))
        self.assertEqual(self.worker.calls[-1]["text"], entry.TEXT)
        self.assertEqual(sum(r["operation"] == "set_control_text" for r in self.worker.calls), 1)
        self.assertIn("verified by UIA readback", result.text)

    def test_legacy_launch_only_parser_still_refuses_partial_combined_execution(self):
        permission = tools.authorize_task_request(COMBINED, "notepad")
        self.assertEqual(permission.status, "denied")
        self.assertIn("partial subset", permission.message)

    def test_launch_first_is_rejected_then_model_authorizes_before_any_effect(self):
        engine = self.engine(CombinedModel(first_launch=True))
        result = engine.respond(COMBINED)
        self.assertTrue(result.ok)
        self.assertEqual((result.task.decisions, len(result.task.operations)), (6, 5))
        self.launch.assert_called_once_with("notepad")
        self.assertEqual(sum(r["operation"] == "set_control_text" for r in self.worker.calls), 1)
        import json
        messages = [json.loads(m["content"]) for m in engine.conversation.history if m["role"] == "tool"]
        self.assertEqual(messages[0]["status"], "combined_authorization_required")
        self.assertEqual(messages[1]["status"], "authorized")

    def test_launch_first_review_cannot_authorize_wrong_literal_or_repeat_forever(self):
        for second in (entry.event("request_text_entry", {"app_id": "notepad", "text": "Altered"}),
                       speech.native_reply("I will launch it.")):
            replies = iter((entry.event("open_application", {"app_id": "notepad"}), second))
            result = self.engine(lambda *_a, **_k: iter(next(replies))).respond(COMBINED)
            self.assertFalse(result.ok)
            self.assertFalse(result.task.operations)
            self.launch.assert_not_called()
            self.assertEqual(self.worker.calls, [])

    def test_combined_unsupported_ambiguous_negated_and_extra_effects_fail_before_launch(self):
        for request in ("Open Notepad and Calculator and write Hello from Aqua into it.",
                        "Open Chrome and write Hello from Aqua into it.",
                        "Don't " + COMBINED, 'Say "' + COMBINED + '"', "If I asked: " + COMBINED,
                        COMBINED + " Delete my files.", "Open Notepad and write something into it."):
            with self.subTest(request=request):
                result = self.engine(CombinedModel()).respond(request)
                self.assertFalse(result.task.operations)
        self.launch.assert_not_called()
        self.assertEqual(self.worker.calls, [])

    def test_baseline_failure_or_truncation_prevents_launch(self):
        for status, truncated in (("timeout", False), ("ok", True)):
            with self.subTest(status=status):
                self.worker.baseline_status, self.worker.truncated = status, truncated
                result = self.engine(CombinedModel()).respond(COMBINED)
                self.assertFalse(result.ok)
                self.launch.assert_not_called()
                self.assertFalse(any(r["operation"] == "set_control_text" for r in self.worker.calls))

    def test_preexisting_handle_is_not_a_new_target(self):
        self.worker.baseline = [100]
        result = self.engine(CombinedModel()).respond(COMBINED)
        self.assertFalse(result.ok)
        self.launch.assert_called_once_with("notepad")
        self.assertFalse(any(r["operation"] in {"inspect_window", "set_control_text"} for r in self.worker.calls))
        self.assertIn("new", result.text.lower())

    def test_truncated_post_launch_listing_cannot_establish_unique_target(self):
        self.worker.post_truncated = True
        result = self.engine(CombinedModel()).respond(COMBINED)
        self.assertFalse(result.ok)
        self.launch.assert_called_once_with("notepad")
        self.assertFalse(any(r["operation"] in {"inspect_window", "set_control_text"} for r in self.worker.calls))
        self.assertIn("incomplete", result.text)

    def test_launch_failure_or_window_not_ready_never_writes(self):
        for mode in ("failed", "not_ready"):
            with self.subTest(mode=mode):
                self.worker.calls.clear()
                self.launch.reset_mock()
                self.launch.return_value = tools.ToolResult("notepad", "launch_failed", False) if mode == "failed" else tools.ToolResult("notepad", "launch_attempt_succeeded", True)
                self.worker.windows = []
                result = self.engine(CombinedModel()).respond(COMBINED)
                self.assertFalse(result.ok)
                self.assertFalse(any(r["operation"] in {"inspect_window", "set_control_text"} for r in self.worker.calls))

    def test_nonempty_target_remains_protected(self):
        self.worker.write_status = "not_empty"
        result = self.engine(CombinedModel()).respond(COMBINED)
        self.assertFalse(result.ok)
        self.assertEqual(result.task.status, "failed")
        self.assertNotIn("was verified by UIA readback", result.text)

    def test_combined_opt_in_or_budget_missing_prevents_partial_launch(self):
        for options in ({"enabled": ()}, {"max_tool_operations": 4}, {"max_model_decisions": 4}):
            with self.subTest(options=options):
                result = self.engine(CombinedModel(), **options).respond(COMBINED)
                self.assertFalse(result.ok)
                self.launch.assert_not_called()

    def test_inventory_unknown_targets_and_non_observation_remain_protected(self):
        for request in ("What applications can you currently see open on my desktop and delete my files?",
                        "What applications can you currently see open on my desktop in Outlook?",
                        'Say "' + INVENTORY + '"', "If I asked " + INVENTORY):
            with self.subTest(request=request):
                self.assertNotEqual(observation_permission(request).status, "approved")
        self.assertEqual(observation_permission("What is currently running on my desktop?").status, "clarification")
        self.assertEqual(observation_permission("What is a desktop?").status, "none")

    def test_private_baseline_never_exposes_unrelated_windows_to_model(self):
        self.worker.baseline = [100, 900]
        observer = ObservationSession(self.worker)
        observer.begin_task(("notepad",))
        observer.enable_editable_controls()
        self.assertTrue(observer.capture_launch_baseline())
        old = deepcopy(self.worker.windows[0])
        new = deepcopy(old)
        new["identity"] = {"handle": 200, "pid": 20, "created": 456, "runtime_id": [42, 200]}
        self.worker.windows = [old, new]
        listed = observer.list_windows()
        self.assertEqual(len(listed["data"]["windows"]), 1)
        self.assertEqual(len(observer.window_ids), 1)
        self.assertNotIn("baseline_handles", str(listed))
        observer.begin_task(("notepad",))
        self.assertIsNone(observer._launch_baseline)

    def test_old_opaque_reference_cannot_inspect_or_register_after_launch(self):
        observer = ObservationSession(self.worker)
        observer.begin_task(("notepad",))
        old_id = observer.list_windows()["data"]["windows"][0]["window_id"]
        observer.begin_task(("notepad",))
        observer.enable_editable_controls()
        self.worker.baseline = [100]
        self.assertTrue(observer.capture_launch_baseline())
        previous = len(self.worker.calls)
        self.assertEqual(observer.inspect_window(old_id)["status"], "not_authorized")
        self.assertNotIn(old_id, observer.window_ids)
        interaction = InteractionSession(observer, Mock())
        interaction.begin_task(("notepad",), entry.TEXT)
        self.assertEqual(interaction.register_controls(old_id), {})
        self.assertEqual(len(self.worker.calls), previous)
        observer.clear()
        self.assertIsNone(observer._launch_baseline)

    def test_native_baseline_includes_hidden_handles_without_accessible_names(self):
        native = object.__new__(backend.NativeWindows)
        native.truncated = False
        native.callback_type = lambda callback: callback
        def enumerate_windows(callback, _parameter):
            for handle in (100, 200): callback(handle, 0)
            return True
        native.user = SimpleNamespace(EnumWindows=enumerate_windows)
        native.is_observable = lambda handle: handle == 200
        request = {"operation": "list_windows", "scope": ["notepad"], "launch_baseline": True}
        with patch.object(backend, "UIAutomation", side_effect=AssertionError("No UIA labels/patterns in baseline")):
            result = backend.dispatch(request, native=native)
        self.assertEqual(result, {"status": "ok", "baseline_handles": [100, 200], "truncated": False})
        self.assertEqual(backend.dispatch({**request, "scope": ["chrome"]}, native=native)["status"], "not_authorized")

    def test_combined_literal_scope_and_quoted_command_content(self):
        self.assertFalse(authorize_launch_text_entry(COMBINED, "notepad", "Altered", ("notepad",)))
        self.assertFalse(authorize_launch_text_entry(COMBINED, "calculator", entry.TEXT, ("calculator",)))
        self.assertFalse(authorize_launch_text_entry("Open Notepad and write something into it.", "notepad", "something", ("notepad",)))
        content = "Hello and open Chrome"
        self.assertFalse(authorize_launch_text_entry("Open Notepad and write " + content + " into it.", "notepad", content, ("notepad",)))
        request = 'Open Notepad and type "' + content + '" into it.'
        self.assertTrue(authorize_launch_text_entry(request, "notepad", content, ("notepad",)))
        result = self.engine(CombinedModel(text=content)).respond(request)
        self.assertTrue(result.ok)
        self.launch.assert_called_once_with("notepad")
        self.assertEqual(self.worker.calls[-1]["text"], content)

    def test_model_cannot_skip_launch_or_substitute_app(self):
        for second in (entry.event("list_windows", {}), entry.event("set_control_text", {"control_id": "ci_forged0000"}),
                       entry.event("open_application", {"app_id": "calculator"})):
            replies = iter((entry.event("request_text_entry", {"app_id": "notepad", "text": entry.TEXT}), second))
            result = self.engine(lambda *_a, **_k: iter(next(replies))).respond(COMBINED)
            self.assertFalse(result.ok)
            self.launch.assert_not_called()
            self.assertEqual(self.worker.calls, [])

    def test_duplicate_launch_is_not_executed_twice(self):
        replies = iter((entry.event("request_text_entry", {"app_id": "notepad", "text": entry.TEXT}),
                        entry.event("open_application", {"app_id": "notepad"}), entry.event("open_application", {"app_id": "notepad"})))
        result = self.engine(lambda *_a, **_k: iter(next(replies))).respond(COMBINED)
        self.assertFalse(result.ok)
        self.launch.assert_called_once_with("notepad")
        self.assertFalse(any(r["operation"] == "set_control_text" for r in self.worker.calls))

    def test_cancelled_or_unknown_launch_preserves_truth_and_clears_baseline(self):
        for error in (KeyboardInterrupt(), OSError("unknown launch outcome")):
            self.worker.calls.clear()
            self.launch.side_effect = error
            engine = self.engine(CombinedModel())
            result = engine.respond(COMBINED)
            self.assertFalse(result.ok)
            self.assertTrue(result.task.unknown_operations)
            self.assertFalse(any(r["operation"] in {"inspect_window", "set_control_text"} for r in self.worker.calls))
            self.assertIsNone(engine.conversation.desktop_observer._launch_baseline)
            self.assertEqual(engine.conversation.desktop_interaction.control_tokens, ())

    def test_verified_write_on_sixth_decision_needs_no_seventh_model_call(self):
        model, paused = CombinedModel(), False
        def respond(messages, **options):
            nonlocal paused
            if len(model.calls) == 1 and not paused:
                paused = True
                return iter(speech.native_reply("I will continue."))
            return model(messages, **options)
        engine = self.engine(respond)
        result = engine.respond(COMBINED)
        self.assertTrue(result.ok)
        self.assertEqual((result.task.decisions, len(result.task.operations)), (6, 5))
        self.assertEqual(engine.client.stream_chat.call_count, 6)
        self.assertEqual(sum(r["operation"] == "set_control_text" for r in self.worker.calls), 1)


if __name__ == "__main__":
    unittest.main()
