"""Initial no-tool capability denials; both real front ends, offline fixtures."""

import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings
from tools import smoke_text_entry as smoke
import test_text_entry_handsfree as fixture

DENIAL = "I cannot access open windows or their UI from here."


class InitialDenial(fixture.Model):
    def __init__(self, reply=DENIAL, *, always=False, payload=fixture.PAYLOAD):
        super().__init__(payload)
        self.reply, self.always, self.requests, self.inputs = reply, always, 0, []

    def __call__(self, messages, **options):
        self.requests += 1
        self.inputs.append(deepcopy(messages))
        if self.requests == 1 or self.always:
            return iter(fixture.hf.native_reply(self.reply))
        return super().__call__(messages, **options)


class InitialCapabilityReviewTests(unittest.TestCase):
    def trial(self, mode, scenario, model, request=smoke.REQUEST):
        with patch("aqua.brain.controller.local_time_context", return_value="Fixed test clock"):
            return smoke.run_trial(Mock(stream_chat=Mock(side_effect=model)), mode, scenario, request=request)

    def test_exact_request_recovers_initial_zero_call_denial_in_both_modes_and_histories(self):
        for scenario in ("fresh", "history"):
            rows = []
            for mode in ("chat", "handsfree"):
                with self.subTest(mode=mode, scenario=scenario):
                    model = InitialDenial()
                    row = self.trial(mode, scenario, model)
                    rows.append(row)
                    self.assertEqual(row["status"], "completed")
                    self.assertEqual((row["decisions"], len(row["operations"]), row["stub_writes"]), (6, 4, 1))
                    self.assertEqual(row["operations"], ["request_text_entry", "list_windows", "inspect_window", "set_control_text"])
                    self.assertIn("request_text_entry", row["first_tools"])
            self.assertEqual(rows[0]["first_request_sha256"], rows[1]["first_request_sha256"])

    def test_initial_opt_in_context_describes_actual_guarded_desktop_capability(self):
        model = fixture.Model(fixture.PAYLOAD)
        row = self.trial("handsfree", "history", model)
        self.assertTrue(row["ok"])
        context = model.calls[0][0][0]["content"]
        self.assertNotIn("There are no other desktop tools", context)
        self.assertIn("inspect_window", context)
        self.assertIn("set_control_text", context)

    def test_second_capability_denial_is_incomplete_without_authorization_or_side_effect(self):
        for mode in ("chat", "handsfree"):
            row = self.trial(mode, "history", InitialDenial(always=True))
            self.assertFalse(row["ok"])
            self.assertEqual((row["status"], row["decisions"], row["operations"], row["stub_writes"]), ("incomplete", 2, [], 0))
            self.assertIn("capability review", row["stop_reason"])
            self.assertNotIn("was verified", row["early_reply"])

    def test_review_cannot_borrow_consent_or_payload_from_history(self):
        for request in ("Right", "Yes, go ahead", "Don't " + smoke.REQUEST, 'Say "' + smoke.REQUEST + '"',
                        "If I asked: " + smoke.REQUEST, smoke.REQUEST + " Delete my files."):
            with self.subTest(request=request):
                row = self.trial("handsfree", "history", InitialDenial(), request)
                self.assertEqual(row["stub_writes"], 0)
                self.assertFalse(row["operations"])

    def test_actual_safety_refusal_does_not_trigger_capability_reconsideration(self):
        for mode in ("chat", "handsfree"):
            model = InitialDenial("I refuse to help access another person's private windows.", always=True)
            row = self.trial(mode, "history", model)
            self.assertEqual((model.requests, row["stub_writes"]), (1, 0))

    def test_review_cannot_add_extra_reconsideration_after_authorization(self):
        model = InitialDenial()
        original = model.__class__.__call__
        def respond(messages, **options):
            if model.requests == 2:
                model.requests += 1
                return iter(fixture.hf.native_reply("I will do that next."))
            return original(model, messages, **options)
        row = self.trial("handsfree", "fresh", respond)
        self.assertEqual((row["decisions"], row["operations"], row["stub_writes"]), (3, ["request_text_entry"], 0))
        self.assertEqual(row["status"], "incomplete")

    def test_opt_in_absent_or_decision_limit_prevents_initial_review(self):
        for enabled, limit in (((), 6), (("notepad",), 1)):
            model = InitialDenial(always=True)
            conversation = Conversation()
            conversation.text_entry_apps = enabled
            controller = ConversationController(Mock(stream_chat=Mock(side_effect=model)), conversation,
                model=DEFAULT_CHAT_MODEL, settings=GenerationSettings(temperature=.2), max_model_decisions=limit)
            result = controller.respond(smoke.REQUEST)
            self.assertEqual(model.requests, 1)
            self.assertFalse(result.task.operations)

    def test_task_local_recovery_keeps_complete_memory_and_current_tool_history(self):
        model = InitialDenial()
        conversation = smoke.history_session("history")
        conversation.text_entry_apps = ("notepad",)
        before = conversation.history
        worker = fixture.Worker()
        controller = ConversationController(Mock(stream_chat=Mock(side_effect=model)), conversation,
            model=DEFAULT_CHAT_MODEL, settings=GenerationSettings(temperature=.2))
        with patch("aqua.desktop.session.run_worker", worker), patch("aqua.desktop.interaction_session.run_worker", worker):
            result = controller.respond(smoke.REQUEST)
        self.assertTrue(result.ok)
        self.assertEqual(conversation.history[:len(before)], before)
        self.assertEqual([m["role"] for m in model.inputs[1]], ["system", "user"])
        self.assertEqual(model.inputs[1][-1]["content"], smoke.REQUEST)
        self.assertNotIn(DENIAL, str(model.inputs[1]))
        # Follow-up retains each current call/result, not an invented short summary.
        self.assertEqual([m["role"] for m in model.inputs[-1]],
                         ["system", "user", "assistant", "tool", "assistant", "tool", "assistant", "tool", "assistant", "tool"])
        self.assertEqual([m["content"] for m in model.inputs[-1] if m["role"] == "user"], [smoke.REQUEST])
        self.assertIn(DENIAL, str(conversation.messages_for("What did I ask earlier?")))
        self.assertEqual(len([r for r in worker.calls if r["operation"] == "set_control_text"]), 1)

    def test_cancellation_during_initial_review_has_no_effect_or_stale_grant(self):
        replies = iter((fixture.hf.native_reply(DENIAL), KeyboardInterrupt()))
        def respond(*_args, **_kwargs):
            item = next(replies)
            if isinstance(item, BaseException): raise item
            return iter(item)
        conversation = Conversation()
        conversation.text_entry_apps = ("notepad",)
        controller = ConversationController(Mock(stream_chat=Mock(side_effect=respond)), conversation,
            model=DEFAULT_CHAT_MODEL, settings=GenerationSettings(temperature=.2))
        result = controller.respond(smoke.REQUEST)
        self.assertEqual(result.task.status, "cancelled")
        self.assertFalse(result.task.operations)
        self.assertIsNone(conversation.action_state.pending_app)


if __name__ == "__main__":
    unittest.main()
