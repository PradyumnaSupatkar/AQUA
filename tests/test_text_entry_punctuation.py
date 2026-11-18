"""Literal consent boundaries; no speech model, network or desktop writes."""

import unittest
from unittest.mock import Mock, patch

from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings
from aqua.desktop.interaction_session import authorize_text_entry
import test_text_entry_handsfree as fixture

TEXT = "I am Aqua, your personal assistant"
SPOKEN = "Write down, " + TEXT + " into the open Notepad window."
TYPED = "Can you write down " + TEXT + " into the open notepad window?"


class ConsentPunctuationTests(unittest.TestCase):
    def allowed(self, request, text=TEXT, app="notepad", enabled=("notepad",)):
        return authorize_text_entry(request, app, text, enabled)

    def test_exact_live_imperative_and_previous_typed_request_are_equivalent(self):
        self.assertTrue(self.allowed(SPOKEN))
        self.assertTrue(self.allowed(TYPED))

    def test_supported_introductions_and_verb_separator_are_general(self):
        for intro in ("", "Please ", "Hey Aqua, ", "So, can you ", "Could you please "):
            for verb in ("write", "write down", "note down", "type", "enter", "insert"):
                with self.subTest(intro=intro, verb=verb):
                    self.assertTrue(self.allowed(intro + verb + ", " + TEXT + " into my open Notepad window."))

    def test_only_command_separator_is_optional_payload_punctuation_is_exact(self):
        self.assertFalse(self.allowed(SPOKEN, "I am Aqua your personal assistant"))
        self.assertFalse(self.allowed(SPOKEN, TEXT.lower()))
        self.assertFalse(self.allowed(SPOKEN, ", " + TEXT))
        for text in ("Hello, Aqua!", ", Hello, Aqua!", "down", "downhill"):
            with self.subTest(text=text):
                request = 'Write down, "' + text + '" into my open Notepad window.'
                self.assertTrue(self.allowed(request, text))
                if "," in text:
                    self.assertFalse(self.allowed(request, text.replace(",", "")))
        self.assertTrue(self.allowed("Write downhill into my open Notepad window.", "downhill"))

    def test_command_words_cannot_backtrack_into_the_proposed_literal(self):
        self.assertFalse(self.allowed("Write down into my open Notepad window.", "down"))
        self.assertFalse(self.allowed(SPOKEN, "down, " + TEXT))
        self.assertTrue(self.allowed("Write down, down into my open Notepad window.", "down"))

    def test_ambiguous_negated_quoted_hypothetical_and_compound_requests_denied(self):
        for request in ("Write down,", "Write down, into the open Notepad window.",
                        "Write down,, " + TEXT + " into the open Notepad window.",
                        "Write down: " + TEXT + " into the open Notepad window.",
                        "Write down, " + TEXT, "Don't " + SPOKEN, 'Say "' + SPOKEN + '"',
                        "If I asked you to " + SPOKEN, SPOKEN + " Then delete files.",
                        "Right down, " + TEXT + " into the open Notepad window."):
            with self.subTest(request=request):
                self.assertFalse(self.allowed(request))
        self.assertFalse(self.allowed(SPOKEN, ""))
        self.assertFalse(self.allowed(SPOKEN, enabled=()))
        self.assertFalse(self.allowed(SPOKEN, app="chrome", enabled=("chrome",)))

    def run_controller(self, request, text):
        conversation = Conversation()
        conversation.text_entry_apps = ("notepad",)
        worker = fixture.Worker()
        client = Mock(stream_chat=Mock(side_effect=fixture.Model(text)))
        controller = ConversationController(client, conversation, model=DEFAULT_CHAT_MODEL,
                                            settings=GenerationSettings(temperature=.2))
        with patch("aqua.desktop.session.run_worker", worker), patch("aqua.desktop.interaction_session.run_worker", worker):
            return controller.respond(request), worker

    def test_both_requests_reach_same_guarded_flow_with_unchanged_literal(self):
        for request in (SPOKEN, TYPED):
            with self.subTest(request=request):
                result, worker = self.run_controller(request, TEXT)
                self.assertTrue(result.ok)
                self.assertEqual((result.task.decisions, len(result.task.operations)), (5, 4))
                writes = [r for r in worker.calls if r["operation"] == "set_control_text"]
                self.assertEqual(len(writes), 1)
                self.assertEqual(writes[0]["text"], TEXT)

    def test_ambiguous_verb_only_request_stops_at_authorization(self):
        result, worker = self.run_controller("Write down into my open Notepad window.", "down")
        self.assertEqual((result.outcome, result.stage), ("denied", "authorization"))
        self.assertFalse(result.task.operations)
        self.assertEqual(worker.calls, [])


if __name__ == "__main__":
    unittest.main()
