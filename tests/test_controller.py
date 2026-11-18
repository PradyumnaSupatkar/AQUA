"""Unified conversation/tool turns are deterministic and cannot launch apps."""

from copy import deepcopy
import json
from unittest.mock import Mock, patch
import unittest

from aqua.actions import tools
from aqua.actions.launcher import ALLOWED_APPS, ActionResult
from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, ChatEvent, GenerationSettings, OllamaError


def call(app="notepad", *, name="open_application", identifier="test-call"):
    return {"id": identifier, "function": {"index": 0, "name": name, "arguments": {"app_id": app}}}


class Stream:
    def __init__(self, *events):
        self.events = iter(events)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        value = next(self.events)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self):
        self.closed = True


def answer(text="Done."):
    return Stream(ChatEvent(text), ChatEvent(done=True))


def proposal(*calls):
    return Stream(ChatEvent(done=True, tool_calls=tuple(calls)))


class Client:
    def __init__(self, *streams):
        self.streams = iter(streams)
        self.requests = []

    def stream_chat(self, messages, **options):
        self.requests.append((deepcopy(messages), deepcopy(options)))
        return next(self.streams)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Controller unit tests may not perform external I/O."))
            manager.start()
            self.addCleanup(manager.stop)
        self.launch_patch = patch("aqua.actions.tools.open_application", return_value=ActionResult(True, "fixed launcher success"))
        self.launch = self.launch_patch.start()
        self.addCleanup(self.launch_patch.stop)

    def engine(self, *streams, **conversation_options):
        self.client = Client(*streams)
        self.conversation = Conversation(**conversation_options)
        return ConversationController(self.client, self.conversation, model=DEFAULT_CHAT_MODEL, settings=GenerationSettings())

    def test_ordinary_conversation_uses_one_interaction_and_retains_context(self):
        engine = self.engine(answer("Formula One is motorsport."), answer("Your drivers are Sainz and Verstappen."))
        with patch("aqua.actions.semantic.interpret_request", side_effect=AssertionError("Retired classifier must never run")):
            self.assertEqual(engine.respond("What is Formula One?").requests, 1)
            self.assertEqual(engine.respond("My favorite drivers are Carlos Sainz and Max Verstappen.").requests, 1)
        messages = self.client.requests[-1][0]
        self.assertEqual([row["role"] for row in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[-2]["content"], "Formula One is motorsport.")
        self.launch.assert_not_called()

    def test_all_required_explicit_requests_execute_one_canonical_app(self):
        cases = (
            ("Open Notepad.", "notepad"), ("Hey Aqua, open VS Code.", "vs code"),
            ("Well, open VS Code.", "vs code"), ("Well. Open VS Code.", "vs code"),
            ("Can you open VS Code for me?", "vs code"),
            ("I feel like doing some programming today. Can you open VS Code for me?", "vs code"),
            ("Could you bring up my browser?", "chrome"),
            ("I'd like to work on Aqua. Please launch VS Code.", "vs code"),
            ("Actually, could you open Chrome?", "chrome"),
        )
        for text, app in cases:
            with self.subTest(text=text):
                self.launch.reset_mock()
                engine = self.engine(proposal(call(app)), answer("The launch attempt succeeded; readiness is unverified."))
                result = engine.respond(text)
                self.assertTrue(result.ok)
                self.assertEqual(result.requests, 2)
                self.assertTrue(result.tool_result.success)
                self.launch.assert_called_once_with(app)
                messages, options = self.client.requests[-1]
                self.assertEqual(options["tools"], [])
                self.assertEqual(messages[-1]["role"], "tool")
                status = json.loads(messages[-1]["content"])
                self.assertTrue(status["process_created"])
                self.assertFalse(status["readiness_checked"])
                self.assertIn("readiness was not checked", result.text)

    def test_ambiguous_proposal_and_confirmation_are_one_use(self):
        engine = self.engine(proposal(call("vs code")), answer("The VS Code launch attempt succeeded."))
        result = engine.respond("I feel like programming.")
        self.assertEqual((result.outcome, result.requests), ("clarification", 1))
        self.assertEqual(self.conversation.action_state.pending_app, "vs code")
        self.launch.assert_not_called()
        self.assertEqual(engine.respond("Yes, please.").requests, 1)
        self.launch.assert_called_once_with("vs code")
        self.assertIsNone(self.conversation.action_state.pending_app)
        self.assertEqual(engine.respond("Yes, go ahead.").requests, 0)
        self.launch.assert_called_once()

    def test_no_thanks_and_unrelated_chat_expire_old_suggestions(self):
        for intervening in ("No thanks.", "What is Formula One?"):
            with self.subTest(intervening=intervening):
                self.launch.reset_mock()
                engine = self.engine(proposal(call("vs code")), answer("Formula One is motorsport."))
                engine.respond("I feel like programming.")
                engine.respond(intervening)
                self.assertIsNone(self.conversation.action_state.pending_app)
                self.assertEqual(engine.respond("Yes, go ahead.").requests, 0)
                self.launch.assert_not_called()

    def test_confirmation_cannot_authorize_another_app(self):
        engine = self.engine(proposal(call("vs code")))
        engine.respond("I feel like programming.")
        result = engine.respond("Yes, open Chrome.")
        self.assertEqual(result.requests, 0)
        self.assertIsNone(self.conversation.action_state.pending_app)
        self.launch.assert_not_called()

    def test_nonexact_retargeting_cannot_rearm_or_launch(self):
        engine = self.engine(proposal(call("vs code")), proposal(call("chrome")))
        engine.respond("I feel like programming.")
        result = engine.respond("Go ahead with Chrome.")
        self.assertEqual(result.outcome, "denied")
        self.assertIsNone(self.conversation.action_state.pending_app)
        self.launch.assert_not_called()

    def test_affirmative_conversation_is_not_mistaken_for_confirmation(self):
        engine = self.engine(answer("More about Formula One."))
        result = engine.respond("Yes, tell me more about them.")
        self.assertEqual(result.outcome, "conversation")
        self.assertEqual(result.requests, 1)
        self.launch.assert_not_called()

    def test_unregistered_prose_offer_cannot_arm_a_confirmation(self):
        engine = self.engine(answer("Would you like me to open VS Code?"))
        engine.respond("I feel like programming.")
        self.assertIsNone(self.conversation.action_state.pending_app)
        result = engine.respond("Yes, please.")
        self.assertEqual(result.requests, 0)
        self.launch.assert_not_called()

    def test_forced_unsafe_tool_proposals_never_execute(self):
        for text in (
            "Don't open Chrome.", "If I asked you to open Notepad, what would happen?",
            "My friend said 'open Chrome'.", "Open PowerShell.",
            "Open Chrome and delete my files.",
            "Only open Chrome if my download finishes.", "Tell me about Chrome.",
            "Open Chrome. Search YouTube.", "VS Code can run Python.",
        ):
            with self.subTest(text=text):
                self.launch.reset_mock()
                engine = self.engine(proposal(call("chrome")))
                result = engine.respond(text)
                self.assertIn(result.outcome, {"denied", "clarification"})
                self.launch.assert_not_called()

    def test_explicit_multi_app_scope_executes_sequential_model_choices(self):
        for order in (("chrome", "calculator"), ("calculator", "chrome")):
            with self.subTest(order=order):
                self.launch.reset_mock()
                engine = self.engine(
                    proposal(call(order[0], identifier="first")),
                    proposal(call(order[1], identifier="second")),
                    answer("All applications are ready!"),
                )
                result = engine.respond("Open Chrome and Calculator.")
                self.assertTrue(result.ok)
                self.assertEqual([item.args[0] for item in self.launch.call_args_list], list(order))
                self.assertEqual(result.requests, 3)
                self.assertEqual(result.task.status, "completed")
                self.assertEqual(result.task.decisions, 3)
                self.assertEqual(result.task.attempted_apps, order)
                self.assertEqual(self.conversation.turn_count, 1)
                self.assertEqual([row["role"] for row in self.conversation.history],
                                 ["user", "assistant", "tool", "assistant", "tool", "assistant"])
                self.assertEqual([row["app_id"] for row in self.conversation.verified_results], list(order))
                for row in self.conversation.verified_results:
                    self.assertTrue(row["process_created"])
                    self.assertFalse(row["readiness_checked"])
                remaining = self.client.requests[1][1]["tools"][0]["function"]["parameters"]["properties"]["app_id"]["enum"]
                self.assertEqual(remaining, [order[1]])
                self.assertEqual(self.client.requests[2][1]["tools"], [])
                self.assertIn("2 successful launch attempt(s)", result.text)
                self.assertNotIn("All applications are ready!", result.text)

    def test_malformed_or_unknown_calls_request_clarification_without_execution(self):
        invalid = [call(name="shell"), call("powershell"), call("C:\\Windows\\cmd.exe")]
        for arguments in ({}, {"app_id": "chrome", "command": "delete files"}, {"app_id": []}, '{"app_id":"chrome"}'):
            raw = call()
            raw["function"]["arguments"] = arguments
            invalid.append(raw)
        for raw in invalid:
            with self.subTest(raw=raw):
                engine = self.engine(proposal(raw))
                result = engine.respond("Open Notepad.")
                self.assertIn("couldn't validate", result.text)
                self.assertFalse(any(item.get("process_created") for item in self.conversation.verified_results))
                self.launch.assert_not_called()

    def test_multiple_calls_never_execute_a_partial_subset(self):
        engine = self.engine(proposal(call("chrome", identifier="one"), call("calculator", identifier="two")))
        result = engine.respond("Open Chrome and Calculator.")
        self.assertEqual(result.outcome, "denied")
        self.assertEqual(len(self.conversation.verified_results), 2)
        self.launch.assert_not_called()

    def test_uncompleted_or_postcompletion_proposals_never_execute(self):
        streams = (
            Stream(ChatEvent(tool_calls=(call(),))),
            Stream(ChatEvent(done=True, tool_calls=(call(),)), ChatEvent("late extra content")),
            Stream(ChatEvent(done=True, tool_calls=None)),
        )
        for stream in streams:
            with self.subTest(stream=stream):
                engine = self.engine(stream)
                self.assertFalse(engine.respond("Open Notepad.").ok)
                self.assertTrue(stream.closed)
                self.launch.assert_not_called()

    def test_failed_launch_result_is_model_visible_and_remembered(self):
        self.launch.return_value = ActionResult(False, "private path error omitted")
        engine = self.engine(proposal(call("chrome")), answer("Chrome did not report a successful launch."))
        result = engine.respond("Open Chrome.")
        self.assertFalse(result.tool_result.success)
        self.assertEqual(result.requests, 1)
        self.assertEqual(len(self.client.requests), 1)
        self.assertIn("failed", result.text)
        self.assertEqual(self.conversation.verified_results[-1]["status"], "launch_failed")
        engine.respond("Did you open Chrome?")
        self.assertIn('"status":"launch_failed"', self.client.requests[-1][0][0]["content"])
        self.assertNotIn("private path", str(self.conversation.history))

    def test_failed_launch_cannot_be_rephrased_as_success_by_followup_model(self):
        self.launch.return_value = ActionResult(False, "launcher failed")
        engine = self.engine(proposal(call("chrome")), answer("launch attempt succeeded"))
        result = engine.respond("Open Chrome.")
        self.assertEqual(result.outcome, "launch_failed")
        self.assertEqual(len(self.client.requests), 1)
        self.assertNotIn("succeeded", result.text)
        self.assertFalse(self.conversation.verified_results[-1]["process_created"])

    def test_tool_result_survives_failed_or_cancelled_final_generation(self):
        for error in (OllamaError("stream disconnected"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.launch.reset_mock()
                engine = self.engine(proposal(call()), Stream(error))
                result = engine.respond("Open Notepad.")
                self.assertFalse(result.ok)
                self.assertTrue(result.tool_result.success)
                self.assertEqual(self.conversation.verified_results[-1]["status"], "launch_attempt_succeeded")
                self.launch.assert_called_once_with("notepad")
                self.assertEqual(self.conversation.history[-1]["role"], "assistant")

    def test_final_generation_cannot_execute_additional_tools(self):
        engine = self.engine(proposal(call()), proposal(call("chrome")))
        result = engine.respond("Open Notepad.")
        self.assertFalse(result.ok)
        self.launch.assert_called_once_with("notepad")
        records = self.conversation.verified_results
        self.assertEqual(len(records), 2)
        self.assertEqual((records[0]["app_id"], records[0]["status"], records[0]["process_created"]),
                         ("notepad", "launch_attempt_succeeded", True))
        self.assertEqual((records[1]["status"], records[1]["process_created"]), ("denied", False))
        denied_proposal = self.conversation.history[-3]["tool_calls"][0]
        self.assertEqual(denied_proposal["function"]["arguments"]["app_id"], "chrome")

    def test_interrupted_launch_records_unknown_outcome_without_retry(self):
        engine = self.engine(proposal(call()))
        self.launch.side_effect = KeyboardInterrupt()
        result = engine.respond("Open Notepad.")
        self.assertFalse(result.ok)
        self.launch.assert_called_once_with("notepad")
        self.assertEqual(self.conversation.verified_results[-1]["status"], "launch_outcome_unknown")
        self.assertIsNone(self.conversation.verified_results[-1]["process_created"])
        self.assertIn("outcome is unknown", result.text)

    def test_clear_forgets_pending_and_action_records(self):
        engine = self.engine(proposal(call()), answer("Launch attempt sent."), proposal(call("vs code")))
        engine.respond("Open Notepad.")
        engine.respond("I feel like programming.")
        self.conversation.clear()
        self.assertEqual(self.conversation.history, [])
        self.assertEqual(self.conversation.verified_results, [])
        self.assertIsNone(self.conversation.action_state.pending_app)
        self.assertEqual(engine.respond("Yes, please.").requests, 0)
        self.launch.assert_called_once()

    def test_history_capacity_is_reserved_before_external_effect(self):
        engine = self.engine(proposal(call()))
        with patch.object(self.conversation, "can_store_tool_turn", return_value=False):
            result = engine.respond("Open Notepad.")
        self.assertIn("budget", result.text)
        self.launch.assert_not_called()

    def test_clock_is_fresh_per_turn_and_capabilities_come_from_registry(self):
        engine = self.engine(answer("Today."), answer("Now."))
        with patch("aqua.brain.controller.local_time_context", side_effect=["CLOCK one +05:30", "CLOCK two +05:30"]):
            engine.respond("What date is it?")
            engine.respond("What time is it?")
        self.assertIn("CLOCK one", self.client.requests[0][0][0]["content"])
        self.assertIn("CLOCK two", self.client.requests[1][0][0]["content"])
        for app in ALLOWED_APPS:
            self.assertIn(app, self.client.requests[0][0][0]["content"])
        self.assertEqual([row["function"]["name"] for row in self.client.requests[0][1]["tools"]], ["open_application"])
        self.launch.assert_not_called()

    def test_streaming_timings_and_request_count_are_observable(self):
        engine = self.engine(answer("Hello."))
        engine.clock = Mock(side_effect=[10.0, 10.2, 10.7])
        chunks = []
        result = engine.respond("Hello", on_text=chunks.append)
        self.assertEqual(chunks, ["Hello."])
        self.assertEqual(result.requests, 1)
        self.assertAlmostEqual(result.first_token_seconds, .2)
        self.assertAlmostEqual(result.total_seconds, .7)

    def test_actual_transport_retry_counter_overrides_logical_call_count(self):
        engine = self.engine()
        self.client.chat_request_count = 4

        def retried_request(*_args, **_options):
            self.client.chat_request_count += 2
            return answer("Hello.")

        self.client.stream_chat = retried_request
        self.assertEqual(engine.respond("Hello").requests, 2)


if __name__ == "__main__":
    unittest.main()
