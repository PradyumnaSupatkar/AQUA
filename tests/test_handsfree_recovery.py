"""Hands-free keeps listening through microphone trouble: dropped audio (input overflow, full queue)
discards only the affected turn; a stalled or lost stream is reopened; three failures within a minute
end the session. Partial audio never reaches the recogniser. Scripted microphones, no devices."""

import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from aqua import handsfree
from aqua.audio.streaming import MicrophoneFailure

import test_voice_handsfree as base
from test_voice_handsfree import COMMAND, Microphone, frame, utterance, wake_turn


class GapMicrophone(Microphone):
    """A string item "gap:<kind>" marks audio lost just before the next frame."""

    def __init__(self, items):
        super().__init__(items)
        self._gap = None

    def read_frame(self, **options):
        while self.items and isinstance(self.items[0], str):
            self._gap = self.items.popleft().split(":", 1)[1]
        return super().read_frame(**options)

    def take_gap(self):
        gap, self._gap = self._gap, None
        return gap


class RecoveryTests(unittest.TestCase):
    setUp = base.HandsfreeVoiceTests.setUp  # The same offline patches, without re-running its tests.

    def run_with(self, microphones, texts):
        self.microphones = list(microphones)
        texts = iter(texts)
        from aqua.speech.recognizer import Transcript
        self.recognizer.transcribe.side_effect = lambda *_a, **_k: Transcript(next(texts), "en", .99, .32, -10, -20,
                                                                              0, -.1, .01, 1)
        options = dict(end_silence=.128, initial_timeout=.512, max_utterance=1.024, pre_roll=.064, wake_cooldown=0,
                       speak=False)
        output, errors = StringIO(), StringIO()
        with patch("aqua.handsfree.MicrophoneStream", side_effect=self.microphones), \
                patch("aqua.chat._open_speaker", return_value=None), \
                patch("aqua.handsfree.time.sleep"), \
                redirect_stdout(output), redirect_stderr(errors):
            status = handsfree.run_handsfree(**options)
        return status, output.getvalue(), errors.getvalue()

    def test_audio_lost_during_a_capture_discards_only_that_turn(self):
        partial = [frame(COMMAND) for _ in range(4)]
        items = [frame(.9), *partial, "gap:input_overflow", *utterance(), *wake_turn()]
        status, output, errors = self.run_with([GapMicrophone(items)], ["Hello there."])
        self.assertEqual(status, 0)
        self.assertIn("Audio was lost (input overflow: the audio callback ran late, likely CPU load) during a capture; "
                      "this turn was discarded", errors)
        self.assertEqual(self.recognizer.transcribe.call_count, 1)  # Only the later, complete turn.

    def test_a_gap_while_idle_is_not_an_error(self):
        items = ["gap:queue_full", frame(), *wake_turn()]
        status, _output, errors = self.run_with([GapMicrophone(items)], ["Hello there."])
        self.assertEqual(status, 0)
        self.assertNotIn("discarded", errors)
        self.assertEqual(self.recognizer.transcribe.call_count, 1)

    def test_a_stalled_stream_is_reopened_and_listening_continues(self):
        first = GapMicrophone([frame(), MicrophoneFailure("stall", "The microphone delivered no audio for 2 s.")])
        second = GapMicrophone([*wake_turn()])
        status, output, errors = self.run_with([first, second], ["Hello there."])
        self.assertEqual(status, 0)
        self.assertIn("Microphone problem (stall) while listening for the wake word", errors)
        self.assertIn("Reopening the microphone", output)
        self.assertEqual(self.recognizer.transcribe.call_count, 1)

    def test_failure_during_capture_never_sends_partial_audio(self):
        first = GapMicrophone([frame(.9), *[frame(COMMAND) for _ in range(5)],
                               MicrophoneFailure("device", "The microphone disconnected or stopped.")])
        second = GapMicrophone([*wake_turn()])
        status, _output, errors = self.run_with([first, second], ["Hello there."])
        self.assertIn("Microphone problem (device) during a capture", errors)
        self.assertEqual(self.recognizer.transcribe.call_count, 1)  # Only the turn after reopening.

    def test_three_failures_within_a_minute_end_the_session(self):
        failing = [GapMicrophone([MicrophoneFailure("device", "The microphone disconnected or stopped.")])
                   for _ in range(3)]
        status, _output, errors = self.run_with(failing, [])
        self.assertEqual(status, 1)
        self.assertIn("failed 3 times within a minute", errors)


if __name__ == "__main__":
    unittest.main()
