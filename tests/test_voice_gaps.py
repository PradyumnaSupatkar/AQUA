"""Mid-reply gap diagnostics and the model's token rate in speech timings."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import Mock

from aqua import chat
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import ChatEvent, GenerationSettings
from aqua.voice.output import SpeechOutput, SpeechTimings, _Turn


class TurnTimelineTests(unittest.TestCase):
    def test_gaps_are_idle_time_between_chunks_after_first_audio(self):
        turn = _Turn(started=0.0)
        turn.schedule(1.0, 2.0)   # first chunk: audio 1.0 -> 3.0, no gap before first audio
        turn.schedule(2.5, 1.0)   # ready before the first ends: plays 3.0 -> 4.0, no gap
        turn.schedule(5.5, 1.0)   # device idle 4.0 -> 5.5: gap 1.5
        turn.schedule(6.7, 0.5)   # idle 6.5 -> 6.7: gap 0.2
        timings = turn.snapshot()
        self.assertAlmostEqual(timings.longest_gap_seconds, 1.5)
        self.assertAlmostEqual(timings.total_gap_seconds, 1.7)

    def test_describe_reports_gap_and_model_rate(self):
        timings = SpeechTimings(.94, 3.0, 6.0, 4, 4.2, 4.5, 14.3)
        text = timings.describe()
        self.assertIn("longest mid-reply gap 4.200s (total 4.500s)", text)
        self.assertIn("model generation 14.3 tok/s", text)
        self.assertIn("model generation n/a", SpeechTimings(None, 0, 0, 0).describe())


class SlowEngine:
    """Synthesis slower than the audio it makes, so the player starves."""
    sample_rate = 1000

    def synthesize(self, text):
        import numpy as np
        from time import sleep
        sleep(.2)                         # 0.2 s of synthesis...
        return np.zeros(50, np.float32)   # ...for 0.05 s of audio


class PlayerStarvationTests(unittest.TestCase):
    def test_pipeline_records_gaps_when_synthesis_cannot_keep_up(self):
        speaker = SpeechOutput(SlowEngine(), Mock(spec=["play", "stop"]), warn=lambda _m: None)
        self.addCleanup(speaker.close)
        speaker.feed("One. Two. Three.")
        speaker.finish_turn()
        self.assertTrue(speaker.wait(5))
        timings = speaker.timings
        self.assertEqual(timings.sentences_spoken, 3)
        # Each chunk is ready ~0.2 s after the previous one, which played 0.05 s: ~0.15 s idle, twice.
        self.assertGreater(timings.longest_gap_seconds, .08)
        self.assertLess(timings.longest_gap_seconds, .6)
        self.assertGreater(timings.total_gap_seconds, timings.longest_gap_seconds)


class RespondRateTests(unittest.TestCase):
    def test_respond_passes_the_model_token_rate_to_the_speaker_before_finishing(self):
        client = Mock()
        client.stream_chat.side_effect = lambda *_a, **_k: iter([
            ChatEvent("Hello there."), ChatEvent(done=True, eval_count=143, eval_duration=10_000_000_000)])
        speaker = Mock()
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            chat._respond(client, Conversation(), "Hi", model="m", settings=GenerationSettings(),
                          diagnostics=False, speaker=speaker)
        names = [entry[0] for entry in speaker.method_calls]
        self.assertEqual(names[-2:], ["note_generation_rate", "finish_turn"])
        self.assertAlmostEqual(speaker.note_generation_rate.call_args.args[0], 14.3)

    def test_rate_is_ignored_when_unknown(self):
        speaker = SpeechOutput(Mock(sample_rate=1000, synthesize=Mock(return_value=[])), Mock(spec=["play", "stop"]),
                               warn=lambda _m: None)
        self.addCleanup(speaker.close)
        speaker.feed("x")
        speaker.note_generation_rate(None)
        speaker.note_generation_rate(-3)
        self.assertIsNone(speaker.timings.llm_tokens_per_second)
        speaker.note_generation_rate(20.5)
        self.assertEqual(speaker.timings.llm_tokens_per_second, 20.5)


if __name__ == "__main__":
    unittest.main()
