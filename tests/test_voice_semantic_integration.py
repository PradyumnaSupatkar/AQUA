"""Speech, semantic authorization, and conversation cooperate without I/O."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import Mock, patch

from aqua import chat
from aqua.actions.launcher import ActionResult
from aqua.actions.tools import ActionState, tool_definitions
from aqua.audio.backend import AudioError
from aqua.audio.levels import AudioLevels
from aqua.audio.recording import Recording
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, ChatEvent, GenerationSettings, ModelInfo, OllamaError
from aqua.speech.models import SpeechError
from aqua.speech.recognizer import Transcript


def transcript(text):
    return Transcript(text, "en", .95, 1.0, -20, -30, 0)


def reply(text="A conversational answer."):
    return iter([ChatEvent(text), ChatEvent(done=True)])


def proposal(app):
    return iter([ChatEvent(done=True, tool_calls=({"function": {"name": "open_application", "arguments": {"app_id": app}}},))])


class VoiceSemanticIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.state = ActionState()
        self.recording = Recording(b"\x00\x04" * 80000, 16000, AudioLevels(.03, .03, 0), 0)
        self.client = Mock()
        self.client.check_model.return_value = ModelInfo(DEFAULT_CHAT_MODEL, "test", "qwen3", "non_thinking")
        self.client.stream_chat.side_effect = lambda *_args, **_kwargs: reply()
        self.recognizer = Mock()
        self.conversations = []

        def conversation(**options):
            value = Conversation(**options)
            value.bind_action_state(self.state)
            self.conversations.append(value)
            return value

        patches = {
            "client_factory": patch("aqua.chat.OllamaClient", return_value=self.client),
            "speech_factory": patch("aqua.chat.OfflineRecognizer", return_value=self.recognizer),
            "capture": patch("aqua.chat.record_test", return_value=self.recording),
            "reliability": patch("aqua.chat.transcript_rejection_reason", return_value=None),
            "conversation_factory": patch("aqua.chat.Conversation", side_effect=conversation),
            "launch": patch("aqua.actions.tools.open_application", return_value=ActionResult(True, "Opening vs code.")),
            "network": patch("socket.create_connection", side_effect=AssertionError("Tests must not access Ollama")),
            "process": patch("aqua.actions.launcher.subprocess.Popen", side_effect=AssertionError("Tests must not launch applications")),
            "dns": patch("socket.getaddrinfo", side_effect=AssertionError("No DNS in voice unit tests")),
            "connect": patch("socket.socket.connect", side_effect=AssertionError("No socket in voice unit tests")),
        }
        for name, manager in patches.items():
            setattr(self, name, manager.start())
            self.addCleanup(manager.stop)

    def invoke(self, utterances, inputs=None, **kwargs):
        self.recognizer.transcribe.side_effect = [transcript(text) if isinstance(text, str) else text for text in utterances]
        if inputs is None:
            inputs = [""] * len(utterances) + ["/quit"]
        output, errors = StringIO(), StringIO()
        with patch("builtins.input", side_effect=inputs), redirect_stdout(output), redirect_stderr(errors):
            status = chat.run_voice_chat(**kwargs)
        return status, output.getvalue(), errors.getvalue()

    def test_formula_one_conversation_and_driver_followup_keep_context(self):
        utterances = ["What is Formula One?", "My favorite drivers are Carlos Sainz and Max Verstappen.", "Tell me more about them."]
        status, _output, errors = self.invoke(utterances)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(self.client.stream_chat.call_count, 3)
        messages = self.client.stream_chat.call_args.args[0]
        self.assertEqual([message["content"] for message in messages if message["role"] == "user"], utterances)
        self.assertEqual([message["role"] for message in messages], ["system", "user", "assistant", "user", "assistant", "user"])
        self.launch.assert_not_called()
        self.network.assert_not_called()

    def test_pending_app_can_be_confirmed_only_once(self):
        self.client.stream_chat.side_effect = [proposal("vs code"), reply("Launch attempt succeeded.")]
        status, _output, _errors = self.invoke(["I want to do some coding", "Yes, go ahead", "Yes, go ahead"])
        self.assertEqual(status, 0)
        self.launch.assert_called_once_with("vs code")
        self.assertIsNone(self.state.pending_app)
        self.assertEqual(self.client.stream_chat.call_count, 2)
        self.assertEqual(self.conversations[-1].verified_results[-1]["app_id"], "vs code")

    def test_affirmative_conversational_followups_retain_context(self):
        utterances = ["What is Formula One?", "Yeah, Carlos Sainz is my favorite driver.", "Yes, tell me more about him."]
        status, _output, errors = self.invoke(utterances)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(self.client.stream_chat.call_count, 3)
        messages = self.client.stream_chat.call_args.args[0]
        self.assertEqual([message["content"] for message in messages if message["role"] == "user"], utterances)
        self.launch.assert_not_called()

    def test_unrelated_conversation_disarms_pending_before_late_confirmation(self):
        self.client.stream_chat.side_effect = [proposal("vs code"), reply("Formula One is a motorsport.")]
        status, _output, errors = self.invoke(["I want to do some coding", "What is Formula One?", "Yes, go ahead"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.launch.assert_not_called()
        self.assertIsNone(self.state.pending_app)
        self.assertTrue(any(call.args[0][-1]["content"] == "What is Formula One?" for call in self.client.stream_chat.call_args_list))

    def test_session_controls_and_unrelated_typed_input_disarm_pending(self):
        for control in ("/clear", "/help", "/unknown", "unrelated typed text"):
            with self.subTest(control=control):
                self.state.clear()
                self.launch.reset_mock()
                self.client.stream_chat.side_effect = [proposal("vs code")]
                status, _output, _errors = self.invoke(["I want to do some coding", "Yes, go ahead"], ["", control, "", "/quit"])
                self.assertEqual(status, 0)
                self.launch.assert_not_called()
                self.assertIsNone(self.state.pending_app)

    def test_skipped_unreliable_audio_disarms_pending(self):
        self.client.stream_chat.side_effect = [proposal("vs code")]
        self.reliability.side_effect = [None, "not reliable", None]
        status, output, _errors = self.invoke(["I want to do some coding", "noise", "Yes, go ahead"])
        self.assertEqual(status, 0)
        self.assertIn("Skipped:", output)
        self.launch.assert_not_called()

    def test_recording_failure_or_interrupt_disarms_pending(self):
        for failure in (AudioError("disconnected"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                self.state.clear()
                self.launch.reset_mock()
                self.client.stream_chat.side_effect = [proposal("vs code")]
                self.capture.side_effect = [self.recording, failure, self.recording]
                status, _output, _errors = self.invoke(["I want to do some coding", "Yes, go ahead"], ["", "", "", "/quit"])
                self.assertEqual(status, 0)
                self.launch.assert_not_called()

    def test_transcription_error_disarms_pending(self):
        self.client.stream_chat.side_effect = [proposal("vs code")]
        status, _output, errors = self.invoke(["I want to do some coding", SpeechError("recognition failed"), "Yes, go ahead"])
        self.assertEqual(status, 0)
        self.assertIn("recognition failed", errors)
        self.launch.assert_not_called()
        self.assertIsNone(self.state.pending_app)

    def test_native_model_error_disarms_pending(self):
        self.client.stream_chat.side_effect = [proposal("vs code"), OllamaError("timeout")]
        status, output, _errors = self.invoke(["I want to do some coding", "Open Chrome", "Yes, go ahead"])
        self.assertEqual(status, 0)
        self.assertIn("safely", output)
        self.launch.assert_not_called()
        self.assertIsNone(self.state.pending_app)

    def test_native_model_interrupt_cancels_only_current_turn(self):
        self.client.stream_chat.side_effect = [KeyboardInterrupt(), reply("Formula One is a motorsport.")]
        status, output, errors = self.invoke(["Bring up my coding environment", "What is Formula One?"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("cancelled", output)
        self.assertEqual(self.client.stream_chat.call_count, 2)
        self.launch.assert_not_called()

    def test_native_request_reuses_requested_context_and_timeout(self):
        settings = GenerationSettings(num_ctx=2048)
        status, _output, errors = self.invoke(["What is Formula One?"], settings=settings, timeout=7)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.client_factory.assert_called_once_with(timeout=7)
        self.client.stream_chat.assert_called_once()
        self.assertEqual(self.client.stream_chat.call_args.kwargs["tools"], tool_definitions())
        self.assertEqual(settings.num_ctx, 2048)
        self.assertIs(self.client.stream_chat.call_args.kwargs["settings"], settings)

    def test_native_defaults_keep_existing_model_context_and_timeout(self):
        status, _output, errors = self.invoke(["What is Formula One?"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.client_factory.assert_called_once_with(timeout=120.0)
        self.client.stream_chat.assert_called_once()
        self.assertEqual(self.client.stream_chat.call_args.kwargs["model"], DEFAULT_CHAT_MODEL)
        self.assertEqual(self.client.stream_chat.call_args.kwargs["settings"].num_ctx, 4096)

    def test_voice_diagnostics_reports_shared_controller_and_single_request(self):
        status, output, errors = self.invoke(["What is Formula One?"], diagnostics=True)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("Conversation: chat requests 1; outcome conversation; stage model_request", output)
        self.client.stream_chat.assert_called_once()
        self.launch.assert_not_called()

    def test_normal_voice_output_omits_semantic_technical_metadata(self):
        status, output, errors = self.invoke(["What is Formula One?"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertNotIn("diagnostics", self.client.stream_chat.call_args.kwargs)
        self.assertNotIn("Semantic classifier:", output)
        self.assertNotIn("Semantic safety:", output)
        self.client.stream_chat.assert_called_once()


if __name__ == "__main__":
    unittest.main()
