"""tools/record_asr_set.py without a microphone: prompts, capture until silence, WAV format."""

import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from aqua.audio.vad import UtteranceConfig
from tools import record_asr_set as rec


class FakeMicrophone:
    def __init__(self, probabilities):
        self.frames = [np.full(512, .1 if p > .5 else 0.0, dtype=np.float32) for p in probabilities]
        self.probabilities = list(probabilities)
        self.paused = True
        self.log = []

    def resume(self):
        self.paused = False
        self.log.append("resume")

    def pause(self):
        self.paused = True
        self.log.append("pause")

    def read_frame(self, timeout=.1):
        return self.frames.pop(0) if self.frames else None


class FakeVad:
    def __init__(self, mic):
        self.mic = mic

    def reset(self):
        pass

    def probability(self, _frame):
        return self.mic.probabilities.pop(0)


class RecorderTests(unittest.TestCase):
    def test_prompts_cover_the_requested_commands(self):
        texts = [text for _id, text in rec.PROMPTS]
        for wanted in ("write this is a second line into notepad", "write hello I am Aqua", "write meeting at noon",
                       "open notepad", "open VS Code", "pause the video", "pause the music", "turn the volume down",
                       "lock my screen", "set the quality to 1080p", "go back 10 seconds",
                       "search youtube for lofi hip hop", "play the second one", "set the volume to 30",
                       "jump to 2:30", "don't open Chrome", "open Spotify", "open Calculator", "open Chrome",
                       "open Brave", "open Visual Studio Code"):
            self.assertIn(wanted, texts)
        self.assertTrue(28 <= len(texts) <= 32)
        self.assertEqual(len({pid for pid, _ in rec.PROMPTS}), len(rec.PROMPTS))
        self.assertTrue(any(len(text.split()) >= 15 for text in texts))  # One long sentence.

    def test_records_until_silence_and_pauses_the_microphone(self):
        speech = [0.0] * 5 + [0.9] * 20 + [0.0] * 30  # 0.8 s of silence ends it (25 frames).
        mic = FakeMicrophone(speech)
        event = rec.record_one(mic, FakeVad(mic), UtteranceConfig(end_silence=.8, initial_timeout=8.0,
                                                                  max_utterance=20.0, pre_roll=0.0))
        self.assertEqual(event.status, "complete")
        self.assertEqual(mic.log, ["resume", "pause"])
        self.assertAlmostEqual(event.speech_seconds, 20 * 512 / 16000, places=3)

    def test_wav_is_16_khz_mono_16_bit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "x.wav"
            rec.write_wav(path, np.array([0.0, 0.5, -1.5], dtype=np.float32))
            with wave.open(str(path)) as handle:
                self.assertEqual((handle.getframerate(), handle.getnchannels(), handle.getsampwidth()), (16000, 1, 2))
                data = np.frombuffer(handle.readframes(3), dtype="<i2")
            self.assertEqual(list(data), [0, 16383, -32767])

    def test_audio_goes_to_the_ignored_cache(self):
        self.assertEqual(rec.OUT, Path(__file__).resolve().parents[1] / ".cache" / "asr-set")
        ignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/.cache/", ignore)


if __name__ == "__main__":
    unittest.main()
