"""One model/tool path replaces the old independent action router."""

from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import Mock, patch

from aqua.actions import tools
from aqua.actions.launcher import ActionResult
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import ChatEvent, GenerationSettings
from aqua.chat import _respond_or_launch


def proposal(app="notepad"):
    return iter([ChatEvent(done=True, tool_calls=({"function": {"name": tools.TOOL_NAME, "arguments": {"app_id": app}}},))])


def reply(text="A useful answer."):
    return iter([ChatEvent(text), ChatEvent(done=True)])


class OfflineBoundary(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.getaddrinfo", "socket.socket.connect", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Unit routing tests must not perform external I/O."))
            manager.start()
            self.addCleanup(manager.stop)


class TestActionRouter(OfflineBoundary):
    @patch("aqua.actions.tools.open_application")
    def test_approved_action(self, launcher):
        launcher.return_value = ActionResult(True, "Opening notepad.")
        authorization = tools.authorize_request("Hey Aqua, open Notepad", "notepad")
        self.assertEqual(authorization.status, "approved")
        result = tools.execute_application("notepad")
        self.assertTrue(result.success)
        self.assertFalse(result.readiness_checked)
        launcher.assert_called_once_with("notepad")

    @patch("aqua.actions.tools.open_application")
    def test_reject_unapproved_action(self, launcher):
        unapproved = {"function": {"name": tools.TOOL_NAME, "arguments": {"app_id": "powershell"}}}
        with self.assertRaises(tools.ToolValidationError):
            tools.validate_tool_call(unapproved)
        self.assertEqual(tools.authorize_request("Open PowerShell", "chrome").status, "denied")
        launcher.assert_not_called()

    @patch("aqua.actions.tools.open_application")
    def test_reject_compound_command(self, launcher):
        authorization = tools.authorize_request("Open Notepad and delete my files", "notepad")
        self.assertEqual(authorization.status, "denied")
        launcher.assert_not_called()

    @patch("aqua.actions.tools.execute_application")
    def test_normal_conversation(self, execute):
        client, conversation = Mock(), Conversation()
        client.stream_chat.return_value = reply("Formula One is a motorsport.")
        with redirect_stdout(StringIO()):
            _respond_or_launch(client, conversation, "What is Formula One?", model="test-model", settings=None, diagnostics=False)
        execute.assert_not_called()
        client.stream_chat.assert_called_once()
        self.assertEqual(conversation.history[-1]["content"], "Formula One is a motorsport.")


class TestVoiceRouting(OfflineBoundary):
    @patch("aqua.actions.tools.open_application", return_value=ActionResult(True, "fixed launch success"))
    def test_chat_wrapper_preserves_one_user_turn_across_multiple_tools(self, launcher):
        client, conversation = Mock(), Conversation()
        client.stream_chat.side_effect = [proposal("notepad"), proposal("calculator"),
                                          reply("All applications are ready!")]
        output = StringIO()
        with redirect_stdout(output):
            _respond_or_launch(client, conversation, "Open Notepad and Calculator.",
                               model="test-model", settings=None, diagnostics=True)
        self.assertEqual([item.args[0] for item in launcher.call_args_list], ["notepad", "calculator"])
        self.assertEqual(client.stream_chat.call_count, 3)
        self.assertEqual(conversation.turn_count, 1)
        self.assertEqual(conversation.last_task.status, "completed")
        self.assertEqual(conversation.last_task.attempted_apps, ("notepad", "calculator"))
        self.assertEqual([row["role"] for row in conversation.history],
                         ["user", "assistant", "tool", "assistant", "tool", "assistant"])
        self.assertIn("2 successful launch attempt(s)", output.getvalue())
        self.assertNotIn("All applications are ready!", output.getvalue())
        self.assertIn("Task: model decisions 3; tool operations 2; completed 2; failed 0; unknown 0; status completed", output.getvalue())

    @patch("aqua.actions.tools.open_application", return_value=ActionResult(True, "Opening notepad."))
    def test_action_uses_native_proposal_and_remembers_verified_result(self, launcher):
        client, conversation = Mock(), Conversation()
        client.stream_chat.side_effect = [proposal(), reply("The Notepad launch attempt succeeded.")]
        with redirect_stdout(StringIO()):
            _respond_or_launch(client, conversation, "Open Notepad", model="test-model", settings=None, diagnostics=False)
        launcher.assert_called_once_with("notepad")
        self.assertEqual(client.stream_chat.call_count, 2)
        self.assertEqual(client.stream_chat.call_args_list[0].kwargs["tools"], tools.tool_definitions())
        self.assertEqual(client.stream_chat.call_args.kwargs["tools"], [])
        tool_message = client.stream_chat.call_args.args[0][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(json.loads(tool_message["content"])["app_id"], "notepad")
        self.assertTrue(conversation.verified_results[-1]["process_created"])
        self.assertFalse(conversation.verified_results[-1]["readiness_checked"])

    @patch("aqua.actions.tools.execute_application")
    def test_conversation_reaches_same_model_once_without_a_classifier(self, execute):
        client, conversation = Mock(), Conversation()
        client.stream_chat.return_value = reply("Formula One is a motorsport.")
        settings = GenerationSettings()
        with redirect_stdout(StringIO()):
            _respond_or_launch(client, conversation, "What is Formula One?", model="test-model", settings=settings, diagnostics=False)
        client.stream_chat.assert_called_once()
        self.assertIs(client.stream_chat.call_args.kwargs["settings"], settings)
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
