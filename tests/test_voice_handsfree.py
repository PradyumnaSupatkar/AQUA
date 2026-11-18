"""Hands-free spoken replies: wake during playback, Ctrl+C, follow-up window.

Uses FakeEngine/FakePlayer and scripted microphone frames; no audio device,
wake model, Whisper or Ollama.
"""

from collections import deque
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from time import perf_counter, sleep
import unittest
from unittest.mock import Mock, patch

import numpy as np

from aqua import cli, handsfree
from aqua.actions.tools import ActionState
from aqua.audio.streaming import FRAME_SAMPLES
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, ChatEvent, ModelInfo
from aqua.speech.recognizer import Transcript
from aqua.voice.engines import FakeEngine
from aqua.voice.output import SpeechOutput
from aqua.voice.player import FakePlayer

WAKE, COMMAND, DURING_PLAYBACK, TAIL = .9, .1, .33, .2


def frame(value=0.0):
    return np.full(FRAME_SAMPLES, value, dtype=np.float32)


def utterance(value=COMMAND):
    return [*[frame(value) for _ in range(10)], *[frame() for _ in range(4)]]


def wake_turn():
    return [frame(WAKE), *utterance()]


class Detector:
    def feed(self, samples):
        return Mock(keyword="HEY_AQUA") if float(np.mean(samples)) > .8 else None

    def reset(self):
        pass


class Vad:
    def probability(self, samples):
        return .95 if float(np.mean(samples)) > .05 else .01

    def reset(self):
        pass


class Microphone:
    """Scripted frames; a callable item runs (e.g. releasing playback) and yields no frame."""
    def __init__(self, items):
        self.items = deque(items)
        self.device_info = None
        self.paused = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read_frame(self, **_options):
        while True:
            if not self.items:
                raise KeyboardInterrupt
            item = self.items.popleft()
            if isinstance(item, BaseException):
                raise item
            if not callable(item):
                return item
            item()

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False


def until(predicate, timeout=3.0):
    """The scripted microphone never blocks like a real one; let workers catch up."""
    deadline = perf_counter() + timeout
    while not predicate():
        if perf_counter() > deadline:
            raise AssertionError("condition not reached")
        sleep(.002)


def heard(pcm):
    """Distinct synthetic levels present in a recognised recording."""
    values = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    return {round(float(value), 2) for value in np.unique(values)}


class HandsfreeVoiceTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Hands-free voice tests must stay offline."))
            manager.start()
            self.addCleanup(manager.stop)
        self.state = ActionState()
        self.recognizer = Mock()
        self.client = Mock()
        self.client.check_model.return_value = ModelInfo(DEFAULT_CHAT_MODEL, "test", "qwen3", "non_thinking")
        self.replies = deque()
        self.client.stream_chat.side_effect = lambda *_a, **_k: iter(
            [ChatEvent(self.replies.popleft() if self.replies else "Okay."), ChatEvent(done=True)])
        console = Mock()
        console.poll.return_value = None
        patches = {
            "aqua.handsfree.SherpaWakeDetector": Detector(), "aqua.handsfree.StreamingVad": Vad(),
            "aqua.handsfree.OfflineRecognizer": self.recognizer, "aqua.handsfree.ConsoleCommands": console,
            "aqua.chat.OllamaClient": self.client, "aqua.handsfree.ActionState": self.state,
        }
        for target, value in patches.items():
            manager = patch(target, return_value=value)
            manager.start()
            self.addCleanup(manager.stop)
        manager = patch("aqua.actions.semantic.interpret_request", side_effect=AssertionError("No classifier"))
        manager.start()
        self.addCleanup(manager.stop)

    def make_speaker(self, *, gate=True):
        self.engine, self.player = FakeEngine(), FakePlayer(gate=gate)
        self.speaker = SpeechOutput(self.engine, self.player, warn=lambda _m: None)
        self.addCleanup(self.speaker.close)
        return self.speaker

    def release_playback(self):
        def release():
            self.player.release()
            self.assertTrue(self.speaker.wait(3))
        return release

    def invoke(self, items, texts, *, speaker=None, **options):
        self.microphone = Microphone(items)
        texts = iter(texts)
        self.recognizer.transcribe.side_effect = lambda *_a, **_k: Transcript(next(texts), "en", .99, .32, -10, -20, 0, -.1, .01, 1)
        defaults = dict(end_silence=.128, initial_timeout=.512, max_utterance=1.024, pre_roll=.064,
                        wake_cooldown=0, speak=speaker is not None)
        defaults.update(options)
        output, errors = StringIO(), StringIO()
        with patch("aqua.handsfree.MicrophoneStream", return_value=self.microphone), \
                patch("aqua.chat._open_speaker", return_value=speaker), \
                redirect_stdout(output), redirect_stderr(errors):
            status = handsfree.run_handsfree(**defaults)
        return status, output.getvalue(), errors.getvalue()

    def recordings(self):
        return [call.args[0] for call in self.recognizer.transcribe.call_args_list]

    # ---- wake during playback ------------------------------------------
    def test_wake_during_playback_stops_it_and_captures_without_playback_audio(self):
        speaker = self.make_speaker()
        self.replies.extend(["First sentence. Second sentence. Third.", "Done."])
        items = [*wake_turn(), lambda: self.assertTrue(self.player.playing.wait(3)),
                 *[frame(DURING_PLAYBACK) for _ in range(6)], *wake_turn(),
                 lambda: until(lambda: len(self.player.played) == 2)]
        status, output, errors = self.invoke(items, ["Hey Aqua, tell me.", "Hey Aqua, stop that."], speaker=speaker)
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(self.recognizer.transcribe.call_count, 2)
        self.assertIn("Speech stopped.", output)
        self.assertGreaterEqual(self.player.stops, 1)
        first, second = self.recordings()
        self.assertIn(WAKE, heard(first))           # Normal turns keep wake pre-roll.
        self.assertEqual(len(second), 14 * FRAME_SAMPLES * 2)
        self.assertNotIn(DURING_PLAYBACK, heard(second))
        self.assertNotIn(WAKE, heard(second))
        # Only the first sentence ever reached the device before the wake; then the new reply.
        self.assertEqual(self.player.played, [(len("First sentence.") * 4, 16000), (len("Done.") * 4, 16000)])

    def test_speech_during_playback_alone_never_reaches_whisper(self):
        speaker = self.make_speaker()
        self.replies.append("Here is a long answer. With more. And more. And more.")
        items = [*wake_turn(), *[frame(DURING_PLAYBACK) for _ in range(60)]]
        status, _output, _errors = self.invoke(items, ["Hey Aqua, explain."], speaker=speaker)
        self.assertEqual(status, 0)
        self.recognizer.transcribe.assert_called_once()

    # ---- Ctrl+C --------------------------------------------------------
    def test_ctrl_c_while_speaking_stops_speech_and_keeps_listening(self):
        speaker = self.make_speaker()
        self.replies.extend(["A fairly long spoken reply. It continues.", "Second answer."])
        self.state.pending_app = None
        items = [*wake_turn(), frame(), KeyboardInterrupt(), *wake_turn()]
        status, output, errors = self.invoke(items, ["Hey Aqua, talk.", "Hey Aqua, again."], speaker=speaker)
        self.assertEqual((status, errors), (0, ""))
        self.assertIn("Speech stopped.", output)
        self.assertEqual(self.recognizer.transcribe.call_count, 2)
        self.assertEqual(output.count("Hands-free chat ended."), 1)  # Only the final idle Ctrl+C exits.

    def test_ctrl_c_while_idle_and_silent_still_exits(self):
        speaker = self.make_speaker(gate=False)
        self.replies.append("Short.")
        items = [*wake_turn(), lambda: self.speaker.wait(3), KeyboardInterrupt(), *wake_turn()]
        status, output, _errors = self.invoke(items, ["Hey Aqua, hi.", "unused"], speaker=speaker)
        self.assertEqual(status, 0)
        self.assertIn("Hands-free chat ended.", output)
        self.recognizer.transcribe.assert_called_once()

    # ---- follow-up window ----------------------------------------------
    def test_follow_up_opens_only_after_playback_ends_plus_tail(self):
        speaker = self.make_speaker()
        self.replies.extend(["Would you like the details?", "Here they are."])
        items = [*wake_turn(),
                 *[frame(DURING_PLAYBACK) for _ in range(30)],      # Still speaking: wake-only.
                 self.release_playback(),
                 *[frame(TAIL) for _ in range(handsfree.FOLLOW_UP_TAIL_FRAMES)],
                 *utterance()]                                       # Answer without the wake word.
        status, output, errors = self.invoke(items, ["Hey Aqua, tell me about F1.", "The engines, please."],
                                             speaker=speaker, diagnostics=True)
        self.assertEqual((status, errors), (0, ""))
        self.assertIn("Listening for your answer...", output)
        self.assertIn("Speech output: first audio", output)
        self.assertEqual(self.recognizer.transcribe.call_count, 2)
        answer = self.recordings()[1]
        self.assertEqual(heard(answer), {COMMAND, 0.0})
        users = [m["content"] for m in self.client.stream_chat.call_args.args[0] if m["role"] == "user"]
        self.assertEqual(users[-1], "The engines, please.")

    def test_no_follow_up_after_a_normal_reply(self):
        speaker = self.make_speaker(gate=False)
        self.replies.append("Formula One is a motorsport.")
        items = [*wake_turn(), lambda: self.speaker.wait(3), *[frame() for _ in range(10)], *utterance()]
        status, output, _errors = self.invoke(items, ["Hey Aqua, what is F1?"], speaker=speaker)
        self.assertEqual(status, 0)
        self.assertNotIn("Listening for your answer", output)
        self.recognizer.transcribe.assert_called_once()

    def test_text_only_sessions_get_the_follow_up_after_a_question(self):
        self.replies.extend(["Which app should I open?", "Okay."])
        items = [*wake_turn(), *[frame(TAIL) for _ in range(handsfree.FOLLOW_UP_TAIL_FRAMES)], *utterance()]
        status, output, _errors = self.invoke(items, ["Hey Aqua, open something.", "Notepad."])
        self.assertEqual(status, 0)
        self.assertIn("Listening for your answer...", output)
        self.assertEqual(self.recognizer.transcribe.call_count, 2)
        self.assertNotIn(TAIL, heard(self.recordings()[1]))

    def test_pending_confirmation_opens_follow_up_and_silence_keeps_it(self):
        def respond(*_a, **_k):
            return iter([ChatEvent(done=True, tool_calls=(
                {"function": {"name": "open_application", "arguments": {"app_id": "vs code"}}},))])
        self.client.stream_chat.side_effect = respond
        items = [*wake_turn(), *[frame() for _ in range(handsfree.FOLLOW_UP_TAIL_FRAMES)],
                 *[frame() for _ in range(40)]]
        status, output, _errors = self.invoke(items, ["Hey Aqua, I feel like programming."])
        self.assertEqual(status, 0)
        self.assertIn("Would you like me to open vs code?", output)
        self.assertIn("Listening for your answer...", output)
        self.assertIn("No answer heard.", output)
        self.assertNotIn("Skipped:", output)
        self.recognizer.transcribe.assert_called_once()

    def test_follow_up_window_stays_inside_the_utterance_cap(self):
        frontend = handsfree.HandsfreeFrontend(Detector(), Vad(), handsfree.UtteranceConfig(max_utterance=1.024, initial_timeout=.512))
        frontend.arm_follow_up(3.0)
        events = [frontend.feed(frame(), now=0) for _ in range(40)]
        self.assertEqual([e.status for e in events if e is not None], ["no_speech"])
        self.assertTrue(next(e for e in events if e is not None).follow_up)

    def test_follow_up_zero_disables_the_window(self):
        self.replies.append("Which app should I open?")
        items = [*wake_turn(), *[frame() for _ in range(20)], *utterance()]
        status, output, _errors = self.invoke(items, ["Hey Aqua, open something."], follow_up=0)
        self.assertEqual(status, 0)
        self.assertNotIn("Listening for your answer", output)
        self.recognizer.transcribe.assert_called_once()

    def test_invalid_follow_up_is_a_setup_error(self):
        status, _output, errors = self.invoke([], [], follow_up=.2)
        self.assertEqual(status, 1)
        self.assertIn("follow-up", errors)


class FollowUpCliTests(unittest.TestCase):
    def options(self, arguments):
        with patch("aqua.cli.run_handsfree", return_value=0) as run, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(cli.main(["handsfree", *arguments]), 0)
        return run.call_args.kwargs

    def test_default_matches_constant_and_value_forwards(self):
        self.assertEqual(self.options([])["follow_up"], handsfree.FOLLOW_UP_SECONDS)
        self.assertEqual(self.options(["--follow-up", "0"])["follow_up"], 0)
        self.assertEqual(self.options(["--follow-up", "4.5"])["follow_up"], 4.5)

    def test_invalid_values_rejected(self):
        for value in (".2", "11", "nan", "-1"):
            with self.subTest(value=value), patch("aqua.cli.run_handsfree") as run, \
                    redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                cli.main(["handsfree", "--follow-up", value])
            run.assert_not_called()

    def test_follow_up_is_handsfree_only(self):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            cli.main(["voice-chat", "--follow-up", "3"])


if __name__ == "__main__":
    unittest.main()
