"""Final conversational defaults use installed models without fallback or pulls."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aqua import chat
from aqua.audio.levels import AudioLevels
from aqua.audio.recording import Recording
from aqua.brain.ollama import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONTEXT_SIZE,
    DEFAULT_KEEP_ALIVE,
    DEFAULT_TEMPERATURE,
    ChatEvent,
    GenerationSettings,
    ModelInfo,
    OllamaClient,
)
from aqua.cli import main
from aqua.speech.models import DEFAULT_MODEL_DIR, MODEL_DIRS
from aqua.speech.recognizer import Transcript


class FinalizedConversationDefaultsTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.getaddrinfo", "socket.socket.connect"):
            network_patch = patch(target, side_effect=AssertionError("Default-configuration tests must not access the network"))
            network_patch.start()
            self.addCleanup(network_patch.stop)

    def test_finalized_configuration_keeps_requested_inference_settings(self):
        self.assertEqual(DEFAULT_CHAT_MODEL, "qwen3:4b-instruct-2507-q4_K_M")
        self.assertEqual(DEFAULT_CONTEXT_SIZE, 4096)
        self.assertEqual(DEFAULT_TEMPERATURE, .2)
        self.assertEqual(DEFAULT_KEEP_ALIVE, "5m")
        self.assertEqual(GenerationSettings().num_ctx, 4096)
        self.assertEqual(GenerationSettings().temperature, .2)
        self.assertEqual(DEFAULT_MODEL_DIR, MODEL_DIRS["small"])

    def test_both_cli_modes_still_allow_explicit_thinking_model(self):
        for command, function in (("chat", "run_text_chat"), ("voice-chat", "run_voice_chat")):
            with self.subTest(command=command), patch(f"aqua.cli.{function}", return_value=0) as run:
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    status = main([command, "--model", "qwen3:4b"])
                self.assertEqual(status, 0)
                self.assertEqual(run.call_args.kwargs["model"], "qwen3:4b")
                self.assertEqual(DEFAULT_CHAT_MODEL, "qwen3:4b-instruct-2507-q4_K_M")

    def test_direct_text_session_uses_instruct_and_retains_completed_turns(self):
        client = Mock()
        client.check_model.return_value = ModelInfo(DEFAULT_CHAT_MODEL, "test", "qwen3", "non_thinking")
        client.stream_chat.side_effect = lambda *_args, **_kwargs: iter([ChatEvent("Noted."), ChatEvent(done=True)])
        with patch("aqua.chat.OllamaClient", return_value=client), patch(
            "builtins.input", side_effect=["Remember teal.", "Which color?", "/quit"]
        ), redirect_stdout(StringIO()) as output, redirect_stderr(StringIO()) as errors:
            self.assertEqual(chat.run_text_chat(), 0)
        client.check_model.assert_called_once_with(DEFAULT_CHAT_MODEL)
        self.assertEqual(client.stream_chat.call_count, 2)
        self.assertEqual(client.stream_chat.call_args.kwargs["model"], DEFAULT_CHAT_MODEL)
        self.assertEqual(len(client.stream_chat.call_args.args[0]), 4)
        self.assertNotIn("uses hidden thinking", output.getvalue())
        self.assertEqual(errors.getvalue(), "")

    def test_direct_voice_session_uses_instruct_and_reuses_small_without_gain(self):
        client = Mock()
        client.check_model.return_value = ModelInfo(DEFAULT_CHAT_MODEL, "test", "qwen3", "non_thinking")
        client.stream_chat.side_effect = lambda *_args, **_kwargs: iter([ChatEvent("Hello."), ChatEvent(done=True)])
        recording = Recording(bytes(1000), 100, AudioLevels(.1, .05, 0), 0)
        recognizer = Mock()
        recognizer.transcribe.return_value = Transcript("Hello Aqua.", "en", .95, 1, -20, -30, 0)
        with patch("aqua.chat.OllamaClient", return_value=client), patch(
            "aqua.chat.OfflineRecognizer", return_value=recognizer
        ) as load, patch("aqua.chat.record_test", return_value=recording) as capture, patch(
            "aqua.chat.transcript_rejection_reason", return_value=None
        ), patch("builtins.input", side_effect=["", "", "/quit"]), redirect_stdout(StringIO()), redirect_stderr(StringIO()) as errors:
            self.assertEqual(chat.run_voice_chat(), 0)
        client.check_model.assert_called_once_with(DEFAULT_CHAT_MODEL)
        load.assert_called_once_with()
        self.assertEqual(capture.call_count, 2)
        capture.assert_called_with(device=None, sample_rate=None)
        recognizer.transcribe.assert_called_with(recording.pcm, 100, gain_db=0.0, language=None)
        self.assertEqual(client.stream_chat.call_args.kwargs["model"], DEFAULT_CHAT_MODEL)
        self.assertEqual(len(client.stream_chat.call_args.args[0]), 4)
        self.assertEqual(errors.getvalue(), "")

    def test_missing_default_cannot_fall_back_to_installed_thinking_or_open_microphone(self):
        tags = {"models": [{"name": "qwen3:4b", "details": {"family": "qwen3"}}]}
        before = deepcopy(tags)
        for command in ("chat", "voice-chat"):
            with self.subTest(command=command), patch(
                "aqua.brain.ollama.OllamaClient._json", side_effect=[{"version": "test"}, tags]
            ) as requests, patch("aqua.brain.ollama.HTTPConnection") as connection, patch(
                "aqua.chat.OfflineRecognizer"
            ) as speech, patch("aqua.chat.record_test") as capture, patch("builtins.input") as prompt, redirect_stdout(StringIO()), redirect_stderr(StringIO()) as errors:
                self.assertEqual(main([command]), 1)
            self.assertIn(DEFAULT_CHAT_MODEL, errors.getvalue())
            self.assertIn("not installed", errors.getvalue())
            self.assertIn("never downloads", errors.getvalue())
            self.assertEqual([call.args[0] for call in requests.call_args_list], ["/api/version", "/api/tags"])
            connection.assert_not_called()
            speech.assert_not_called()
            capture.assert_not_called()
            prompt.assert_not_called()
        self.assertEqual(tags, before)

    def test_default_request_keeps_context_temperature_and_five_minute_residency(self):
        response = SimpleNamespace(
            status=200,
            readline=Mock(return_value=json.dumps({"message": {"content": "Hello."}, "done": True}).encode() + b"\n"),
            close=Mock(),
        )
        connection = Mock(sock=None)
        connection.getresponse.return_value = response
        with patch("aqua.brain.ollama.OllamaClient._connection", return_value=connection):
            events = list(OllamaClient().stream_chat(
                [{"role": "user", "content": "Hello"}], model=DEFAULT_CHAT_MODEL, settings=GenerationSettings()
            ))
        self.assertTrue(events[-1].done)
        payload = json.loads(connection.request.call_args.kwargs["body"])
        self.assertEqual(payload["model"], DEFAULT_CHAT_MODEL)
        self.assertEqual(payload["keep_alive"], "5m")
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertEqual(payload["options"]["temperature"], .2)
        self.assertTrue(response.close.called and connection.close.called)


if __name__ == "__main__":
    unittest.main()
