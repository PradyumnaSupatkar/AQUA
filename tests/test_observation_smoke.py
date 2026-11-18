"""Explicit observation smoke uses fake UIA/model data in automated tests."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import Mock, patch

from aqua.brain.conversation import Conversation
from aqua.brain.ollama import ChatEvent
from tools import smoke_observation


WINDOW = "w_0123456789abcdef_1"


def proposal(name, arguments=None):
    return iter([ChatEvent(done=True, tool_calls=({"function": {
        "name": name, "arguments": arguments or {}}},))])


def answer(value):
    return iter([ChatEvent(json.dumps(value)), ChatEvent(done=True)])


class Observer:
    def __init__(self, *, empty=False):
        self.scope = ()
        self.window_ids = ()
        self.empty = empty
        self.list_windows = Mock(side_effect=self.list_result)
        self.inspect_window = Mock(side_effect=self.inspect_result)

    def begin_task(self, scope):
        self.scope = tuple(scope)

    def list_result(self):
        windows = [] if self.empty else [{"window_id": WINDOW, "app_id": "vs code", "title": "Fixture", "active": True}]
        self.window_ids = () if self.empty else (WINDOW,)
        return {"tool": "list_windows", "status": "empty" if self.empty else "ok", "untrusted_data": True,
                "elapsed_seconds": .01, "data": {"windows": windows}}

    def inspect_result(self, _window_id):
        return {"tool": "inspect_window", "status": "ok", "untrusted_data": True,
                "elapsed_seconds": .02, "data": {"window_id": WINDOW, "app_id": "vs code",
                "controls": [{"control_id": "c0", "control_type": "Button", "name": "Save",
                              "enabled": True, "visible": True}]}}


class ObservationSmokeTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Smoke unit tests must remain offline and read nothing"))
            mocked = manager.start()
            self.addCleanup(manager.stop)
            if target == "subprocess.Popen":
                self.process = mocked

    def invoke(self, function, *args, **kwargs):
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            status = function(*args, **kwargs)
        self.process.assert_not_called()
        return status, output.getvalue()

    def session(self, *responses, empty=False):
        conversation = Conversation()
        observer = Observer(empty=empty)
        conversation.desktop_observer = observer
        client = Mock()
        client.stream_chat.side_effect = list(responses)
        return client, conversation, observer

    def test_live_and_explicit_scope_are_required_before_constructing_clients(self):
        with patch.object(smoke_observation, "OllamaClient") as factory:
            status, output = self.invoke(smoke_observation.main, ["--app", "vs code"])
            self.assertEqual(status, 2)
            self.assertIn("No requests made", output)
            for args in (["--live"], ["--live", "--app", "powershell"]):
                with self.subTest(args=args), redirect_stdout(StringIO()), redirect_stderr(StringIO()), \
                        self.assertRaises(SystemExit) as raised:
                    smoke_observation.main(args)
                self.assertEqual(raised.exception.code, 2)
        factory.assert_not_called()

    def test_real_controller_flow_prints_grounded_facts_counts_and_timings_only(self):
        client, conversation, observer = self.session(
            proposal("list_windows"), proposal("inspect_window", {"window_id": WINDOW}),
            answer({"observations": [{"window_id": WINDOW, "control_ids": ["c0"]}]}))
        status, output = self.invoke(smoke_observation.run_demo, client, "vs code", conversation)
        self.assertEqual(status, 0)
        self.assertIn("Save", output)
        self.assertIn("Operation: list_windows; status=observation_succeeded", output)
        self.assertIn("elapsed=0.010s; windows=1", output)
        self.assertIn("elapsed=0.020s; controls=1", output)
        self.assertNotIn('"controls":', output)
        self.assertIn("no application was launched or manipulated", output)
        observer.list_windows.assert_called_once_with()
        observer.inspect_window.assert_called_once_with(WINDOW)
        self.assertEqual(client.stream_chat.call_args.kwargs["settings"].temperature, .2)

    def test_empty_approved_scope_reports_limitation_without_opening_an_app(self):
        client, conversation, observer = self.session(proposal("list_windows"),
                                                     answer({"observations": []}), empty=True)
        status, output = self.invoke(smoke_observation.run_demo, client, "vs code", conversation)
        self.assertEqual(status, 1)
        self.assertIn("LIMITATION:", output)
        observer.inspect_window.assert_not_called()

    def test_model_launch_proposal_is_rejected_and_does_not_create_confirmation(self):
        client, conversation, observer = self.session(proposal("list_windows"),
                                                     proposal("open_application", {"app_id": "chrome"}))
        status, output = self.invoke(smoke_observation.run_demo, client, "vs code", conversation)
        self.assertEqual(status, 1)
        self.assertIn("LIMITATION:", output)
        self.assertIsNone(conversation.action_state.pending_app)
        observer.inspect_window.assert_not_called()

    def test_invalid_direct_scope_fails_before_any_model_or_ui_call(self):
        client, conversation, observer = self.session()
        with self.assertRaises(ValueError):
            self.invoke(smoke_observation.run_demo, client, "C:\\Windows\\notepad.exe", conversation)
        client.stream_chat.assert_not_called()
        observer.list_windows.assert_not_called()


if __name__ == "__main__":
    unittest.main()
