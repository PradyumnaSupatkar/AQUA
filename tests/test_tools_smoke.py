"""The native tool smoke helper is opt-in and cannot launch applications."""

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO, TextIOWrapper
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aqua.brain.controller import ConversationController
from aqua.brain.ollama import ChatEvent, DEFAULT_CHAT_MODEL, OllamaError
from tools import smoke_tools


def reply(text):
    return [ChatEvent(text), ChatEvent(done=True)]


def proposal(app):
    return [ChatEvent(done=True, tool_calls=({"function": {
        "name": "open_application", "arguments": {"app_id": app},
    }},))]


class ToolsSmokeTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Smoke unit tests must stay offline and launch nothing."))
            mock = manager.start()
            self.addCleanup(manager.stop)
            if target == "subprocess.Popen":
                self.process = mock

    def invoke(self, function, *args, **kwargs):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = function(*args, **kwargs)
        self.process.assert_not_called()
        return status, output.getvalue(), errors.getvalue()

    @staticmethod
    def client_for(responses):
        client = Mock()
        pending = iter(responses)
        client.stream_chat.side_effect = lambda *_args, **_kwargs: iter(next(pending))
        client.check_model.return_value = SimpleNamespace(name=DEFAULT_CHAT_MODEL, version="test")
        return client

    def test_no_live_flag_never_constructs_model_client(self):
        with patch.object(smoke_tools, "OllamaClient") as factory:
            status, output, errors = self.invoke(smoke_tools.main, [])
        self.assertEqual(status, 2)
        self.assertIn("--live", output)
        self.assertIn("No requests made", errors)
        factory.assert_not_called()

    def test_all_fixed_scenarios_use_real_controller_with_recording_stub(self):
        responses = []
        for case in smoke_tools.CASES:
            if case.outcome == "clarification":
                responses.append(proposal("vs code"))
            elif case.app is not None:
                if case.requests == 2 or case.outcome == "launch_failed":
                    responses.append(proposal(case.app))
                if case.outcome != "launch_failed":
                    responses.append(reply("The launch attempt succeeded; readiness was not checked."))
            elif case.requests:
                if case.capabilities:
                    content = "I can open " + ", ".join(smoke_tools.ALLOWED_APPS) + "."
                elif case.failed_reply:
                    content = "The launch attempt for Chrome failed."
                elif case.mentions:
                    content = "I attempted to launch Notepad."
                else:
                    content = "No application action will be taken."
                responses.append(reply(content))
        client = self.client_for(responses)
        status, output, errors = self.invoke(smoke_tools.run_cases, client, diagnostics=True)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn(f"{len(smoke_tools.CASES)}/{len(smoke_tools.CASES)} turns matched", output)
        self.assertIn("outcome=clarification; requests=1", output)
        self.assertIn("outcome=launch_attempt_succeeded; requests=2", output)
        self.assertIn("outcome=launch_attempt_succeeded; requests=1", output)
        self.assertIn("outcome=cancelled_confirmation; requests=0", output)
        self.assertIn("outcome=stale_confirmation; requests=0", output)
        self.assertIn("outcome=launch_failed; requests=1", output)
        self.assertIn("endpoint=http://127.0.0.1:11434/api/chat", output)
        self.assertIn("no Windows applications were launched", output)
        self.assertEqual(client.stream_chat.call_count, sum(case.requests for case in smoke_tools.CASES))

    def test_forced_unsafe_proposal_is_reported_even_when_policy_blocks_it(self):
        case = smoke_tools.Case("negative", "Don't open Chrome.", "conversation", 1)
        client = self.client_for([proposal("chrome")])
        with patch.object(smoke_tools, "CASES", (case,)):
            status, output, errors = self.invoke(smoke_tools.run_cases, client)
        self.assertEqual(status, 1)
        self.assertEqual(errors, "")
        self.assertIn("FAIL outcome=denied", output)
        self.assertIn("stub calls=[]", output)
        self.assertIn("expected outcome=conversation", output)

    def test_stubbed_launch_failure_is_retained_for_followup(self):
        cases = (smoke_tools.Case("failure", "Open Chrome.", "launch_failed", 1, app="chrome", failed_reply=True),
                 smoke_tools.Case("failure", "Did you open Chrome?", "conversation", 1, mentions="chrome", failed_reply=True))
        client = self.client_for([proposal("chrome"), reply("I could not open Chrome; the launch failed.")])
        controllers = []

        def factory(*args, **kwargs):
            controller = ConversationController(*args, **kwargs)
            controllers.append(controller)
            return controller

        with patch.object(smoke_tools, "CASES", cases), patch.object(smoke_tools, "ConversationController", side_effect=factory):
            status, output, errors = self.invoke(smoke_tools.run_cases, client)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(len(controllers), 1)
        self.assertEqual(controllers[0].conversation.verified_results[-1]["status"], "launch_failed")
        self.assertFalse(controllers[0].conversation.verified_results[-1]["process_created"])
        self.assertIn("2/2 turns matched", output)

    def test_failure_check_rejects_a_contradictory_model_explanation(self):
        case = smoke_tools.Case("failure", "Open Chrome.", "launch_failed", 1, app="chrome", failed_reply=True)
        result = SimpleNamespace(ok=True, outcome="launch_failed", requests=1,
                                 text="Tool result: the launch failed. launch attempt succeeded")
        reasons = smoke_tools._check(case, result, ["chrome"], None)
        self.assertIn("reply contradicted the verified launch failure", reasons)

    def test_live_flag_preflights_once_and_keeps_existing_default_model(self):
        case = smoke_tools.Case("chat", "How are you?", "conversation", 1)
        client = self.client_for([reply("I'm well.")])
        with patch.object(smoke_tools, "CASES", (case,)), patch.object(smoke_tools, "OllamaClient", return_value=client):
            status, output, errors = self.invoke(smoke_tools.main, ["--live"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        client.check_model.assert_called_once_with(DEFAULT_CHAT_MODEL)
        self.assertIn("metadata excluded from per-turn", output)
        self.assertEqual(client.stream_chat.call_args.kwargs["model"], DEFAULT_CHAT_MODEL)

    def test_model_setup_error_does_not_echo_private_server_content(self):
        client = Mock()
        client.check_model.side_effect = OllamaError("PRIVATE SERVER CONTENT")
        with patch.object(smoke_tools, "OllamaClient", return_value=client):
            status, _, errors = self.invoke(smoke_tools.main, ["--live"])
        self.assertEqual(status, 1)
        self.assertIn("OllamaError", errors)
        self.assertNotIn("PRIVATE", errors)
        client.stream_chat.assert_not_called()

    def test_windows_cp1252_output_is_reconfigured_before_unicode_model_reply(self):
        raw = BytesIO()
        output = TextIOWrapper(raw, encoding="cp1252", newline="")
        case = smoke_tools.Case("chat", "How are you?", "conversation", 1)
        client = self.client_for([reply("Hello 🚀 — नमस्ते")])
        with patch.object(smoke_tools, "CASES", (case,)), patch.object(smoke_tools, "OllamaClient", return_value=client), \
                redirect_stdout(output), redirect_stderr(StringIO()):
            status = smoke_tools.main(["--live"])
        output.flush()
        self.assertEqual(status, 0)
        self.assertIn("Hello 🚀 — नमस्ते", raw.getvalue().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
