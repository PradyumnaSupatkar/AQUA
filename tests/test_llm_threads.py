"""--llm-threads: Ollama CPU threads, 4 while speaking, Ollama's default otherwise."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import Mock, patch

from aqua import chat, cli
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import ChatEvent, GenerationSettings, OllamaClient

from test_ollama import Connection, Response, stream


class LlmThreadsCliTests(unittest.TestCase):
    def options(self, arguments, target):
        with patch(f"aqua.cli.{target}", return_value=0) as run, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(cli.main(arguments), 0)
        return run.call_args.kwargs

    def test_auto_uses_four_only_while_speaking(self):
        self.assertEqual(cli.SPEAKING_LLM_THREADS, 4)
        cases = (
            (["handsfree"], "run_handsfree", 4),
            (["handsfree", "--no-speak"], "run_handsfree", None),
            (["handsfree", "--tts-engine", "none"], "run_handsfree", None),
            (["chat"], "run_text_chat", None),
            (["chat", "--speak"], "run_text_chat", 4),
            (["voice-chat"], "run_voice_chat", None),
            (["voice-chat", "--speak"], "run_voice_chat", 4),
        )
        for arguments, target, expected in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(self.options(arguments, target)["llm_threads"], expected)

    def test_explicit_value_wins_and_invalid_values_fail(self):
        self.assertEqual(self.options(["chat", "--llm-threads", "6"], "run_text_chat")["llm_threads"], 6)
        self.assertEqual(self.options(["handsfree", "--llm-threads", "8"], "run_handsfree")["llm_threads"], 8)
        for value in ("0", "65", "many", "-2"):
            with self.subTest(value=value), patch("aqua.cli.run_text_chat") as run, \
                    redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                cli.main(["chat", "--llm-threads", value])
            run.assert_not_called()


class OllamaNumThreadTests(unittest.TestCase):
    def request(self, client):
        connection = Connection(Response(stream({"done": True})))
        with patch("aqua.brain.ollama.HTTPConnection", return_value=connection):
            list(client.stream_chat([{"role": "user", "content": "Hi."}], model="qwen3:4b", settings=GenerationSettings()))
        return json.loads(connection.request.call_args.kwargs["body"])

    def test_request_carries_num_thread_only_when_set(self):
        self.assertEqual(self.request(OllamaClient(num_thread=4))["options"]["num_thread"], 4)
        self.assertNotIn("num_thread", self.request(OllamaClient())["options"])

    def test_invalid_thread_counts_are_rejected(self):
        for value in (0, 65, 2.5, "4", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                OllamaClient(num_thread=value)


class SessionTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.getaddrinfo", "socket.socket.connect", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("must stay offline"))
            manager.start()
            self.addCleanup(manager.stop)

    def test_start_passes_threads_only_when_chosen(self):
        client = Mock()
        client.check_model.return_value = Mock(version="t", name="m", thinking_control="non_thinking")
        with patch("aqua.chat.OllamaClient", return_value=client) as factory, redirect_stdout(StringIO()):
            chat._start(model="m", timeout=9, max_turns=2, max_chars=4000, settings=None, llm_threads=4)
            chat._start(model="m", timeout=9, max_turns=2, max_chars=4000, settings=None)
        self.assertEqual(factory.call_args_list[0].kwargs, {"timeout": 9, "keep_alive": "5m", "num_thread": 4})
        self.assertEqual(factory.call_args_list[1].kwargs, {"timeout": 9, "keep_alive": "5m"})

    def test_diagnostics_show_ollama_threads(self):
        client = Mock()
        client.keep_alive, client.num_thread = "30m", 4
        client.stream_chat.side_effect = lambda *_a, **_k: iter([ChatEvent("Hi."), ChatEvent(done=True)])
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(StringIO()):
            chat._respond(client, Conversation(), "Hello", model="m", settings=GenerationSettings(), diagnostics=True)
        self.assertIn("Ollama CPU threads: 4", output.getvalue())


if __name__ == "__main__":
    unittest.main()
