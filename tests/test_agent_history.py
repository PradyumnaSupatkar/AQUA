"""A bounded agent task keeps every tool exchange without requiring I/O."""

import json
import unittest
from unittest.mock import patch

from aqua.brain.conversation import Conversation
from aqua.brain.ollama import ChatEvent, GenerationSettings, OllamaClient


def assistant(app="notepad", *, identifier="step-one", content=""):
    return {"role": "assistant", "content": content, "tool_calls": [
        {"id": identifier, "function": {"name": "open_application", "arguments": {"app_id": app}}},
    ]}


def result(app="notepad", *, identifier="step-one", status="launch_attempt_succeeded"):
    return {"role": "tool", "tool_name": "open_application", "tool_call_id": identifier,
            "content": json.dumps({"app_id": app, "status": status, "readiness_checked": False})}


class AgentHistoryTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("History tests must not perform external I/O."))
            manager.start()
            self.addCleanup(manager.stop)

    def start(self, **options):
        conversation = Conversation(**options)
        conversation.commit_tool_turn("Open Notepad and Calculator.", assistant(), [result()])
        return conversation

    def test_multiple_operations_and_final_answer_are_one_complete_user_turn(self):
        conversation = self.start()
        conversation.append_tool_step(assistant("calculator", identifier="step-two"),
                                      [result("calculator", identifier="step-two")])
        self.assertEqual(conversation.turn_count, 1)
        request = conversation.messages_for_tool_result()
        self.assertEqual([message["role"] for message in request],
                         ["system", "user", "assistant", "tool", "assistant", "tool"])
        self.assertEqual([item["app_id"] for item in conversation.verified_results], ["notepad", "calculator"])
        self.assertTrue(conversation.complete_last_turn("Both process-creation attempts were reported; readiness is unverified."))
        self.assertEqual(conversation.turn_count, 1)
        self.assertEqual(sum(message["role"] == "user" for message in conversation.history), 1)

    def test_actual_transport_accepts_all_prior_pairs_before_the_next_decision(self):
        conversation = self.start()
        conversation.append_tool_step(assistant("calculator", identifier="step-two"),
                                      [result("calculator", identifier="step-two")])
        messages = conversation.messages_for_tool_result()
        client = OllamaClient()
        with patch.object(client, "_stream", return_value=iter([ChatEvent("Finished.", done=True)])) as stream:
            events = list(client.stream_chat(messages, model="qwen3:4b-instruct-2507-q4_K_M",
                                             settings=GenerationSettings(), tools=[]))
        self.assertTrue(events[-1].done)
        self.assertEqual(stream.call_args.args[0]["messages"], messages)

    def test_whole_active_task_must_fit_even_if_the_next_exchange_fits_alone(self):
        conversation = Conversation(max_chars=600, system_prompt="S")
        first = assistant(content="A" * 120)
        second = assistant("calculator", identifier="step-two", content="B" * 120)
        conversation.commit_tool_turn("Open both apps.", first, [result()])
        next_results = [result("calculator", identifier="step-two")]
        self.assertTrue(conversation.can_store_tool_turn("Open both apps.", second, next_results))
        before = conversation.history
        self.assertFalse(conversation.can_append_tool_step(second, next_results))
        with self.assertRaisesRegex(ValueError, "active tool turn"):
            conversation.append_tool_step(second, next_results)
        self.assertEqual(conversation.history, before)

    def test_reservation_counts_fresh_context_and_preserves_all_steps_on_failure(self):
        conversation = self.start(max_chars=900, system_prompt="S")
        proposed, executed = assistant("calculator", identifier="step-two"), [result("calculator", identifier="step-two")]
        self.assertTrue(conversation.can_append_tool_step(proposed, executed))
        self.assertFalse(conversation.can_append_tool_step(proposed, executed, system_context="X" * 850))
        conversation.append_tool_step(proposed, executed)
        before = conversation.history
        with self.assertRaisesRegex(ValueError, "tool result and system context"):
            conversation.messages_for_tool_result(system_context="X" * 850)
        self.assertEqual(conversation.history, before)

    def test_older_turns_are_pruned_without_dropping_an_active_step(self):
        conversation = Conversation(max_chars=800, system_prompt="S")
        conversation.commit("old " * 50, "answer " * 25)
        conversation.commit_tool_turn("Open both apps.", assistant(), [result()])
        self.assertEqual(conversation.turn_count, 2)
        conversation.append_tool_step(assistant("calculator", identifier="step-two"),
                                      [result("calculator", identifier="step-two")])
        self.assertEqual(conversation.turn_count, 1)
        self.assertEqual([message["role"] for message in conversation.history],
                         ["user", "assistant", "tool", "assistant", "tool"])
        self.assertEqual(len(conversation.verified_results), 2)

    def test_failed_operation_and_later_results_remain_available_for_followup(self):
        conversation = self.start()
        conversation.append_tool_step(assistant("chrome", identifier="step-two"),
                                      [result("chrome", identifier="step-two", status="launch_failed")])
        conversation.append_tool_step(assistant("calculator", identifier="step-three"),
                                      [result("calculator", identifier="step-three")])
        conversation.complete_last_turn("Two attempts succeeded and the Chrome attempt failed.")
        request = conversation.messages_for("Which operation failed?")
        results = [json.loads(message["content"]) for message in request if message["role"] == "tool"]
        self.assertEqual([item["status"] for item in results],
                         ["launch_attempt_succeeded", "launch_failed", "launch_attempt_succeeded"])

    def test_request_and_caller_mutation_cannot_change_later_steps(self):
        conversation = self.start()
        proposed, executed = assistant("calculator", identifier="step-two"), [result("calculator", identifier="step-two")]
        conversation.append_tool_step(proposed, executed)
        proposed["tool_calls"][0]["function"]["arguments"]["app_id"] = "chrome"
        executed[0]["content"] = "{}"
        messages = conversation.messages_for_tool_result()
        messages[-2]["tool_calls"].clear()
        self.assertEqual(conversation.history[-2]["tool_calls"][0]["function"]["arguments"], {"app_id": "calculator"})
        self.assertEqual(conversation.verified_results[-1]["app_id"], "calculator")

    def test_invalid_later_pair_is_rejected_transactionally(self):
        for mutate in (
            lambda proposed, executed: executed[0].update(tool_call_id="wrong"),
            lambda proposed, executed: proposed["tool_calls"][0]["function"].update(arguments={"app_id": float("nan")}),
            lambda proposed, executed: executed[0].update(content='{"status":"ok","status":"failed"}'),
        ):
            conversation = self.start()
            proposed, executed = assistant("calculator", identifier="step-two"), [result("calculator", identifier="step-two")]
            mutate(proposed, executed)
            before = conversation.history
            with self.assertRaises(ValueError):
                conversation.can_append_tool_step(proposed, executed)
            with self.assertRaises(ValueError):
                conversation.append_tool_step(proposed, executed)
            self.assertEqual(conversation.history, before)

    def test_append_requires_an_open_task(self):
        conversation = Conversation()
        for operation in (conversation.can_append_tool_step, conversation.append_tool_step):
            with self.assertRaisesRegex(ValueError, "active tool turn"):
                operation(assistant(), [result()])
        conversation = self.start()
        conversation.complete_last_turn("Done.")
        with self.assertRaisesRegex(ValueError, "active tool turn"):
            conversation.append_tool_step(assistant("calculator", identifier="step-two"),
                                          [result("calculator", identifier="step-two")])

    def test_oversized_final_answer_cannot_erase_multi_step_results(self):
        conversation = self.start(max_chars=900, system_prompt="S")
        conversation.append_tool_step(assistant("calculator", identifier="step-two"),
                                      [result("calculator", identifier="step-two")])
        before = conversation.history
        self.assertFalse(conversation.complete_last_turn("X" * 2000))
        self.assertEqual(conversation.history, before)

    def test_clear_forgets_progress_results_and_pending_confirmation(self):
        conversation = self.start()
        conversation.last_task = {"status": "partial", "operations": ["notepad"]}
        conversation.action_state.pending_app = "calculator"
        conversation.clear()
        self.assertIsNone(conversation.last_task)
        self.assertEqual(conversation.history, [])
        self.assertEqual(conversation.verified_results, [])
        self.assertIsNone(conversation.action_state.pending_app)


if __name__ == "__main__":
    unittest.main()
