"""Thinking/sampling CLI controls never require microphones or local inference."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import Mock, patch

from aqua import chat, cli
from aqua.brain.ollama import GenerationSettings, OllamaError


class IntelligenceCliTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("CLI tests must stay offline."))
            manager.start()
            self.addCleanup(manager.stop)

    def invoke(self, arguments):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return cli.main(arguments)

    def test_thinking_temperature_seed_reach_all_conversation_modes(self):
        for command, target in (("chat", "run_text_chat"), ("voice-chat", "run_voice_chat"), ("handsfree", "run_handsfree")):
            for mode in ("auto", "on", "off"):
                with self.subTest(command=command, mode=mode), patch.object(cli, target, return_value=0) as run:
                    self.assertEqual(self.invoke([command, "--thinking", mode, "--temperature", "0.2", "--seed", "42"]), 0)
                    settings = run.call_args.kwargs["settings"]
                    self.assertEqual(settings.thinking, mode)
                    self.assertEqual(settings.temperature, .2)
                    self.assertEqual(settings.seed, 42)

    def test_default_configuration_is_preserved(self):
        with patch.object(cli, "run_text_chat", return_value=0) as run:
            self.assertEqual(self.invoke(["chat"]), 0)
        settings = run.call_args.kwargs["settings"]
        self.assertEqual(settings.thinking, "auto")
        self.assertIsNone(settings.seed)
        self.assertEqual(settings.temperature, .2)
        self.assertEqual(settings.num_ctx, 4096)
        self.assertEqual(settings.num_predict, 1024)
        for name in ("top_p", "top_k", "presence_penalty", "repeat_penalty"):
            self.assertIsNone(getattr(settings, name))

    def test_complete_benchmark_sampling_preset_can_be_used_interactively(self):
        for command, target in (("chat", "run_text_chat"), ("voice-chat", "run_voice_chat"), ("handsfree", "run_handsfree")):
            with self.subTest(command=command), patch.object(cli, target, return_value=0) as run:
                self.assertEqual(self.invoke([command, "--top-p", ".95", "--top-k", "40", "--presence-penalty", "0", "--repeat-penalty", "1"]), 0)
            settings = run.call_args.kwargs["settings"]
            self.assertEqual((settings.top_p, settings.top_k, settings.presence_penalty, settings.repeat_penalty), (.95, 40, 0, 1))

    def test_invalid_optional_sampling_prevents_conversation_start(self):
        for options in (["--top-p", "nan"], ["--top-p", "1.1"], ["--top-k", "-1"], ["--presence-penalty", "3"], ["--repeat-penalty", "inf"]):
            with self.subTest(options=options), patch.object(cli, "run_text_chat") as run, self.assertRaises(SystemExit):
                self.invoke(["chat", *options])
            run.assert_not_called()

    def test_invalid_thinking_or_seed_prevents_chat_start(self):
        with patch.object(cli, "run_text_chat") as run:
            for options in (["--thinking", "sometimes"], ["--seed", "1.5"], ["--seed", "nan"]):
                with self.subTest(options=options), self.assertRaises(SystemExit):
                    self.invoke(["chat", *options])
        run.assert_not_called()

    def test_unsupported_explicit_mode_fails_before_whisper_or_microphone(self):
        client = Mock()
        client.validate_thinking.side_effect = OllamaError("The requested thinking mode is unsupported.")
        with patch.object(chat, "OllamaClient", return_value=client), \
                patch.object(chat, "OfflineRecognizer") as recognizer, \
                patch.object(chat, "record_test") as microphone, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            status = chat.run_voice_chat(settings=GenerationSettings(thinking="on"))
        self.assertEqual(status, 1)
        client.validate_thinking.assert_called_once()
        recognizer.assert_not_called()
        microphone.assert_not_called()

    def test_benchmark_cli_routes_without_starting_conversation_or_microphone(self):
        with patch("aqua.benchmark.runner.run", return_value=0) as run, \
                patch.object(cli, "run_text_chat") as conversation, \
                patch.object(cli, "record_test") as microphone:
            self.assertEqual(self.invoke(["benchmark", "--live", "--pilot", "--configs", "A", "C"]), 0)
        self.assertTrue(run.call_args.args[0].live)
        self.assertTrue(run.call_args.args[0].pilot)
        self.assertEqual(run.call_args.args[0].configs, ["A", "C"])
        conversation.assert_not_called()
        microphone.assert_not_called()

    def test_benchmark_setup_failure_is_a_helpful_nonzero_exit(self):
        output = StringIO()
        with patch("aqua.benchmark.runner.run", side_effect=ValueError("Resume refused: dataset hash differs")), \
                redirect_stdout(StringIO()), redirect_stderr(output):
            self.assertEqual(cli.main(["benchmark", "--live"]), 2)
        self.assertIn("Benchmark setup/report error", output.getvalue())
        self.assertIn("dataset hash differs", output.getvalue())


if __name__ == "__main__":
    unittest.main()
