"""The opt-in multi-step demo stays deterministic offline and cannot launch apps."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aqua.brain.ollama import ChatEvent
from tools import smoke_agent


def proposal(app):
    return [ChatEvent(done=True, tool_calls=({"function": {
        "name": "open_application", "arguments": {"app_id": app}}},))]


class AgentSmokeTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex", "socket.getaddrinfo"):
            manager = patch(target, side_effect=AssertionError("Smoke unit tests must remain offline"))
            manager.start()
            self.addCleanup(manager.stop)

    def invoke(self, function, *args, **kwargs):
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output), \
                patch("subprocess.Popen", side_effect=AssertionError("No real process in demo tests")) as process:
            status = function(*args, **kwargs)
        process.assert_not_called()
        return status, output.getvalue()

    def client(self):
        client = Mock()
        client.stream_chat.side_effect = [iter(proposal("notepad")), iter(proposal("calculator")),
                                         iter([ChatEvent("Everything succeeded."), ChatEvent(done=True)])]
        return client

    def test_requires_explicit_live_flag(self):
        with patch.object(smoke_agent, "OllamaClient") as factory:
            status, output = self.invoke(smoke_agent.main, [])
        factory.assert_not_called()
        self.assertEqual(status, 2)
        self.assertIn("No requests made", output)

    def test_success_demo_uses_production_controller_and_recording_executor(self):
        client = self.client()
        status, output = self.invoke(smoke_agent.run_demo, client)
        self.assertEqual(status, 0)
        self.assertIn("decisions=3; operations=2", output)
        self.assertIn("user, assistant, tool, assistant, tool, assistant", output)
        self.assertEqual(client.stream_chat.call_count, 3)

    def test_failure_demo_retains_failure_despite_model_success_claim(self):
        status, output = self.invoke(smoke_agent.run_demo, self.client(), fail_first=True)
        self.assertEqual(status, 0)
        self.assertIn("partial_failure", output)
        self.assertIn("1 failed", output)
        self.assertNotIn("Everything succeeded.", output)

    def test_model_stopping_early_fails_demo_without_inventing_completion(self):
        client = Mock()
        client.stream_chat.side_effect = [iter([ChatEvent("Done!"), ChatEvent(done=True)]) for _ in range(2)]
        status, output = self.invoke(smoke_agent.run_demo, client)
        self.assertEqual(status, 1)
        self.assertIn("No tool operations were performed", output)
        self.assertIn("Not attempted:", output)
        self.assertEqual(client.stream_chat.call_count, 2)

    def test_custom_vs_code_fixture_reports_actual_channels_without_seed_override(self):
        client = Mock()
        client.stream_chat.side_effect = [iter(proposal("vs code")), iter(proposal("calculator")),
                                         iter([ChatEvent("All done."), ChatEvent(done=True)])]
        status, output = self.invoke(smoke_agent.run_demo, client,
                                     request="Open VS Code and Calculator.", diagnostics=True)
        self.assertEqual(status, 0)
        self.assertIn("['vs code', 'calculator']", output)
        self.assertIn('"app_id": "vs code"', output)
        self.assertIn('answer="All done."', output)
        self.assertIn("reconsideration=False", output)
        self.assertIn("stop_reason=none", output)
        settings = client.stream_chat.call_args.kwargs["settings"]
        self.assertEqual(settings.temperature, 0.2)
        self.assertIsNone(settings.seed)

    def test_repeats_use_fresh_conversations_and_one_preflight(self):
        client = Mock()
        client.check_model.return_value = SimpleNamespace(version="test", name="test-model")
        responses = []
        for _ in range(2):
            responses.extend((iter(proposal("vs code")), iter(proposal("calculator")),
                              iter([ChatEvent("Done."), ChatEvent(done=True)])))
        client.stream_chat.side_effect = responses
        with patch.object(smoke_agent, "OllamaClient", return_value=client):
            status, output = self.invoke(smoke_agent.main,
                                         ["--live", "--request", "Open VS Code and Calculator.", "--repeat", "2"])
        self.assertEqual(status, 0)
        self.assertIn("Completed 2/2 clean-session tasks (100.0%)", output)
        client.check_model.assert_called_once()
        for index in (0, 3):
            messages = client.stream_chat.call_args_list[index].args[0]
            self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertNotEqual(client.stream_chat.call_args_list[0].args[0],
                            client.stream_chat.call_args_list[1].args[0])

    def test_diagnostics_mark_review_and_print_its_actual_call(self):
        client = Mock()
        client.stream_chat.side_effect = [iter(proposal("vs code")),
                                         iter([ChatEvent("Would you like Calculator now?"), ChatEvent(done=True)]),
                                         iter(proposal("calculator")),
                                         iter([ChatEvent("Done."), ChatEvent(done=True)])]
        status, output = self.invoke(smoke_agent.run_demo, client,
                                     request="Open VS Code and Calculator.", diagnostics=True)
        self.assertEqual(status, 0)
        self.assertIn("Decision 3: reconsideration=True", output)
        self.assertIn('"app_id": "calculator"', output)
        self.assertIn("decisions=4; operations=2", output)

    def test_repeat_bounds_fail_before_constructing_client(self):
        with patch.object(smoke_agent, "OllamaClient") as factory:
            for value in ("0", "21", "x"):
                with self.subTest(value=value), redirect_stdout(StringIO()), redirect_stderr(StringIO()), \
                        self.assertRaises(SystemExit) as raised:
                    smoke_agent.main(["--live", "--repeat", value])
                self.assertEqual(raised.exception.code, 2)
        factory.assert_not_called()

    def test_diagnostics_reject_raw_reasoning_before_printing_or_execution(self):
        client = Mock()
        client.stream_chat.return_value = iter([ChatEvent("<think>private reasoning"), ChatEvent(done=True)])
        status, output = self.invoke(smoke_agent.run_demo, client, diagnostics=True)
        self.assertEqual(status, 1)
        self.assertNotIn("private reasoning", output)
        self.assertNotIn("<think>", output)
        self.assertIn("Stub execution order: []", output)


if __name__ == "__main__":
    unittest.main()
