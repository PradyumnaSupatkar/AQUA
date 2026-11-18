"""Ollama keep-alive: strict parsing, per-mode defaults, and the request payload."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import Mock, patch

from aqua import chat, cli, handsfree
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import DEFAULT_KEEP_ALIVE, ChatEvent, GenerationSettings, OllamaClient, parse_keep_alive

from test_ollama import Connection, Response, stream


class ParseKeepAliveTests(unittest.TestCase):
    def test_accepts_digits_with_unit_or_zero(self):
        for value in ("0", "5m", "30m", "1h", "90s", "0m", "120m"):
            with self.subTest(value=value):
                self.assertEqual(parse_keep_alive(value), value)

    def test_rejects_anything_else(self):
        for value in ("", "5", "5d", "-1", "-1m", "1.5h", " 5m", "5m ", "5M", "1h30m", "m", "0x5m",
                      "1234567m", 5, None, b"5m"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_keep_alive(value)

    def test_client_validates_and_defaults(self):
        self.assertEqual(OllamaClient().keep_alive, DEFAULT_KEEP_ALIVE)
        self.assertEqual(DEFAULT_KEEP_ALIVE, "5m")
        with self.assertRaises(ValueError):
            OllamaClient(keep_alive="forever")


class RequestCarriesKeepAliveTests(unittest.TestCase):
    def test_chat_request_sends_the_configured_value(self):
        for value in ("30m", "0", "1h"):
            connection = Connection(Response(stream({"done": True})))
            with self.subTest(value=value), patch("aqua.brain.ollama.HTTPConnection", return_value=connection):
                list(OllamaClient(keep_alive=value).stream_chat(
                    [{"role": "user", "content": "Hi."}], model="qwen3:4b", settings=GenerationSettings()))
                self.assertEqual(json.loads(connection.request.call_args.kwargs["body"])["keep_alive"], value)


class KeepAliveCliTests(unittest.TestCase):
    def options(self, arguments, target):
        with patch(f"aqua.cli.{target}", return_value=0) as run, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(cli.main(arguments), 0)
        return run.call_args.kwargs

    def test_defaults_per_mode(self):
        self.assertEqual(self.options(["chat"], "run_text_chat")["keep_alive"], "5m")
        self.assertEqual(self.options(["voice-chat"], "run_voice_chat")["keep_alive"], "5m")
        self.assertEqual(self.options(["handsfree"], "run_handsfree")["keep_alive"], "30m")
        self.assertEqual(cli.HANDSFREE_KEEP_ALIVE, handsfree.HANDSFREE_KEEP_ALIVE)

    def test_explicit_value_forwards(self):
        self.assertEqual(self.options(["chat", "--keep-alive", "1h"], "run_text_chat")["keep_alive"], "1h")
        self.assertEqual(self.options(["handsfree", "--keep-alive", "0"], "run_handsfree")["keep_alive"], "0")

    def test_invalid_values_rejected_before_runner(self):
        for value in ("5", "5d", "-1", "1.5h", "forever"):
            with self.subTest(value=value), patch("aqua.cli.run_text_chat") as run, \
                    redirect_stdout(StringIO()), redirect_stderr(StringIO()) as errors, \
                    self.assertRaises(SystemExit) as exited:
                cli.main(["chat", "--keep-alive", value])
            self.assertEqual(exited.exception.code, 2)
            self.assertIn("Keep-alive", errors.getvalue())
            run.assert_not_called()


class SessionKeepAliveTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.getaddrinfo", "socket.socket.connect", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Keep-alive tests must stay offline."))
            manager.start()
            self.addCleanup(manager.stop)

    def test_text_chat_builds_its_client_with_the_session_value(self):
        client = Mock()
        client.check_model.return_value = Mock(version="t", name="m", thinking_control="none")
        with patch("aqua.chat.OllamaClient", return_value=client) as factory, \
                patch("builtins.input", side_effect=["/quit"]), redirect_stdout(StringIO()):
            self.assertEqual(chat.run_text_chat(keep_alive="45m"), 0)
        factory.assert_called_once_with(timeout=120.0, keep_alive="45m")

    def test_handsfree_runner_defaults_to_thirty_minutes(self):
        import inspect
        self.assertEqual(inspect.signature(handsfree.run_handsfree).parameters["keep_alive"].default, "30m")
        self.assertEqual(inspect.signature(chat.run_text_chat).parameters["keep_alive"].default, "5m")
        self.assertEqual(inspect.signature(chat.run_voice_chat).parameters["keep_alive"].default, "5m")

    def test_diagnostics_show_keep_alive(self):
        client = Mock()
        client.keep_alive = "30m"
        client.stream_chat.side_effect = lambda *_a, **_k: iter([ChatEvent("Hi."), ChatEvent(done=True)])
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(StringIO()):
            chat._respond(client, Conversation(), "Hello", model="m", settings=GenerationSettings(), diagnostics=True)
        self.assertIn("Ollama keep-alive: 30m", output.getvalue())
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(StringIO()):
            chat._respond(client, Conversation(), "Hello", model="m", settings=GenerationSettings(), diagnostics=False)
        self.assertNotIn("keep-alive", output.getvalue())


if __name__ == "__main__":
    unittest.main()
