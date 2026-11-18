"""Ollama latency diagnostics use mocked streams and never retain reasoning."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import math
import unittest
from unittest.mock import Mock, patch

from aqua.brain.conversation import Conversation
from aqua.brain.ollama import ChatEvent, GenerationSettings, OllamaClient, OllamaProtocolError
from aqua.chat import _respond


class BackendMetricsTests(unittest.TestCase):
    def events(self, items, clock=None):
        response = Mock(status=200)
        response.readline.side_effect = [json.dumps(item).encode() + b"\n" for item in items]
        connection = Mock()
        connection.getresponse.return_value = response
        clock = clock or list(range(1 + 2 * len(items)))
        with patch("aqua.brain.ollama.HTTPConnection", return_value=connection), patch(
            "aqua.brain.ollama.perf_counter", side_effect=clock
        ):
            events = list(OllamaClient().stream_chat(
                [{"role": "user", "content": "A question"}], model="qwen3:4b", settings=GenerationSettings()
            ))
        connection.close.assert_called_once()
        response.close.assert_called_once()
        return events

    def test_distinct_server_and_stream_timings_discard_reasoning_text(self):
        private, more = "PRIVATE_REASONING", "more"
        events = self.events([
            {"message": {"thinking": private}},
            {"message": {"thinking": more}},
            {"message": {"content": " \n"}},
            {"message": {"content": "4"}},
            {"done": True, "total_duration": 2_200_000_000, "load_duration": 100_000_000,
             "prompt_eval_count": 9, "prompt_eval_duration": 200_000_000,
             "eval_count": 19, "eval_duration": 1_900_000_000},
        ], [10, 10.1, 10.2, 10.3, 10.5, 10.6, 11, 11.1, 12, 12.1, 12.2])
        final = events[-1]
        self.assertEqual("".join(event.content for event in events), " \n4")
        self.assertNotIn(private, repr(events))
        self.assertEqual(final.load_duration, 100_000_000)
        self.assertEqual(final.prompt_eval_count, 9)
        self.assertEqual(final.prompt_eval_duration, 200_000_000)
        self.assertEqual(final.eval_count, 19)
        self.assertEqual(final.eval_duration, 1_900_000_000)
        self.assertAlmostEqual(final.first_stream_seconds, .2)
        self.assertAlmostEqual(final.first_thinking_seconds, .2)
        self.assertAlmostEqual(final.first_content_seconds, 2)
        self.assertEqual(final.thinking_chunks, 2)
        self.assertEqual(final.thinking_characters, len(private) + len(more))
        self.assertTrue(final.done)

    def test_direct_answer_reports_no_thinking_phase(self):
        events = self.events([{"message": {"content": "Hello"}}, {"done": True}])
        self.assertEqual(events[-1].first_stream_seconds, 2)
        self.assertEqual(events[-1].first_content_seconds, 2)
        self.assertIsNone(events[-1].first_thinking_seconds)
        self.assertEqual(events[-1].thinking_chunks, 0)
        self.assertEqual(events[-1].thinking_characters, 0)

    def test_malformed_optional_statistics_do_not_break_valid_answer(self):
        final = self.events([{
            "message": {"content": "Fine"}, "done": True, "load_duration": -1,
            "total_duration": math.nan, "eval_count": True, "eval_duration": 2**1000,
            "prompt_eval_count": "PRIVATE", "prompt_eval_duration": 2.5,
        }])[-1]
        self.assertEqual(final.content, "Fine")
        for name in ("load_duration", "total_duration", "eval_count", "eval_duration", "prompt_eval_count", "prompt_eval_duration"):
            self.assertIsNone(getattr(final, name))
        self.assertNotIn("PRIVATE", repr(final))

    def test_malformed_thinking_channel_fails_without_echoing_its_body(self):
        with self.assertRaises(OllamaProtocolError) as caught:
            self.events([{"message": {"thinking": ["PRIVATE"]}, "done": True}])
        self.assertNotIn("PRIVATE", str(caught.exception))


class VisibleMetricsTests(unittest.TestCase):
    def respond(self, final, *, diagnostics=True):
        client = Mock()
        client.stream_chat.return_value = iter([ChatEvent("An answer."), final])
        conversation = Conversation()
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors), patch(
            "aqua.chat.perf_counter", side_effect=[10, 11, 12]
        ):
            success = _respond(client, conversation, "Question", model="qwen3:4b",
                               settings=GenerationSettings(), diagnostics=diagnostics)
        self.assertTrue(success)
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(conversation.turn_count, 1)
        return output.getvalue()

    def test_diagnostics_distinguish_server_work_and_observed_phases(self):
        output = self.respond(ChatEvent(
            done=True, total_duration=8_000_000_000, load_duration=1_000_000_000,
            prompt_eval_duration=2_000_000_000, prompt_eval_count=20,
            eval_count=50, eval_duration=5_000_000_000,
            first_stream_seconds=.2, first_thinking_seconds=.2, first_content_seconds=1.2,
            thinking_chunks=30, thinking_characters=100,
        ))
        self.assertIn("first visible token 1.000s; complete response 2.000s", output)
        self.assertIn("load 1.000s", output)
        self.assertIn("prompt 2.000s (20 tokens)", output)
        self.assertIn("generation 5.000s (50 total tokens, 10.00 tok/s)", output)
        self.assertIn("thinking-to-answer interval 1.000s", output)
        self.assertIn("not exact server thinking time", output)
        self.assertIn("30 chunks, 100 characters", output)
        self.assertIn("not token counts", output)
        self.assertIn("answer-only token count is unavailable", output)

    def test_missing_and_malformed_ui_metrics_are_graceful(self):
        for final in (ChatEvent(done=True), ChatEvent(
            done=True, total_duration=math.nan, load_duration=-1,
            prompt_eval_count="PRIVATE", eval_count=True, eval_duration=2**1000,
            first_stream_seconds=math.inf, first_thinking_seconds=-1,
            first_content_seconds=math.nan, thinking_chunks="PRIVATE", thinking_characters=2**1000,
        )):
            with self.subTest(final_type=type(final).__name__):
                output = self.respond(final)
                self.assertIn("n/a", output)
                self.assertNotIn("PRIVATE", output)
                self.assertNotIn("nan", output)
                self.assertNotIn("inf", output)

    def test_zero_generation_duration_has_no_division_by_zero(self):
        output = self.respond(ChatEvent(done=True, eval_count=0, eval_duration=0))
        self.assertIn("generation 0.000s (0 total tokens, n/a)", output)

    def test_server_metrics_remain_opt_in(self):
        output = self.respond(ChatEvent(done=True, eval_count=10), diagnostics=False)
        self.assertIn("An answer.", output)
        self.assertNotIn("Ollama server:", output)
        self.assertNotIn("Hidden stream volume:", output)


if __name__ == "__main__":
    unittest.main()
