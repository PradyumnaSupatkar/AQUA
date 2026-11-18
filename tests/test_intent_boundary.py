"""Semantic proposals must not turn ambiguous user text into write consent."""

from unittest import TestCase
from unittest.mock import Mock, patch

from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings
from aqua.actions import tools
import test_text_entry_handsfree as fixture


class IntentBoundaryTests(TestCase):
    def test_paused_append_tool_is_neither_offered_nor_accepted(self):
        names = {d['function']['name'] for d in tools.available_definitions(text_entry_apps=('notepad',))}
        self.assertNotIn('append_control_text', names)
        self.assertNotIn('request_text_append', names)
        with self.assertRaises(tools.ToolValidationError):
            tools.validate_registered_call({'function': {'name': 'append_control_text', 'arguments': {'control_id': 'ci_exampletoken'}}})

    def run_entry(self, request, literal, *, history=False):
        conversation = Conversation()
        conversation.text_entry_apps = ('notepad',)
        if history:
            conversation.commit('My name is Sam. Remember it.', 'Your name is Sam.')
        worker = fixture.Worker()
        client = Mock(stream_chat=Mock(side_effect=fixture.Model(literal)))
        engine = ConversationController(client, conversation, model=DEFAULT_CHAT_MODEL,
                                        settings=GenerationSettings(temperature=.2))
        with patch('aqua.desktop.session.run_worker', worker), \
                patch('aqua.desktop.interaction_session.run_worker', worker), \
                patch('aqua.actions.tools.execute_application') as launch:
            result = engine.respond(request)
        launch.assert_not_called()
        return result, worker, conversation

    def test_unquoted_deictic_is_not_literal_write_authority_even_if_model_insists(self):
        for history in (False, True):
            for literal in ('that', 'it', 'something', 'anything'):
                with self.subTest(history=history, literal=literal):
                    result, worker, conversation = self.run_entry(
                        f'Write {literal} into my open Notepad window.', literal, history=history)
                    self.assertEqual(result.outcome, 'clarification')
                    self.assertEqual(result.stage, 'authorization')
                    self.assertEqual(worker.calls, [])
                    self.assertEqual(len(result.task.operations), 0)
                    self.assertIsNone(conversation.action_state.pending_app)
                    self.assertIn('?', result.text)

    def test_explicit_quoted_word_preserves_legitimate_literal(self):
        for literal in ('that', 'something', "Don't open Chrome.", 'Hello, Aqua!'):
            with self.subTest(literal=literal):
                result, worker, _ = self.run_entry(
                    f'Type "{literal}" into my open Notepad window.', literal)
                self.assertEqual(result.outcome, 'text_entry_completed')
                writes = [r for r in worker.calls if r['operation'] == 'set_control_text']
                self.assertEqual([r['text'] for r in writes], [literal])

    def test_whitespace_does_not_bypass_ambiguity_veto_or_change_quoted_literal(self):
        result, worker, _ = self.run_entry('Write something  into my open Notepad window.', 'something ')
        self.assertEqual(result.outcome, 'clarification')
        self.assertEqual(worker.calls, [])
        literal = ' something '
        result, worker, _ = self.run_entry(f'Type "{literal}" into my open Notepad window.', literal)
        self.assertEqual(result.outcome, 'text_entry_completed')
        self.assertEqual([r['text'] for r in worker.calls if r['operation'] == 'set_control_text'], [literal])

    def test_ordinary_literal_still_uses_verified_empty_entry_once(self):
        result, worker, _ = self.run_entry('Write Hello from Aqua into my open Notepad window.', 'Hello from Aqua')
        self.assertEqual(result.outcome, 'text_entry_completed')
        self.assertEqual(sum(r['operation'] == 'set_control_text' for r in worker.calls), 1)
        self.assertLessEqual(result.task.decisions, 6)
        self.assertLessEqual(len(result.task.operations), 5)

    def test_model_substitution_and_negation_do_not_gain_authority(self):
        for request, proposed in (
            ('Write Hello from Aqua into my open Notepad window.', 'Sam'),
            ('Do not write Hello from Aqua into my open Notepad window.', 'Hello from Aqua'),
            ('If I asked you to write Hello from Aqua into my open Notepad window, what would happen?', 'Hello from Aqua'),
            ('Tell me about Notepad.', 'Hello from Aqua'),
        ):
            with self.subTest(request=request):
                result, worker, _ = self.run_entry(request, proposed, history=True)
                self.assertEqual(result.outcome, 'denied')
                self.assertEqual(worker.calls, [])

    def test_understood_indirect_goals_still_require_one_use_launch_confirmation(self):
        for request, app in (("I'd like somewhere to jot down a few thoughts.", 'notepad'),
                             ('I feel like doing some programming.', 'vs code')):
            with self.subTest(request=request):
                client = Mock(stream_chat=Mock(return_value=iter(fixture.entry.event('open_application', {'app_id': app}))))
                conversation = Conversation()
                engine = ConversationController(client, conversation, model=DEFAULT_CHAT_MODEL,
                                                settings=GenerationSettings(temperature=.2))
                with patch.object(tools, 'execute_application') as execute:
                    result = engine.respond(request)
                execute.assert_not_called()
                self.assertEqual(result.outcome, 'clarification')
                self.assertEqual(conversation.action_state.pending_app, app)

    def test_ambiguity_clarification_cannot_be_approved_with_a_bare_yes(self):
        _, _, conversation = self.run_entry('Write that into my open Notepad window.', 'that')
        client = Mock()
        engine = ConversationController(client, conversation, model=DEFAULT_CHAT_MODEL, settings=GenerationSettings())
        with patch.object(tools, 'execute_application') as execute:
            result = engine.respond('Yes, go ahead.')
        client.stream_chat.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(result.outcome, 'stale_confirmation')
