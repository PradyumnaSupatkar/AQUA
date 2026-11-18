"""Bounded, model-directed tasks with real policy and simulated launch results."""

from collections import deque
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import unittest
from unittest.mock import patch

from aqua.actions import tools
from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import ChatEvent, DEFAULT_CHAT_MODEL, GenerationSettings, OllamaError
from aqua.brain.task import MAX_MODEL_DECISIONS, MAX_TOOL_OPERATIONS


def call(app, identifier=None):
    return {"id": identifier or "step-" + app.replace(" ", "-"),
            "function": {"name": "open_application", "arguments": {"app_id": app}}}


def choose(app, identifier=None):
    return [ChatEvent(done=True, tool_calls=(call(app, identifier),))]


def reply(text="The task is finished."):
    return [ChatEvent(text), ChatEvent(done=True)]


class ScriptedClient:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.requests = []

    def stream_chat(self, messages, **options):
        self.requests.append((deepcopy(messages), deepcopy(options)))
        if not self.responses:
            raise AssertionError("Unexpected extra model decision")
        events = self.responses.popleft()
        def stream():
            for event in events:
                if isinstance(event, BaseException):
                    raise event
                yield event
        return stream()


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Agent tests may not use network or applications"))
            manager.start()
            self.addCleanup(manager.stop)
        manager = patch.object(tools, "execute_application", side_effect=lambda app: tools.ToolResult(app, "launch_attempt_succeeded", True))
        self.launch = manager.start()
        self.addCleanup(manager.stop)

    def engine(self, *responses, **limits):
        self.client = ScriptedClient(*responses)
        self.conversation = Conversation()
        return ConversationController(self.client, self.conversation, model=DEFAULT_CHAT_MODEL,
                                      settings=GenerationSettings(temperature=.2), **limits)

    def results(self):
        return [json.loads(row["content"]) for row in self.conversation.history if row["role"] == "tool"]

    def test_two_steps_observe_each_real_result_in_one_user_turn(self):
        engine = self.engine(choose("notepad"), choose("calculator"), reply("I completed everything."))
        result = engine.respond("Open Notepad and Calculator.")
        self.assertTrue(result.ok)
        self.assertEqual([c.args[0] for c in self.launch.call_args_list], ["notepad", "calculator"])
        self.assertEqual(result.task.status, "completed")
        self.assertEqual((result.task.decisions, len(result.task.operations)), (3, 2))
        self.assertEqual(self.conversation.turn_count, 1)
        self.assertEqual([r["role"] for r in self.conversation.history],
                         ["user", "assistant", "tool", "assistant", "tool", "assistant"])
        self.assertEqual(json.loads(self.client.requests[1][0][-1]["content"])["app_id"], "notepad")
        progress = json.loads(self.client.requests[1][0][-1]["content"])["task_progress"]
        self.assertEqual(progress["remaining_authorized_apps"], ["calculator"])
        self.assertEqual(progress["attempted_apps"], ["notepad"])
        self.assertTrue(progress["remaining_apps_already_authorized"])
        enum = self.client.requests[1][1]["tools"][0]["function"]["parameters"]["properties"]["app_id"]["enum"]
        self.assertEqual(enum, ["calculator"])
        self.assertEqual(self.client.requests[2][1]["tools"], [])
        self.assertEqual(len(result.task.completed_operations), 2)

    def test_model_chooses_order_rather_than_a_fixed_workflow(self):
        engine = self.engine(choose("calculator"), choose("notepad"), reply())
        result = engine.respond("Open Notepad and Calculator.")
        self.assertEqual([op.app_id for op in result.task.operations], ["calculator", "notepad"])
        self.assertEqual([c.args[0] for c in self.launch.call_args_list], ["calculator", "notepad"])

    def test_failure_is_committed_before_model_chooses_another_authorized_app(self):
        self.launch.side_effect = lambda app: tools.ToolResult(app, "launch_failed", False) if app == "notepad" else tools.ToolResult(app, "launch_attempt_succeeded", True)
        engine = self.engine(choose("notepad"), choose("calculator"), reply("Everything opened successfully."))
        result = engine.respond("Open Notepad then Calculator.")
        self.assertEqual(json.loads(self.client.requests[1][0][-1]["content"])["status"], "launch_failed")
        self.assertEqual(result.task.status, "partial_failure")
        self.assertEqual(len(result.task.failed_operations), 1)
        self.assertEqual(len(result.task.completed_operations), 1)
        self.assertEqual([r["status"] for r in self.results()], ["launch_failed", "launch_attempt_succeeded"])
        self.assertNotIn("Everything opened successfully", result.text)

    def test_duplicate_success_or_failed_attempt_with_new_id_cannot_retry(self):
        for success in (True, False):
            with self.subTest(success=success):
                self.launch.reset_mock()
                self.launch.side_effect = lambda app: tools.ToolResult(app, "launch_attempt_succeeded" if success else "launch_failed", success)
                engine = self.engine(choose("notepad", "first"), choose("notepad", "different-id"))
                result = engine.respond("Open Notepad and Calculator.")
                self.launch.assert_called_once_with("notepad")
                self.assertEqual(result.task.decisions, 2)
                self.assertEqual(len(result.task.operations), 1)
                self.assertIn("Duplicate", result.task.stop_reason)
                self.assertEqual(result.task.remaining_apps, ("calculator",))

    def test_replayed_call_identifier_cannot_execute_a_different_app(self):
        engine = self.engine(choose("notepad", "same-id"), choose("calculator", "same-id"))
        result = engine.respond("Open Notepad and Calculator.")
        self.launch.assert_called_once_with("notepad")
        self.assertEqual(result.task.status, "stopped")

    def test_later_step_cannot_expand_original_consent(self):
        engine = self.engine(choose("notepad"), choose("chrome"))
        result = engine.respond("Open Notepad and Calculator.")
        self.launch.assert_called_once_with("notepad")
        self.assertFalse(result.ok)
        self.assertFalse(any(op.app_id == "chrome" for op in result.task.operations))

    def test_unsafe_or_unsupported_part_rejects_before_first_operation(self):
        cases = ["Open Notepad and delete my files.", "Open Notepad and PowerShell.",
                 "Open Notepad and Firefox.", "Open Notepad then inspect my project.",
                 "Don't open Notepad and Calculator.", 'My friend said "open Notepad and Calculator".',
                 "If I asked you to open Notepad and Calculator, what happens?",
                 "Open Notepad and Calculator only if my download completes."]
        for text in cases:
            with self.subTest(text=text):
                self.launch.reset_mock()
                result = self.engine(choose("notepad")).respond(text)
                self.launch.assert_not_called()
                self.assertEqual(result.task.operations, ())

    def test_unknown_stage_two_tool_is_not_available(self):
        raw = call("notepad")
        raw["function"] = {"name": "run_tests", "arguments": {"command": "pytest"}}
        engine = self.engine([ChatEvent(done=True, tool_calls=(raw,))])
        result = engine.respond("Open Notepad.")
        self.launch.assert_not_called()
        self.assertIn("couldn't validate", result.text)
        self.assertEqual([d["function"]["name"] for d in self.client.requests[0][1]["tools"]], ["open_application"])

    def test_tool_operation_limit_with_lower_host_limit_prevents_extra_effect(self):
        engine = self.engine(choose("notepad"), choose("calculator"), reply("All four apps are open."),
                             max_tool_operations=2)
        result = engine.respond("Open Notepad, Calculator, VS Code and Chrome.")
        self.assertEqual(len(result.task.operations), 2)
        self.assertEqual(result.task.decisions, 3)
        self.assertEqual(result.task.status, "stopped")
        self.assertIn("Tool operation limit", result.task.stop_reason)
        self.assertEqual(self.client.requests[-1][1]["tools"], [])
        self.assertNotIn("All four apps are open", result.text)

    def test_model_decision_limit_stops_without_an_extra_request(self):
        engine = self.engine(choose("notepad"), choose("calculator"), reply(), max_model_decisions=2)
        result = engine.respond("Open Notepad, Calculator and Chrome.")
        self.assertEqual((result.task.decisions, len(self.client.requests)), (2, 2))
        self.assertEqual(len(result.task.operations), 2)
        self.assertIn("Model decision limit", result.task.stop_reason)
        self.assertEqual(len(self.client.responses), 1)

    def test_limits_cannot_exceed_five_operations_or_six_decisions(self):
        self.assertEqual((MAX_TOOL_OPERATIONS, MAX_MODEL_DECISIONS), (5, 6))
        for limits in ({"max_tool_operations": 6}, {"max_model_decisions": 7},
                       {"max_tool_operations": 0}, {"max_model_decisions": True}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                self.engine(**limits)

    def test_all_four_current_apps_fit_inside_default_bounds(self):
        order = ["chrome", "notepad", "vs code", "calculator"]
        engine = self.engine(*(choose(app) for app in order), reply())
        result = engine.respond("Open all approved apps.")
        self.assertEqual([op.app_id for op in result.task.operations], order)
        self.assertEqual((result.task.decisions, len(result.task.operations)), (5, 4))
        self.assertEqual(result.task.status, "completed")

    def test_fake_completion_without_tools_cannot_be_reported_as_done(self):
        engine = self.engine(reply("I opened both applications. All done!"), reply("All done!"))
        result = engine.respond("Open Notepad and Calculator.")
        self.launch.assert_not_called()
        self.assertEqual(result.task.status, "incomplete")
        self.assertIn("No tool operations", result.text)
        self.assertNotIn("All done", result.text)
        self.assertNotIn("I opened both", str(self.conversation.history))
        self.assertEqual(result.task.decisions, 2)

    def test_model_stopping_early_reports_remaining_unattempted_app(self):
        engine = self.engine(choose("notepad"), reply("Both are open."), reply("Both are open."))
        result = engine.respond("Open Notepad and Calculator.")
        self.assertFalse(result.ok)
        self.assertEqual(result.task.status, "incomplete")
        self.assertIn("Not attempted: calculator", result.text)
        self.assertNotIn("Both are open", result.text)
        self.assertEqual(result.task.decisions, 3)

    def test_single_reconsideration_sees_real_results_and_requires_model_call(self):
        stopped = "Notepad is open. Would you like me to now open Calculator?"
        engine = self.engine(choose("notepad"), reply(stopped), choose("calculator"), reply())
        result = engine.respond("Open Notepad and Calculator.")
        self.assertTrue(result.ok)
        self.assertEqual((result.task.decisions, len(result.task.operations)), (4, 2))
        self.assertEqual([c.args[0] for c in self.launch.call_args_list], ["notepad", "calculator"])
        review, options = self.client.requests[2]
        self.assertEqual([m["role"] for m in review], ["system", "user", "assistant", "tool"])
        self.assertEqual(review[1]["content"], "Open Notepad and Calculator.")
        self.assertIn(json.dumps(stopped), review[0]["content"])
        self.assertIn("Single bounded completeness review", review[0]["content"])
        self.assertIn('"remaining_apps":["calculator"]', review[0]["content"])
        self.assertEqual(json.loads(review[-1]["content"])["status"], "launch_attempt_succeeded")
        self.assertEqual(options["tools"][0]["function"]["parameters"]["properties"]["app_id"]["enum"], ["calculator"])
        self.assertEqual(self.conversation.turn_count, 1)
        self.assertEqual([m["role"] for m in self.conversation.history],
                         ["user", "assistant", "tool", "assistant", "tool", "assistant"])
        self.assertNotIn(stopped, str(self.conversation.history))
        self.assertNotIn("Single bounded completeness review", self.client.requests[3][0][0]["content"])

    def test_review_is_once_per_task_even_after_further_progress(self):
        engine = self.engine(choose("notepad"), reply(), choose("calculator"), reply())
        result = engine.respond("Open Notepad, Calculator and Chrome.")
        self.assertEqual(result.task.decisions, 4)
        self.assertEqual(result.task.status, "incomplete")
        self.assertEqual(result.task.remaining_apps, ("chrome",))
        self.assertEqual(len(self.launch.call_args_list), 2)

    def test_reconsideration_does_not_execute_without_a_new_tool_call(self):
        engine = self.engine(choose("notepad"), reply("Done."), reply("Calculator is also open."))
        result = engine.respond("Open Notepad and Calculator.")
        self.launch.assert_called_once_with("notepad")
        self.assertEqual(result.task.status, "incomplete")
        self.assertNotIn("Calculator is also open", result.text)

    def test_refusal_or_cancellation_reply_is_not_reconsidered(self):
        for text in ("I cannot continue.", "I can't help with that.", "I won't open Calculator.",
                     "This is unsafe.", "I refuse this action.", "Cancelled.", "Stop here.",
                     "Do not continue.", "I'm unable to proceed.", "I can\u2019t continue.",
                     "I won\u2019t open Calculator.", "I will not continue.", "I must not proceed.",
                     "I couldn't continue.", "I could not continue.", "It is not safe to proceed.",
                     "It's unsafe.", "It\u2019s unsafe.", "This isn't safe.", "I cannot safely continue."):
            with self.subTest(text=text):
                self.launch.reset_mock()
                result = self.engine(choose("notepad"), reply(text)).respond("Open Notepad and Calculator.")
                self.assertEqual(result.task.decisions, 2)
                self.launch.assert_called_once_with("notepad")
                self.assertEqual(result.task.status, "incomplete")
                self.assertIn("explicitly declined", result.task.stop_reason)

    def test_readiness_caveats_and_duplicate_safeguards_do_not_decline_remaining_work(self):
        for caveat in (
            "I cannot verify VS Code is ready.",
            "I can't confirm the window is responding.",
            "I won't retry VS Code.",
            "We should not claim readiness from process creation alone.",
            'The safety rule says "do not retry an attempted operation".',
            "The process is created; don't assume its window is ready.",
        ):
            with self.subTest(caveat=caveat):
                self.launch.reset_mock()
                result = self.engine(choose("vs code"), reply(caveat), choose("calculator"), reply()).respond(
                    "Open VS Code and Calculator.")
                self.assertEqual(result.task.status, "completed")
                self.assertEqual(result.task.decisions, 4)
                self.assertEqual([c.args[0] for c in self.launch.call_args_list], ["vs code", "calculator"])
                self.assertIn("Single bounded completeness review", self.client.requests[2][0][0]["content"])

    def test_review_context_has_explicit_unfinished_state_and_real_budget(self):
        result = self.engine(choose("vs code"), reply("Would you like the other app?"),
                             choose("calculator"), reply()).respond("Open VS Code and Calculator.")
        context = self.client.requests[2][0][0]["content"]
        state = json.loads(context.split("Authoritative continuation state: ", 1)[1].split("\n", 1)[0])
        self.assertEqual(state["task_status"], "in_progress")
        self.assertEqual(state["remaining_authorized_apps"], ["calculator"])
        self.assertEqual(state["attempted"], [{"app_id": "vs code", "status": "launch_attempt_succeeded", "process_created": True}])
        self.assertFalse(state["confirmation_required"])
        self.assertEqual((state["tool_operations_left"], state["model_decisions_left_including_review"]), (4, 4))
        self.assertEqual(result.task.status, "completed")

    def test_plain_stop_after_review_reports_exhausted_review_without_execution(self):
        result = self.engine(choose("calculator"), reply("Done."), reply("Next I will propose VS Code.")).respond(
            "Open VS Code and Calculator.")
        self.launch.assert_called_once_with("calculator")
        self.assertEqual(result.stage, "task_reconsideration")
        self.assertEqual(result.task.stop_reason, "The single reconsideration returned no native tool call.")
        self.assertEqual(result.task.remaining_apps, ("vs code",))

    def test_refusal_during_review_stays_a_refusal(self):
        result = self.engine(choose("calculator"), reply("Done."), reply("I cannot continue.")).respond(
            "Open VS Code and Calculator.")
        self.launch.assert_called_once_with("calculator")
        self.assertEqual(result.task.decisions, 3)
        self.assertEqual(result.task.stop_reason, "The model declined further work during reconsideration.")

    def test_model_budget_exhaustion_is_distinct_from_a_skipped_review(self):
        result = self.engine(choose("vs code"), reply("Done."), max_model_decisions=2).respond(
            "Open VS Code and Calculator.")
        self.launch.assert_called_once_with("vs code")
        self.assertEqual(result.task.decisions, 2)
        self.assertIn("no reconsideration budget remains", result.task.stop_reason)

    def test_no_reconsideration_after_limits_are_reached(self):
        for limits in ({"max_model_decisions": 2}, {"max_tool_operations": 1}):
            with self.subTest(limits=limits):
                self.launch.reset_mock()
                result = self.engine(choose("notepad"), reply(), **limits).respond("Open Notepad and Calculator.")
                self.assertEqual(result.task.decisions, 2)
                self.launch.assert_called_once_with("notepad")

    def test_review_counts_toward_model_decision_cap(self):
        result = self.engine(choose("notepad"), reply(), choose("calculator"), max_model_decisions=3).respond(
            "Open Notepad and Calculator.")
        self.assertEqual(result.task.decisions, 3)
        self.assertEqual(len(self.client.requests), 3)
        self.assertEqual(len(result.task.operations), 2)
        self.assertEqual(result.task.status, "stopped")
        self.assertIn("Model decision limit", result.task.stop_reason)

    def test_review_preserves_duplicate_and_original_scope_checks(self):
        for app in ("notepad", "chrome"):
            with self.subTest(app=app):
                self.launch.reset_mock()
                result = self.engine(choose("notepad"), reply(), choose(app, "new-id")).respond(
                    "Open Notepad and Calculator.")
                self.launch.assert_called_once_with("notepad")
                self.assertFalse(result.ok)
                self.assertEqual(result.task.remaining_apps, ("calculator",))

    def test_review_after_failure_receives_failure_without_retrying(self):
        self.launch.side_effect = lambda app: tools.ToolResult(app, "launch_failed", False) if app == "notepad" else tools.ToolResult(app, "launch_attempt_succeeded", True)
        result = self.engine(choose("notepad"), reply("Finished."), choose("calculator"), reply()).respond(
            "Open Notepad and Calculator.")
        self.assertEqual(result.task.status, "partial_failure")
        self.assertEqual(json.loads(self.client.requests[2][0][-1]["content"])["status"], "launch_failed")
        self.assertEqual([op.app_id for op in result.task.operations], ["notepad", "calculator"])

    def test_cancel_during_review_preserves_effects_and_clears_pending_state(self):
        result = self.engine(choose("notepad"), reply(), [KeyboardInterrupt()]).respond(
            "Open Notepad and Calculator.")
        self.launch.assert_called_once_with("notepad")
        self.assertEqual(result.task.status, "cancelled")
        self.assertIsNone(self.conversation.action_state.pending_app)
        self.assertEqual(self.results()[0]["app_id"], "notepad")

    def test_negative_or_unsupported_requests_never_get_a_review(self):
        for text in ("Don't open Notepad and Calculator.", "Open Notepad and delete my files.",
                     "Open Notepad and PowerShell.", "If I said open Notepad and Calculator, what happens?"):
            with self.subTest(text=text):
                self.launch.reset_mock()
                result = self.engine(reply("No action taken.")).respond(text)
                self.launch.assert_not_called()
                self.assertEqual(result.task.decisions, 1)

    def test_next_step_reserves_complete_history_before_execution(self):
        engine = self.engine(choose("notepad"), choose("calculator"))
        with patch.object(self.conversation, "can_append_tool_step", return_value=False):
            result = engine.respond("Open Notepad and Calculator.")
        self.launch.assert_called_once_with("notepad")
        self.assertIn("history budget", result.task.stop_reason)
        self.assertEqual([r["app_id"] for r in self.results()], ["notepad"])

    def test_interrupted_second_launch_is_unknown_and_never_retried(self):
        self.launch.side_effect = [tools.ToolResult("notepad", "launch_attempt_succeeded", True), KeyboardInterrupt()]
        result = self.engine(choose("notepad"), choose("calculator")).respond("Open Notepad and Calculator.")
        self.assertEqual(result.task.status, "cancelled")
        self.assertEqual(len(result.task.operations), 2)
        self.assertEqual(len(result.task.unknown_operations), 1)
        self.assertEqual([r["status"] for r in self.results()], ["launch_attempt_succeeded", "launch_outcome_unknown"])
        self.assertEqual(self.launch.call_count, 2)

    def test_generation_error_after_first_step_preserves_the_recorded_effect(self):
        result = self.engine(choose("notepad"), [OllamaError("local failure")]).respond("Open Notepad and Calculator.")
        self.assertFalse(result.ok)
        self.assertEqual(len(result.task.completed_operations), 1)
        self.assertEqual(self.results()[0]["app_id"], "notepad")
        self.assertEqual(self.conversation.history[-1]["role"], "assistant")

    def test_unexpected_launcher_error_records_unknown_instead_of_retrying(self):
        self.launch.side_effect = OSError("PRIVATE PATH")
        result = self.engine(choose("notepad")).respond("Open Notepad and Calculator.")
        self.assertEqual(result.task.status, "unknown")
        self.assertEqual(len(result.task.unknown_operations), 1)
        self.assertIsNone(self.results()[0]["process_created"])
        self.assertNotIn("PRIVATE PATH", result.text)
        self.launch.assert_called_once()

    def test_confirmed_single_app_never_authorizes_a_following_other_app(self):
        engine = self.engine(choose("vs code"), choose("chrome"))
        first = engine.respond("I feel like programming.")
        self.assertEqual(first.outcome, "clarification")
        second = engine.respond("Yes, please.")
        self.assertEqual(second.task.authorized_apps, ("vs code",))
        self.launch.assert_called_once_with("vs code")
        self.assertIsNone(self.conversation.action_state.pending_app)
        self.assertFalse(second.ok)

    def test_repeated_explicit_request_is_a_new_task_not_a_duplicate(self):
        engine = self.engine(choose("notepad"), reply(), choose("notepad"), reply())
        first = engine.respond("Open Notepad.")
        second = engine.respond("Open Notepad.")
        self.assertEqual(self.launch.call_count, 2)
        self.assertEqual(len(first.task.operations), 1)
        self.assertEqual(len(second.task.operations), 1)
        self.assertIsNot(first.task, second.task)

    def test_clear_resets_history_pending_confirmation_and_task_progress(self):
        engine = self.engine(choose("notepad"), reply())
        result = engine.respond("Open Notepad.")
        with self.assertRaises(FrozenInstanceError):
            result.task.status = "fabricated"
        self.conversation.action_state.pending_app = "chrome"
        self.conversation.clear()
        self.assertEqual(self.conversation.history, [])
        self.assertIsNone(self.conversation.last_task)
        self.assertIsNone(self.conversation.action_state.pending_app)

    def test_normal_conversation_still_streams_and_uses_one_decision(self):
        chunks = []
        result = self.engine(reply("Python is a language.")).respond("Explain Python.", on_text=chunks.append)
        self.assertEqual(chunks, ["Python is a language."])
        self.assertEqual(result.task.status, "answered")
        self.assertEqual((result.task.decisions, result.requests), (1, 1))
        self.launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
