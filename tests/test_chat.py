"""Interactive sessions use mocked local services and synthetic audio only."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aqua import chat
from aqua.actions.tools import tool_definitions
from aqua.audio.backend import AudioError
from aqua.audio.levels import AudioLevels
from aqua.audio.recording import Recording
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, ChatEvent, GenerationSettings, ModelInfo, OllamaError
from aqua.speech.models import SpeechError
from aqua.speech.recognizer import Transcript


class Stream:
    def __init__(self, *events):
        self.events = iter(events)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        event = next(self.events)
        if isinstance(event, BaseException):
            raise event
        return event

    def close(self):
        self.closed = True


def reply(text="Hello."):
    return Stream(ChatEvent(text), ChatEvent(done=True))


def recording():
    return Recording(bytes(1000), 100, AudioLevels(0.1, 0.05, 0), 0)


def transcript(text="Hello Aqua."):
    return Transcript(text, "en", 0.95, 2.0, -12.0, -24.0, 0)


class _SessionHarness(unittest.TestCase):
    def setUp(self):
        # Missing dependency mocks must fail immediately, never wait on Ollama.
        for target in ("socket.create_connection", "socket.getaddrinfo", "socket.socket.connect", "subprocess.Popen"):
            network_patch = patch(target, side_effect=AssertionError("Session tests must not access the network"))
            network_patch.start()
            self.addCleanup(network_patch.stop)
        self.client = Mock()
        self.client.check_model.return_value = ModelInfo(DEFAULT_CHAT_MODEL, "local-test", "qwen3", "non_thinking")
        self.client.stream_chat.side_effect = lambda *_args, **_kwargs: reply()
        client_patch = patch("aqua.chat.OllamaClient", return_value=self.client)
        self.factory = client_patch.start()
        self.addCleanup(client_patch.stop)
        # One native conversational model response handles ordinary turns.

    def invoke(self, inputs, *, voice=False, **options):
        output, errors = StringIO(), StringIO()
        with patch("builtins.input", side_effect=inputs), redirect_stdout(output), redirect_stderr(errors):
            status = (chat.run_voice_chat if voice else chat.run_text_chat)(**options)
        return status, output.getvalue(), errors.getvalue()


class SessionTests(_SessionHarness):
    def test_text_keeps_successful_context_and_streams_multiple_turns(self):
        streams = [reply("I remember."), reply("Your name is Sam.")]
        self.client.stream_chat.side_effect = streams
        status, output, errors = self.invoke(["My name is Sam.", "What is my name?", "/quit"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.client.check_model.assert_called_once_with(DEFAULT_CHAT_MODEL)
        self.assertEqual(self.client.stream_chat.call_count, 2)
        messages = self.client.stream_chat.call_args.args[0]
        self.assertEqual([(m["role"], m["content"]) for m in messages[1:]], [
            ("user", "My name is Sam."), ("assistant", "I remember."),
            ("user", "What is my name?"),
        ])
        self.assertIn("Aqua: I remember.", output)
        self.assertIn("Your name is Sam.", output)
        self.assertTrue(all(stream.closed for stream in streams))

    def test_help_unknown_blank_and_clear_controls_do_not_reach_model(self):
        status, output, _errors = self.invoke(["", "/help", "/unknown", "Remember this.", "/clear", "New turn.", "/quit"])
        self.assertEqual(status, 0)
        self.assertEqual(self.client.stream_chat.call_count, 2)
        messages = self.client.stream_chat.call_args.args[0]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["content"], "New turn.")
        self.assertIn("Conversation cleared", output)
        self.assertIn("Unknown command", output)

    def test_history_is_bounded_in_live_session(self):
        status, _output, _errors = self.invoke(["One", "Two", "Three", "/quit"], max_turns=1)
        self.assertEqual(status, 0)
        messages = self.client.stream_chat.call_args.args[0]
        self.assertEqual([m["content"] for m in messages if m["role"] == "user"], ["Two", "Three"])

    def test_failed_stream_is_closed_and_partial_turn_not_remembered(self):
        broken = Stream(ChatEvent("Partial answer"), OllamaError("stream disconnected"))
        self.client.stream_chat.side_effect = [broken, reply("Complete.")]
        status, output, errors = self.invoke(["First", "Second", "/quit"])
        self.assertEqual(status, 0)
        self.assertTrue(broken.closed)
        self.assertIn("Partial answer", output)
        self.assertIn("stream disconnected", errors)
        self.assertEqual(len(self.client.stream_chat.call_args.args[0]), 2)

    def test_interrupted_stream_is_closed_and_next_turn_has_clean_context(self):
        interrupted = Stream(ChatEvent("Partial"), KeyboardInterrupt())
        self.client.stream_chat.side_effect = [interrupted, reply("Complete.")]
        status, output, errors = self.invoke(["First", "Second", "/quit"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertTrue(interrupted.closed)
        self.assertIn("Response cancelled", output)
        self.assertEqual(len(self.client.stream_chat.call_args.args[0]), 2)

    def test_incomplete_stream_cannot_commit_a_turn(self):
        broken = Stream(ChatEvent("Not done"))
        self.client.stream_chat.side_effect = [broken, reply("Done.")]
        status, _output, errors = self.invoke(["First", "Second", "/quit"])
        self.assertEqual(status, 0)
        self.assertIn("before completion", errors)
        self.assertEqual(len(self.client.stream_chat.call_args.args[0]), 2)
        self.assertTrue(broken.closed)

    def test_raw_reasoning_channel_is_rejected_without_display_or_history(self):
        self.client.stream_chat.side_effect = [
            Stream(ChatEvent("<thi"), ChatEvent("nk>private chain"), ChatEvent("</think>Hi."), ChatEvent(done=True)),
            reply("Next."),
        ]
        with patch("aqua.chat.perf_counter", side_effect=[10, 11, 20, 21, 22]):
            status, output, errors = self.invoke(["First", "Second", "/quit"], diagnostics=True)
        self.assertEqual(status, 0)
        self.assertNotIn("private chain", output)
        self.assertNotIn("<think", output)
        self.assertIn("reasoning marker", errors)
        self.assertIn("first visible token 1.000s; complete response 2.000s", output)
        messages = self.client.stream_chat.call_args.args[0]
        self.assertEqual(len(messages), 2)

    def test_empty_reply_has_no_visible_ttft_and_is_not_committed(self):
        self.client.stream_chat.side_effect = [Stream(ChatEvent(done=True)), reply()]
        with patch("aqua.chat.perf_counter", side_effect=[10, 12, 20, 21, 22]):
            status, output, _errors = self.invoke(["First", "Second", "/quit"], diagnostics=True)
        self.assertEqual(status, 0)
        self.assertIn("first visible token n/a", output)
        self.assertEqual(len(self.client.stream_chat.call_args.args[0]), 2)

    def test_literal_think_tags_in_legitimate_answers_are_preserved_and_remembered(self):
        answer = "Use `<think>literal</think>` as the example markup."
        self.client.stream_chat.side_effect = [reply(answer), reply("Next.")]
        status, output, errors = self.invoke(["Explain this tag", "Next question", "/quit"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn(answer, output)
        self.assertEqual(self.client.stream_chat.call_args.args[0][-2]["content"], answer)

    def test_preflight_failure_prevents_input_and_generation(self):
        self.client.check_model.side_effect = OllamaError("model missing")
        with patch("builtins.input") as read, redirect_stderr(StringIO()):
            status = chat.run_text_chat()
        self.assertEqual(status, 1)
        read.assert_not_called()
        self.client.stream_chat.assert_not_called()

    def test_eof_and_prompt_interrupt_exit_cleanly(self):
        for ending in (EOFError(), KeyboardInterrupt()):
            with self.subTest(ending=type(ending).__name__):
                status, output, errors = self.invoke([ending])
                self.assertEqual(status, 0)
                self.assertEqual(errors, "")
                self.assertIn("Chat ended", output)

    def test_configuration_reaches_only_local_client_and_generation(self):
        settings = GenerationSettings(temperature=0.1, num_predict=32, num_ctx=2048)
        self.client.check_model.return_value = ModelInfo("other:local", "test")
        status, _output, _errors = self.invoke(["Hello", "/quit"], model="other:local", settings=settings, timeout=7)
        self.assertEqual(status, 0)
        self.factory.assert_called_once_with(timeout=7)
        self.client.check_model.assert_called_once_with("other:local")
        self.assertEqual(self.client.stream_chat.call_args.kwargs, {"model": "other:local", "settings": settings, "tools": tool_definitions()})

    def test_oversized_user_message_is_skipped_before_stream_request(self):
        status, _output, errors = self.invoke(["a" * 13000, "/quit"])
        self.assertEqual(status, 0)
        self.assertIn("character limit", errors)
        self.client.stream_chat.assert_not_called()

    def test_legacy_thinking_notice_and_longer_default_timeout(self):
        self.client.check_model.return_value = SimpleNamespace(
            name="qwen3:4b", version="local-test", thinking_control="hidden",
        )
        status, output, errors = self.invoke(["/quit"], model="qwen3:4b")
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.factory.assert_called_once_with(timeout=120.0)
        self.assertIn("uses hidden thinking", output)
        self.assertIn("first visible reply may take longer", output)
        self.client.check_model.assert_called_once_with("qwen3:4b")


class VoiceSessionTests(_SessionHarness):
    def setUp(self):
        super().setUp()
        self.recognizer = Mock()
        self.recognizer.transcribe.return_value = transcript()
        recognizer_patch = patch("aqua.chat.OfflineRecognizer", return_value=self.recognizer)
        self.recognizer_class = recognizer_patch.start()
        self.addCleanup(recognizer_patch.stop)
        capture_patch = patch("aqua.chat.record_test", return_value=recording())
        self.capture = capture_patch.start()
        self.addCleanup(capture_patch.stop)
        reliability_patch = patch("aqua.chat.transcript_rejection_reason", return_value=None)
        self.reliable = reliability_patch.start()
        self.addCleanup(reliability_patch.stop)

    def test_voice_loads_whisper_once_and_retains_context_across_recordings(self):
        status, output, errors = self.invoke(["", "", "/quit"], voice=True)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.recognizer_class.assert_called_once_with()
        self.assertEqual(self.capture.call_count, 2)
        self.assertEqual(self.recognizer.transcribe.call_count, 2)
        self.capture.assert_called_with(device=None, sample_rate=None)
        self.recognizer.transcribe.assert_called_with(self.capture.return_value.pcm, 100, gain_db=0.0, language=None)
        self.assertEqual(len(self.client.stream_chat.call_args.args[0]), 4)
        self.assertIn("Recognized text: Hello Aqua.", output)

    def test_ordinary_voice_conversation_preserves_formula_one_context(self):
        requests = [
            "What is Formula One?",
            "My favorite drivers are Carlos Sainz and Max Verstappen.",
            "Tell me more about them.",
        ]
        self.recognizer.transcribe.side_effect = [transcript(text) for text in requests]
        self.client.stream_chat.side_effect = [
            reply("Formula One is a motorsport championship."),
            reply("I will remember your favorite drivers."),
            reply("Carlos Sainz and Max Verstappen are Formula One drivers."),
        ]
        status, output, errors = self.invoke(["", "", "", "/quit"], voice=True)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(self.client.stream_chat.call_count, 3)
        self.assertEqual([call.args[0][-1]["content"] for call in self.client.stream_chat.call_args_list], requests)
        messages = self.client.stream_chat.call_args.args[0]
        self.assertEqual([message["content"] for message in messages if message["role"] == "user"], requests)
        self.assertEqual(len(messages), 6)
        self.assertIn("Carlos Sainz and Max Verstappen", output)

    def test_empty_and_unreliable_transcripts_never_reach_llm(self):
        self.recognizer.transcribe.side_effect = [transcript(""), transcript("Maybe hallucinated.")]
        self.reliable.side_effect = ["No speech was recognized.", "Speech confidence was too low."]
        status, output, errors = self.invoke(["", "", "/quit"], voice=True)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(self.capture.call_count, 2)
        self.assertIn("Skipped:", output)
        self.client.stream_chat.assert_not_called()

    def test_voice_controls_and_arbitrary_typed_text_do_not_capture(self):
        status, output, errors = self.invoke(["/help", "/clear", "typed message", "/unknown", "/quit"], voice=True)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.capture.assert_not_called()
        self.client.stream_chat.assert_not_called()
        self.assertIn("Press Enter to record", output)

    def test_ollama_preflight_failure_does_not_load_whisper_or_microphone(self):
        self.client.check_model.side_effect = OllamaError("service unavailable")
        status, _output, errors = self.invoke([], voice=True)
        self.assertEqual(status, 1)
        self.assertIn("service unavailable", errors)
        self.recognizer_class.assert_not_called()
        self.capture.assert_not_called()

    def test_missing_speech_model_prevents_prompt_and_capture(self):
        self.recognizer_class.side_effect = SpeechError("Small model is missing")
        status, _output, errors = self.invoke([], voice=True)
        self.assertEqual(status, 1)
        self.assertIn("Small model is missing", errors)
        self.capture.assert_not_called()

    def test_microphone_and_transcription_errors_return_to_prompt(self):
        self.capture.side_effect = [AudioError("device unplugged"), recording(), recording()]
        self.recognizer.transcribe.side_effect = [SpeechError("recognition failed"), transcript()]
        status, _output, errors = self.invoke(["", "", "", "/quit"], voice=True)
        self.assertEqual(status, 0)
        self.assertIn("device unplugged", errors)
        self.assertIn("recognition failed", errors)
        self.client.stream_chat.assert_called_once()

    def test_recording_interrupt_cancels_only_that_turn(self):
        self.capture.side_effect = [KeyboardInterrupt(), recording()]
        status, output, errors = self.invoke(["", "", "/quit"], voice=True)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("Voice turn cancelled", output)
        self.client.stream_chat.assert_called_once()

    def test_voice_settings_hints_and_separate_latencies_are_reported(self):
        with patch("aqua.chat.perf_counter", side_effect=[0, 2, 10, 15.1, 20, 21.5, 30, 30.4, 32]):
            status, output, errors = self.invoke(
                ["", "/quit"], voice=True, speech_model_dir=Path("local/small"),
                device=7, sample_rate=44100, gain_db=-3, language="en",
                hotwords="Aqua, Python", diagnostics=True,
            )
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.recognizer_class.assert_called_once_with(Path("local/small"))
        self.capture.assert_called_once_with(device=7, sample_rate=44100)
        self.recognizer.transcribe.assert_called_once_with(
            self.capture.return_value.pcm, 100, gain_db=-3, language="en", hotwords="Aqua, Python",
        )
        self.assertIn("Speech model loading: 2.000s", output)
        self.assertIn("Audio capture: 5.100s wall", output)
        self.assertIn("Speech transcription: 1.500s", output)
        self.assertIn("first visible token 0.400s; complete response 2.000s", output)
