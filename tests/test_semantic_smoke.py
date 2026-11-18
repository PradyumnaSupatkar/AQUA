"""The optional live smoke tool stays opt-in and has no application effects."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from aqua.actions.semantic import Decision, IntentError
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, DEFAULT_CONTEXT_SIZE
from tools import smoke_semantic


class SemanticSmokeTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo"):
            manager = patch(target, side_effect=AssertionError("Unit tests must not contact Ollama."))
            manager.start()
            self.addCleanup(manager.stop)

    def invoke(self, args, decisions):
        output, errors = StringIO(), StringIO()
        ticks = iter(value for number in range(7) for value in (number, number + .1))
        with patch.object(smoke_semantic.semantic, "interpret_request", side_effect=decisions) as interpret, \
                patch("aqua.actions.launcher.subprocess.Popen", side_effect=AssertionError("No real applications in unit tests.")) as launch, \
                patch.object(smoke_semantic, "perf_counter", side_effect=lambda: next(ticks)), \
                redirect_stdout(output), redirect_stderr(errors):
            status = smoke_semantic.main(args)
        launch.assert_not_called()
        return status, output.getvalue(), errors.getvalue(), interpret

    def expected(self):
        return [case.decisions[0] for case in smoke_semantic.CASES]

    def test_no_live_flag_never_calls_classifier(self):
        status, output, errors, interpret = self.invoke([], [])
        self.assertEqual(status, 2)
        self.assertIn("--live", output)
        self.assertIn("No requests made", errors)
        interpret.assert_not_called()

    def test_seven_mocked_cases_replay_real_policy_without_launching(self):
        status, output, errors, interpret = self.invoke(["--live"], self.expected())
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(interpret.call_count, 7)
        self.assertIn("7/7 cases matched", output)
        self.assertIn("0.100s", output)
        self.assertIn("policy=would_launch:vs code", output)
        self.assertIn("policy=clarify:vs code", output)
        self.assertIn("policy=would_launch:chrome", output)
        self.assertIn("policy=conversation", output)
        self.assertIn("policy=denied", output)
        self.assertEqual([call.args[0] for call in interpret.call_args_list], [case.text for case in smoke_semantic.CASES])
        for call in interpret.call_args_list:
            self.assertEqual(call.kwargs, {"model": DEFAULT_CHAT_MODEL, "num_ctx": DEFAULT_CONTEXT_SIZE, "timeout": 15.0})

    def test_diagnostics_forwarded_only_when_requested(self):
        status, _output, _errors, interpret = self.invoke(["--live", "--diagnostics"], self.expected())
        self.assertEqual(status, 0)
        self.assertTrue(all(call.kwargs["diagnostics"] for call in interpret.call_args_list))

    def test_negation_may_be_unsupported_but_cannot_launch(self):
        decisions = self.expected()
        decisions[4] = Decision("unsupported", "none")
        status, output, _errors, _interpret = self.invoke(["--live"], decisions)
        self.assertEqual(status, 0)
        self.assertIn("5. PASS", output)

    def test_semantic_misclassification_is_reported_even_when_policy_denies(self):
        decisions = self.expected()
        decisions[6] = Decision("open_app", "chrome")
        status, output, _errors, _interpret = self.invoke(["--live"], decisions)
        self.assertEqual(status, 1)
        self.assertIn("7. FAIL", output)
        self.assertIn("intent=open_app; app=chrome; policy=denied", output)
        self.assertIn("6/7 cases matched", output)

    def test_classifier_error_is_measured_without_echoing_private_exception(self):
        decisions = self.expected()
        decisions[0] = IntentError("private response or microphone transcript")
        status, output, _errors, interpret = self.invoke(["--live"], decisions)
        self.assertEqual(status, 1)
        self.assertEqual(interpret.call_count, 7)
        self.assertIn("1. FAIL 0.100s; classification failed (IntentError)", output)
        self.assertNotIn("private response", output)
        self.assertIn("6/7 cases matched", output)


if __name__ == "__main__":
    unittest.main()
