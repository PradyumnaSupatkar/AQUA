"""Action results and their model requests stay paired in bounded RAM history."""

import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aqua.brain.conversation import Conversation, DEFAULT_SYSTEM_PROMPT
from aqua.brain.ollama import ChatEvent, GenerationSettings, OllamaClient


def assistant(app="notepad", name="open_application"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": {"app_id": app}}},
    ]}


def result(app="notepad", status="launch_started", name="open_application"):
    return {"role": "tool", "tool_name": name, "content": json.dumps({
        "app_id": app, "status": status, "readiness_confirmed": False,
    })}


class ToolHistoryTests(unittest.TestCase):
    def test_default_identity_is_honest_about_host_tools(self):
        self.assertIn("Aqua", DEFAULT_SYSTEM_PROMPT)
        self.assertIn("privacy-first", DEFAULT_SYSTEM_PROMPT)
        self.assertIn("verified host results", DEFAULT_SYSTEM_PROMPT)
        self.assertNotIn("no tools", DEFAULT_SYSTEM_PROMPT.lower())

    def test_success_result_enters_model_history_before_final_generation(self):
        conversation = Conversation()
        conversation.commit_tool_turn("Open Notepad.", assistant(), [result()])
        request = conversation.messages_for_tool_result()
        self.assertEqual([item["role"] for item in request], ["system", "user", "assistant", "tool"])
        self.assertEqual(request[-2]["tool_calls"][0]["function"]["arguments"], {"app_id": "notepad"})
        self.assertEqual(json.loads(request[-1]["content"])["status"], "launch_started")
        self.assertEqual(conversation.turn_count, 1)

    def test_optional_final_answer_completes_the_same_user_turn(self):
        conversation = Conversation()
        conversation.commit_tool_turn("Open Notepad.", assistant(), [result()])
        self.assertTrue(conversation.complete_last_turn("The launcher started Notepad; window readiness is unverified."))
        self.assertEqual(conversation.turn_count, 1)
        request = conversation.messages_for("Which application did you just open?")
        self.assertEqual([item["role"] for item in request], ["system", "user", "assistant", "tool", "assistant", "user"])
        self.assertIn("Notepad", request[-2]["content"])

    def test_tool_history_is_accepted_by_the_actual_client_validation(self):
        conversation = Conversation()
        conversation.commit_tool_turn("Open Notepad.", assistant(), [result()])
        messages = conversation.messages_for_tool_result()
        client = OllamaClient()
        with patch.object(client, "_stream", return_value=iter([ChatEvent("Started Notepad.", done=True)])) as stream:
            events = list(client.stream_chat(messages, model="qwen3:4b-instruct-2507-q4_K_M",
                                             settings=GenerationSettings(), tools=[]))
        self.assertTrue(events[-1].done)
        payload = stream.call_args.args[0]
        self.assertEqual(payload["messages"], messages)
        self.assertEqual(payload["tools"], [])

    def test_failed_launch_is_also_visible_as_a_failure(self):
        conversation = Conversation()
        conversation.commit_tool_turn("Open Chrome.", assistant("chrome"), [result("chrome", "launch_failed")])
        request = conversation.messages_for("Did you open Chrome?")
        self.assertEqual(json.loads(request[-2]["content"])["status"], "launch_failed")
        self.assertFalse(conversation.verified_results[0]["readiness_confirmed"])

    def test_interrupted_or_failed_final_generation_cannot_erase_actual_result(self):
        conversation = Conversation()
        conversation.commit_tool_turn("Open Notepad.", assistant(), [result()])
        before = conversation.history
        conversation.messages_for_tool_result(system_context="Fresh clock and result context")
        # No final-answer commit represents cancellation, disconnect, or timeout.
        self.assertEqual(conversation.history, before)
        self.assertEqual(conversation.history[-1]["role"], "tool")

    def test_mutating_request_or_caller_inputs_cannot_change_arguments_or_results(self):
        proposed, executed = assistant(), [result()]
        conversation = Conversation()
        conversation.commit_tool_turn("Open Notepad.", proposed, executed)
        proposed["tool_calls"][0]["function"]["arguments"]["app_id"] = "chrome"
        executed[0]["content"] = "{}"
        request = conversation.messages_for("Follow-up")
        request[2]["tool_calls"][0]["function"]["arguments"]["app_id"] = "calculator"
        history = conversation.history
        history[1]["tool_calls"].clear()
        records = conversation.verified_results
        records[0]["status"] = "invented"
        self.assertEqual(conversation.history[1]["tool_calls"][0]["function"]["arguments"], {"app_id": "notepad"})
        self.assertEqual(conversation.verified_results[0]["status"], "launch_started")

    def test_turn_limit_removes_the_entire_tool_bundle(self):
        conversation = Conversation(max_turns=1)
        conversation.commit_tool_turn("Open Notepad.", assistant(), [result()], "Started Notepad.")
        conversation.commit("What is Python?", "A programming language.")
        self.assertEqual([item["role"] for item in conversation.history], ["user", "assistant"])
        self.assertEqual(conversation.verified_results, [])

    def test_final_answer_prunes_older_turn_without_breaking_its_own_tool_pair(self):
        conversation = Conversation(max_chars=300, system_prompt="S")
        conversation.commit("old " * 10, "prior " * 8)
        conversation.commit_tool_turn("Open Notepad.", assistant(), [result()])
        self.assertTrue(conversation.complete_last_turn("Final " * 10))
        self.assertEqual(conversation.turn_count, 1)
        self.assertEqual([item["role"] for item in conversation.history], ["user", "assistant", "tool", "assistant"])

    def test_ephemeral_context_is_fresh_and_never_persisted(self):
        conversation = Conversation()
        conversation.commit("Remember Python", "Okay")
        first = conversation.messages_for("Time?", system_context="Clock: 10:00 +05:30")
        second = conversation.messages_for("Time?", system_context="Clock: 10:01 +05:30")
        self.assertIn("10:00", first[0]["content"])
        self.assertIn("10:01", second[0]["content"])
        self.assertNotIn("10:00", second[0]["content"])
        self.assertNotIn("Clock:", json.dumps(conversation.history))

    def test_temporary_context_prunes_whole_request_turns_not_stored_history(self):
        conversation = Conversation(max_chars=250, system_prompt="S")
        conversation.commit_tool_turn("Open Notepad.", assistant(), [result()])
        before = conversation.history
        request = conversation.messages_for("Hello", system_context="x" * 100)
        self.assertEqual([item["role"] for item in request], ["system", "user"])
        self.assertEqual(conversation.history, before)

    def test_reservation_rejects_tool_turn_too_large_for_budget_before_execution(self):
        conversation = Conversation(max_chars=100, system_prompt="S")
        conversation.commit("old", "answer")
        before = conversation.history
        self.assertFalse(conversation.can_store_tool_turn("Open Notepad.", assistant(), [result()]))
        with self.assertRaisesRegex(ValueError, "complete tool turn"):
            conversation.commit_tool_turn("Open Notepad.", assistant(), [result()])
        self.assertEqual(conversation.history, before)

    def test_reservation_includes_ephemeral_capability_and_clock_context(self):
        conversation = Conversation(max_chars=300, system_prompt="S")
        self.assertTrue(conversation.can_store_tool_turn("Open Notepad.", assistant(), [result()]))
        self.assertFalse(conversation.can_store_tool_turn("Open Notepad.", assistant(), [result()], system_context="x" * 200))

    def test_oversized_final_answer_is_omitted_without_losing_result(self):
        conversation = Conversation(max_chars=300, system_prompt="S")
        conversation.commit_tool_turn("Open Notepad.", assistant(), [result()])
        before = conversation.history
        self.assertFalse(conversation.complete_last_turn("x" * 1000))
        self.assertEqual(conversation.history, before)

    def test_tool_followup_never_drops_current_pair_to_fit_large_context(self):
        conversation = Conversation(max_chars=300, system_prompt="S")
        conversation.commit_tool_turn("Open Notepad.", assistant(), [result()])
        before = conversation.history
        with self.assertRaisesRegex(ValueError, "tool result and system context"):
            conversation.messages_for_tool_result(system_context="x" * 200)
        self.assertEqual(conversation.history, before)

    def test_final_answer_without_pending_tool_result_is_rejected(self):
        conversation = Conversation()
        for operation in (conversation.messages_for_tool_result, lambda: conversation.complete_last_turn("Answer")):
            with self.assertRaises(ValueError):
                operation()
        conversation.commit("Hi", "Hello")
        with self.assertRaises(ValueError):
            conversation.messages_for_tool_result()

    def test_mismatched_or_missing_tool_results_cannot_be_committed(self):
        malformed = (
            (assistant(), []),
            (assistant(), [result(), result()]),
            (assistant(), [result(name="unknown_tool")]),
            ({"role": "assistant", "content": "", "tool_calls": []}, [result()]),
            ({"role": "assistant", "content": "", "thinking": "private", "tool_calls": assistant()["tool_calls"]}, [result()]),
        )
        for proposal, results in malformed:
            with self.subTest(proposal=proposal, results=results):
                conversation = Conversation()
                with self.assertRaises(ValueError):
                    conversation.commit_tool_turn("Open Notepad.", proposal, results)
                self.assertEqual(conversation.history, [])

    def test_tool_result_content_requires_strict_json_object(self):
        for content in ("invalid", "[]", "null", '{"status":"ok","status":"failed"}', '{"value":NaN}'):
            with self.subTest(content=content):
                invalid = {**result(), "content": content}
                with self.assertRaises(ValueError):
                    Conversation().commit_tool_turn("Open Notepad.", assistant(), [invalid])

    def test_tool_call_identifiers_and_argument_shapes_are_protocol_validated(self):
        for modify in (
            lambda call: call.update(id=None),
            lambda call: call.update(index=True),
            lambda call: call["function"].update(index=-1),
            lambda call: call["function"].update(arguments="not an object"),
            lambda call: call["function"].update(arguments={"app_id": float("nan")}),
        ):
            proposed = assistant()
            modify(proposed["tool_calls"][0])
            with self.subTest(proposed=proposed), self.assertRaises(ValueError):
                Conversation().commit_tool_turn("Open Notepad.", proposed, [result()])

    def test_optional_native_call_identifier_matches_result(self):
        proposal = assistant()
        proposal["tool_calls"][0].update(id="call-one", type="function")
        executed = {**result(), "tool_call_id": "call-one"}
        conversation = Conversation()
        conversation.commit_tool_turn("Open Notepad.", proposal, [executed])
        self.assertEqual(conversation.history[-1]["tool_call_id"], "call-one")
        executed["tool_call_id"] = "different-call"
        with self.assertRaises(ValueError):
            Conversation().commit_tool_turn("Open Notepad.", proposal, [executed])

    def test_clear_forgets_tool_records_and_the_same_bound_authorization_state(self):
        class State:
            def __init__(self):
                self.pending_app = "vs code"

            def clear(self):
                self.pending_app = None

        # Isolate history ownership from the executor implementation.
        with patch.dict("sys.modules", {"aqua.actions.tools": SimpleNamespace(ActionState=State)}):
            conversation = Conversation()
            state = State()
            conversation.bind_action_state(state)
            self.assertIs(conversation.action_state, state)
            conversation.commit_tool_turn("Open Notepad.", assistant(), [result()])
            conversation.clear()
            self.assertEqual(conversation.history, [])
            self.assertEqual(conversation.verified_results, [])
            self.assertIsNone(state.pending_app)
            self.assertIs(conversation.action_state, state)


if __name__ == "__main__":
    unittest.main()
