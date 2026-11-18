"""Speech-to-controller regressions; no microphone, Ollama or real UIA input."""

from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

from aqua import chat, handsfree
from aqua.actions.tools import ToolResult
from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import ChatEvent, DEFAULT_CHAT_MODEL, GenerationSettings
from aqua.desktop.interaction_session import authorize_text_entry
import test_handsfree as hf
import test_text_entry as entry


PAYLOAD = "I am Aqua, your personal assistant"
SPOKEN = "So, can you write down " + PAYLOAD + " into the open notepad window?"


class Model:
    """Native decisions that reproduce the contradictory prompt's early stop."""
    def __init__(self, payload=entry.TEXT, *, pause=False, stop=False, refusal=False):
        self.payload, self.pause, self.stop, self.refusal = payload, pause, stop, refusal
        self.calls = []

    def __call__(self, messages, **options):
        self.calls.append((deepcopy(messages), deepcopy(options)))
        number = len(self.calls)
        context = messages[0]["content"]
        if number == 1:
            return iter(entry.event("request_text_entry", {"app_id": "notepad", "text": self.payload}))
        if number == 2 and self.refusal:
            return iter(hf.native_reply("I cannot continue."))
        if ((number == 2 and (self.pause or self.stop or "If no ci_ token is exposed, stop" in context))
                or (number == 3 and (self.stop or "Read-only review:" in context))):
            return iter(hf.native_reply("No editable control has been observed in this task."))
        definitions = {d["function"]["name"]: d["function"] for d in options["tools"]}
        for name, key in (("list_windows", None), ("inspect_window", "window_id"), ("set_control_text", "control_id")):
            if name in definitions:
                arguments = {} if key is None else {key: definitions[name]["parameters"]["properties"][key]["enum"][0]}
                return iter(entry.event(name, arguments))
        return iter(hf.native_reply("Everything is done!"))  # Not trusted as evidence.


class Worker:
    def __init__(self, *, verified=True):
        self.calls, self.verified = [], verified

    def __call__(self, request):
        self.calls.append(deepcopy(request))
        if request["operation"] == "list_windows":
            return {"status": "ok", "windows": [entry.raw_window()]}
        if request["operation"] == "inspect_window":
            result = entry.raw_inspection()
            result["controls"][0]["control_type"] = "Document"
            return result
        if request["operation"] != "set_control_text":
            raise AssertionError("Unexpected desktop operation")
        return {"status": "ok" if self.verified else "unverified", "attempted": True, "verified": self.verified}


class SpeechConsentTests(unittest.TestCase):
    def test_explicit_spoken_forms_preserve_exact_payload(self):
        for request in (SPOKEN, "write down " + PAYLOAD + " into my open Notepad window.",
                        "Hey Aqua, could you please note down " + PAYLOAD + " into the open Notepad window?",
                        'So, can you write down "' + PAYLOAD + '" into my open Notepad window?'):
            with self.subTest(request=request):
                self.assertTrue(authorize_text_entry(request, "notepad", PAYLOAD, ("notepad",)))
                self.assertFalse(authorize_text_entry(request, "notepad", PAYLOAD.lower(), ("notepad",)))
                self.assertFalse(authorize_text_entry(request, "notepad", "Altered text", ("notepad",)))

    def test_partial_wrong_negated_quoted_hypothetical_and_compound_speech_denied(self):
        for request in ("Right", "Right, I am Aqua, your personal assistant in Tube Notepad.",
                        "Note down, I am your personal assistant.", "Note down " + PAYLOAD,
                        "Don't " + SPOKEN, "If I asked: " + SPOKEN, 'Say "' + SPOKEN + '"',
                        SPOKEN + " Then delete my files.",
                        "So, can you not write down " + PAYLOAD + " into my open Notepad window?",
                        "So, can you write down " + PAYLOAD + " into my open Chrome window?"):
            with self.subTest(request=request):
                self.assertFalse(authorize_text_entry(request, "notepad", PAYLOAD, ("notepad",)))


class TextContinuationTests(unittest.TestCase):
    def setUp(self):
        self.worker = Worker()
        for target in ("aqua.desktop.session.run_worker", "aqua.desktop.interaction_session.run_worker"):
            manager = patch(target, self.worker)
            manager.start()
            self.addCleanup(manager.stop)

    def invoke(self, model, text=entry.REQUEST):
        conversation = Conversation()
        conversation.text_entry_apps = ("notepad",)
        client = Mock(stream_chat=Mock(side_effect=model))
        controller = ConversationController(client, conversation, model=DEFAULT_CHAT_MODEL,
                                            settings=GenerationSettings(temperature=.2))
        return controller.respond(text), controller

    def test_authorization_is_not_stopped_by_preinspection_missing_reference(self):
        result, controller = self.invoke(Model())
        self.assertTrue(result.ok)
        self.assertEqual((result.task.decisions, len(result.task.operations)), (5, 4))
        self.assertEqual([r["operation"] for r in self.worker.calls], ["list_windows", "inspect_window", "set_control_text"])
        self.assertIn("verified by UIA readback", result.text)
        self.assertNotIn("empty Edit control", result.text)
        self.assertNotIn("runtime_id", json.dumps(controller.conversation.history))

    def test_one_reconsideration_retains_writing_consent_and_finishes_within_limits(self):
        model = Model(pause=True)
        result, _controller = self.invoke(model)
        self.assertTrue(result.ok)
        self.assertEqual((result.task.decisions, len(result.task.operations)), (6, 4))
        self.assertEqual(len([r for r in self.worker.calls if r["operation"] == "set_control_text"]), 1)
        self.assertNotIn("Read-only review:", model.calls[2][0][0]["content"])

    def test_two_no_call_replies_stop_truthfully_with_a_specific_reason(self):
        result, _controller = self.invoke(Model(stop=True))
        self.assertFalse(result.ok)
        self.assertEqual((result.task.decisions, len(result.task.operations)), (3, 1))
        self.assertEqual(self.worker.calls, [])
        self.assertIn("reconsideration", result.task.stop_reason or "")
        self.assertIn("discovery", result.task.stop_reason or "")
        self.assertNotIn("was verified by UIA readback", result.text)

    def test_genuine_refusal_is_not_reconsidered_and_does_not_execute(self):
        result, controller = self.invoke(Model(refusal=True))
        self.assertFalse(result.ok)
        self.assertEqual(result.task.decisions, 2)
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(controller.conversation.desktop_interaction.control_tokens, ())

    def test_previous_payload_or_success_cannot_authorize_current_ambiguous_turn(self):
        result, controller = self.invoke(Model())
        self.assertTrue(result.ok)
        before = deepcopy(self.worker.calls)
        controller.client.stream_chat.side_effect = lambda *_a, **_k: iter(entry.event("request_text_entry", {"app_id": "notepad", "text": entry.TEXT}))
        result = controller.respond("Right")
        self.assertEqual(result.outcome, "denied")
        self.assertEqual(self.worker.calls, before)
        self.assertEqual(controller.conversation.desktop_interaction.control_tokens, ())
        self.assertEqual([m["content"] for m in controller.conversation.history if m["role"] == "user"][-1], "Right")

    def test_unverified_write_is_not_claimed_successful_or_repeated(self):
        self.worker.verified = False
        result, _controller = self.invoke(Model())
        self.assertFalse(result.ok)
        self.assertEqual(result.task.status, "unknown")
        self.assertEqual(len([r for r in self.worker.calls if r["operation"] == "set_control_text"]), 1)
        self.assertNotIn("Everything is done", result.text)

    def test_continuation_cannot_exceed_operation_or_decision_limits(self):
        for name, value in (("max_tool_operations", 1), ("max_model_decisions", 2)):
            with self.subTest(limit=name):
                self.worker.calls.clear()
                conversation = Conversation()
                conversation.text_entry_apps = ("notepad",)
                client = Mock(stream_chat=Mock(side_effect=Model(pause=True)))
                controller = ConversationController(client, conversation, model=DEFAULT_CHAT_MODEL,
                                                    settings=GenerationSettings(temperature=.2), **{name: value})
                result = controller.respond(entry.REQUEST)
                self.assertFalse(result.ok)
                self.assertIn("limit reached", result.task.stop_reason)
                self.assertEqual(self.worker.calls, [])
                self.assertLessEqual(result.task.decisions, 2)
                self.assertEqual(len(result.task.operations), 1)


class HandsfreeEntryTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing acoustic/session fixture without re-running its tests.
        self.frontend = hf.HandsfreeSessionTests()
        self.frontend.setUp()
        self.addCleanup(self.frontend.doCleanups)
        self.worker = Worker()
        for target in ("aqua.desktop.session.run_worker", "aqua.desktop.interaction_session.run_worker"):
            manager = patch(target, self.worker)
            manager.start()
            self.addCleanup(manager.stop)
        self.controllers = []
        def controller(*args, **kwargs):
            result = ConversationController(*args, **kwargs)
            self.controllers.append(result)
            return result
        manager = patch.object(chat, "ConversationController", side_effect=controller)
        manager.start()
        self.addCleanup(manager.stop)

    def test_simulated_wake_transcription_and_typed_instruction_share_authorization(self):
        for spoken, payload in ((entry.REQUEST, entry.TEXT), ("Hey Aqua, " + SPOKEN, PAYLOAD)):
            with self.subTest(spoken=spoken):
                self.worker.calls.clear()
                model = Model(payload, pause=True)
                self.frontend.client.stream_chat.side_effect = model
                status, output, errors = self.frontend.invoke([spoken], language="en", text_entry_apps=("notepad",),
                                                             settings=GenerationSettings(temperature=.2), diagnostics=True)
                self.assertEqual((status, errors), (0, ""))
                self.assertIn("verified by UIA readback", output)
                controller = self.controllers[-1]
                self.assertEqual(controller.conversation.text_entry_apps, ("notepad",))
                self.assertEqual(controller.conversation.last_task.status, "completed")
                self.assertEqual(controller.conversation.last_task.decisions, 6)
                self.assertEqual(self.worker.calls[-1]["text"], payload)
                self.assertEqual(len([r for r in self.worker.calls if r["operation"] == "set_control_text"]), 1)
                self.assertEqual(model.calls[0][0][-1]["content"], handsfree.command_text(spoken))

    def test_ambiguous_transcription_is_not_repaired_from_old_payload(self):
        model = Model()
        self.frontend.client.stream_chat.side_effect = model
        status, output, errors = self.frontend.invoke(["Hey Aqua, Right"], text_entry_apps=("notepad",))
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(self.worker.calls, [])
        self.assertIn("No new text entry was authorized", output)
        self.assertEqual(model.calls[0][0][-1]["content"], "Right")

    def test_ordinary_conversation_retains_history_without_text_entry(self):
        self.frontend.responses.extend([hf.native_reply("Acknowledged."), hf.native_reply("More about your assistant.")])
        status, output, errors = self.frontend.invoke(["Hey Aqua, I am your personal assistant.", "Hey Aqua, tell me more."], text_entry_apps=("notepad",))
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(self.worker.calls, [])
        users = [m["content"] for m in self.frontend.client.stream_chat.call_args.args[0] if m["role"] == "user"]
        self.assertEqual(users, ["I am your personal assistant.", "tell me more."])
        self.assertIn("More about your assistant.", output)

    def test_prior_launch_does_not_replace_or_contaminate_new_text_task(self):
        model = Model()
        launch_replies = iter((hf.native_proposal("notepad"), hf.native_reply("Launch reported.")))
        def respond(messages, **options):
            self.assertTrue(self.frontend.microphone.paused)
            if len(self.frontend.client.stream_chat.call_args_list) <= 2:
                return iter(next(launch_replies))
            return model(messages, **options)
        self.frontend.client.stream_chat.side_effect = respond
        with patch("aqua.actions.tools.execute_application", return_value=ToolResult("notepad", "launch_attempt_succeeded", True)) as launch:
            status, output, errors = self.frontend.invoke(["Hey Aqua, open Notepad.", entry.REQUEST],
                                                         text_entry_apps=("notepad",), settings=GenerationSettings(temperature=.2))
        self.assertEqual((status, errors), (0, ""))
        launch.assert_called_once_with("notepad")
        task = self.controllers[-1].conversation.last_task
        self.assertEqual((task.decisions, len(task.operations), task.status), (5, 4, "completed"))
        self.assertEqual([m["content"] for m in model.calls[0][0] if m["role"] == "user"],
                         ["open Notepad.", handsfree.command_text(entry.REQUEST)])
        self.assertEqual(self.worker.calls[-1]["text"], entry.TEXT)
        self.assertIn("verified by UIA readback", output)

    def test_old_success_does_not_authorize_a_new_ambiguous_speech_turn(self):
        model = Model()
        def respond(messages, **options):
            self.assertTrue(self.frontend.microphone.paused)
            if len(model.calls) < 5:
                return model(messages, **options)
            return iter(entry.event("request_text_entry", {"app_id": "notepad", "text": entry.TEXT}))
        self.frontend.client.stream_chat.side_effect = respond
        status, output, errors = self.frontend.invoke([entry.REQUEST, "Hey Aqua, Right"], text_entry_apps=("notepad",),
                                                     settings=GenerationSettings(temperature=.2))
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(len([r for r in self.worker.calls if r["operation"] == "set_control_text"]), 1)
        self.assertIn("No new text entry was authorized", output)
        conversation = self.controllers[-1].conversation
        self.assertEqual(conversation.desktop_interaction.control_tokens, ())
        self.assertEqual(conversation.history[-3]["role"], "assistant")
        self.assertEqual([m["content"] for m in conversation.history if m["role"] == "user"][-1], "Right")


if __name__ == "__main__":
    unittest.main()
