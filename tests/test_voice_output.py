"""SpeechOutput pipeline with FakeEngine and FakePlayer; never opens an audio device."""

from threading import Event, enumerate as threads
import unittest

from aqua.voice.engines import FakeEngine
from aqua.voice.output import SpeechOutput, open_speaker
from aqua.voice.player import FakePlayer
from aqua.voice.text import REST_NOTICE


def voice_threads():
    return [thread for thread in threads() if thread.name.startswith("aqua-voice")]


class SpeechOutputTests(unittest.TestCase):
    def make(self, engine=None, player=None, **options):
        self.warnings = []
        self.engine = engine or FakeEngine()
        self.player = player or FakePlayer()
        speaker = SpeechOutput(self.engine, self.player, warn=self.warnings.append, **options)
        self.addCleanup(speaker.close)
        return speaker

    def test_streamed_chunks_are_spoken_in_order_with_budget(self):
        speaker = self.make()
        for chunk in ("Hello the", "re. Second sen", "tence! Third? Fourth. Fifth.", " Want more?"):
            speaker.feed(chunk)
        speaker.finish_turn()
        self.assertTrue(speaker.wait(5))
        self.assertEqual(self.engine.texts, ["Hello there.", "Second sentence!", "Third?", REST_NOTICE, "Want more?"])
        self.assertEqual(len(self.player.played), 5)
        self.assertEqual(self.player.played[0], (len("Hello there.") * 4, 16000))
        timings = speaker.timings
        self.assertEqual(timings.sentences_spoken, 5)
        self.assertIsNotNone(timings.first_audio_seconds)
        self.assertGreater(timings.audio_seconds, 0)
        self.assertIn("chunks spoken 5", timings.describe())
        self.assertFalse(speaker.is_speaking)
        self.assertEqual(self.warnings, [])

    def test_unspeakable_text_produces_no_audio(self):
        speaker = self.make()
        speaker.feed("--- 🎉\n")
        speaker.finish_turn()
        self.assertTrue(speaker.wait(2))
        self.assertEqual(self.engine.texts, [])

    def test_next_sentence_is_synthesised_while_current_plays(self):
        speaker = self.make(player=FakePlayer(gate=True))
        speaker.feed("One. Two. Three.")
        speaker.finish_turn()
        self.assertTrue(self.player.playing.wait(2))
        for _ in range(200):
            if len(self.engine.texts) >= 2:
                break
            Event().wait(.005)
        self.assertGreaterEqual(len(self.engine.texts), 2)
        self.assertEqual(len(self.player.played), 1)
        self.assertTrue(speaker.is_speaking)
        self.player.release()
        self.assertTrue(speaker.wait(5))

    def test_stop_mid_reply_clears_queued_audio_immediately(self):
        speaker = self.make(player=FakePlayer(gate=True))
        speaker.feed("One. Two. Three.")
        speaker.finish_turn()
        self.assertTrue(self.player.playing.wait(2))
        speaker.stop()
        self.assertFalse(speaker.is_speaking)  # stop() returns only after playback acknowledged.
        self.assertEqual(len(self.player.played), 1)
        self.assertGreaterEqual(self.player.stops, 1)
        self.player.release()
        Event().wait(.05)
        self.assertEqual(len(self.player.played), 1)
        # Speech still works for the next turn.
        speaker.feed("Again.")
        speaker.finish_turn()
        self.assertTrue(speaker.wait(5))
        self.assertEqual(len(self.player.played), 2)

    def test_engine_failure_warns_once_and_disables_speech(self):
        speaker = self.make(engine=FakeEngine(fail=True))
        speaker.feed("One. Two. Three.")
        speaker.finish_turn()
        self.assertTrue(speaker.wait(2))
        speaker.feed("Later turn. More.")
        speaker.finish_turn()
        self.assertTrue(speaker.wait(1))
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("continue as text", self.warnings[0])
        self.assertFalse(speaker.enabled)
        self.assertEqual(self.player.played, [])
        self.assertEqual(len(self.engine.texts), 1)

    def test_player_failure_warns_once(self):
        speaker = self.make(player=FakePlayer(fail=True))
        speaker.feed("One. Two.")
        speaker.finish_turn()
        self.assertTrue(speaker.wait(2))
        self.assertEqual(len(self.warnings), 1)
        self.assertFalse(speaker.enabled)

    def test_close_joins_threads(self):
        before = len(voice_threads())
        speaker = SpeechOutput(FakeEngine(), FakePlayer(gate=True), warn=lambda _m: None)
        self.assertEqual(len(voice_threads()), before + 2)
        speaker.feed("Speaking now. And more.")
        speaker.close()
        self.assertEqual(len(voice_threads()), before)
        speaker.close()
        speaker.feed("ignored.")

    def test_open_speaker_reports_missing_voice_as_note(self):
        notes = []
        speaker = open_speaker(engine="kokoro", model_dir=r"D:\definitely\absent\voice", out=notes.append)
        self.assertIsNone(speaker)
        self.assertEqual(len(notes), 1)
        self.assertIn("aqua setup-voice --engine kokoro --download", notes[0])
        self.assertIn("text only", notes[0])


if __name__ == "__main__":
    unittest.main()
