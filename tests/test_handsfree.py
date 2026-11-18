"""Wake-gated speech turns and the real action policy, without hardware/I/O."""

from collections import deque
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from aqua import handsfree
from aqua.actions.launcher import ALLOWED_APPS
from aqua.actions.tools import ActionState, tool_definitions
from aqua.audio.backend import AudioError
from aqua.audio.streaming import FRAME_SAMPLES, SAMPLE_RATE
from aqua.audio.vad import UtteranceConfig
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, ChatEvent, ModelInfo, OllamaError
from aqua.speech.models import SpeechError
from aqua.speech.recognizer import Transcript
from aqua.wake.models import WakeError


FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE


def frame(value=0.0):
    return np.full(FRAME_SAMPLES, value, dtype=np.float32)


class AcousticDetector:
    """Only a distinct synthetic marker produces an acoustic wake event."""
    def __init__(self):
        self.calls = self.resets = 0

    def feed(self, samples):
        self.calls += 1
        if float(np.mean(samples)) > .8:
            return SimpleNamespace(keyword="HEY_AQUA")
        return None

    def reset(self):
        self.resets += 1


class AcousticVad:
    def __init__(self):
        self.calls = self.resets = 0

    def probability(self, samples):
        self.calls += 1
        return .95 if float(np.mean(samples)) > .05 else .01

    def reset(self):
        self.resets += 1


class NoExternalEffects(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Hands-free unit tests must stay offline and launch nothing."))
            mock = manager.start()
            self.addCleanup(manager.stop)
            if target == "subprocess.Popen":
                self.process_guard = mock


class HandsfreeFrontendTests(NoExternalEffects):
    def make_frontend(self, **options):
        self.detector, self.vad = AcousticDetector(), AcousticVad()
        config = UtteranceConfig(end_silence=.128, initial_timeout=.512, max_utterance=1.024,
                                 pre_roll=.064, min_speech=.064, **options)
        return handsfree.HandsfreeFrontend(self.detector, self.vad, config, cooldown=2)

    def push(self, frontend, values, *, start=10.0):
        events = []
        for index, value in enumerate(values):
            event = frontend.feed(frame(value), now=start + index * FRAME_SECONDS)
            if event is not None:
                events.append(event)
        return events

    def test_ordinary_speech_without_wake_never_arms_or_runs_vad(self):
        frontend = self.make_frontend()
        self.assertEqual(self.push(frontend, [.1] * 1000), [])
        self.assertFalse(frontend.active)
        self.assertEqual(self.detector.calls, 1000)
        self.assertEqual(self.vad.calls, 0)
        self.assertLessEqual(len(frontend._pre_roll), 2)

    def test_wake_event_arms_once_and_preserves_immediate_speech(self):
        frontend = self.make_frontend()
        events = self.push(frontend, [.25, .9, .1, .1, 0, 0, 0, 0])
        self.assertEqual([event.status for event in events], ["wake", "complete"])
        capture = events[-1]
        np.testing.assert_array_equal(capture.samples[:FRAME_SAMPLES], frame(.25))
        np.testing.assert_array_equal(capture.samples[FRAME_SAMPLES:2 * FRAME_SAMPLES], frame(.9))
        np.testing.assert_array_equal(capture.samples[2 * FRAME_SAMPLES:3 * FRAME_SAMPLES], frame(.1))
        self.assertEqual(self.detector.calls, 2)
        self.assertFalse(frontend.active)

    def test_brief_pause_after_wake_and_natural_internal_pause_are_retained(self):
        frontend = self.make_frontend()
        values = [.9] + [0] * 3 + [.1] * 2 + [0] * 2 + [.1] * 2 + [0] * 4
        events = self.push(frontend, values)
        self.assertEqual([event.status for event in events], ["wake", "complete"])
        self.assertAlmostEqual(events[-1].speech_seconds, 4 * FRAME_SECONDS)
        self.assertEqual(events[-1].samples.size, len(values) * FRAME_SAMPLES)

    def test_wake_phrase_context_alone_cannot_satisfy_post_wake_speech(self):
        frontend = self.make_frontend()
        events = self.push(frontend, [.9] + [0] * 16)
        self.assertEqual([event.status for event in events], ["wake", "no_speech"])
        self.assertIsNone(events[-1].samples)
        self.assertEqual(events[-1].speech_seconds, 0)

    def test_maximum_duration_discards_entire_turn(self):
        frontend = self.make_frontend()
        events = self.push(frontend, [.9] + [.1] * 32)
        self.assertEqual([event.status for event in events], ["wake", "too_long"])
        self.assertIsNone(events[-1].samples)
        self.assertLessEqual(events[-1].duration, frontend.config.max_utterance + frontend.config.pre_roll)

    def test_reset_keeps_cooldown_against_repeated_wake_utterance(self):
        frontend = self.make_frontend()
        self.assertEqual(frontend.feed(frame(.9), now=10).status, "wake")
        frontend.reset()
        self.assertIsNone(frontend.feed(frame(.9), now=11))
        self.assertFalse(frontend.active)
        self.assertEqual(frontend.feed(frame(.9), now=12.1).status, "wake")

    def test_unexpected_keyword_cannot_activate_a_turn(self):
        frontend = self.make_frontend()
        frontend.detector = Mock()
        frontend.detector.feed.return_value = SimpleNamespace(keyword="Hey Jarvis")
        with self.assertRaises(WakeError):
            frontend.feed(frame(.9), now=10)
        self.assertFalse(frontend.active)

    def test_command_text_removes_only_fixed_leading_wake_greeting(self):
        examples = {
            "Hey Aqua, open Chrome.": "open Chrome.",
            " Hey, Aqua! Yes, go ahead. ": "Yes, go ahead.",
            "Aqua, don't open Chrome.": "don't open Chrome.",
            "Hey Aqua.": "",
            "Tell me about Hey Aqua.": "Tell me about Hey Aqua.",
            "Aquarium facts": "Aquarium facts",
            "Don't open Chrome.": "Don't open Chrome.",
        }
        for raw, expected in examples.items():
            with self.subTest(raw=raw):
                self.assertEqual(handsfree.command_text(raw), expected)


class Microphone:
    """Future frames remain scripted; pause never replays processing audio."""
    def __init__(self):
        self.frames = deque()
        self.device_info = None
        self.paused = False
        self.closed = False
        self.pauses = self.resumes = self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def read_frame(self, **_options):
        self.reads += 1
        if not self.frames:
            raise KeyboardInterrupt
        item = self.frames.popleft()
        if isinstance(item, BaseException):
            raise item
        return item

    def pause(self):
        self.paused = True
        self.pauses += 1

    def resume(self):
        self.paused = False
        self.resumes += 1


def turn_frames():
    return [frame(.9), *[frame(.1) for _ in range(10)], *[frame() for _ in range(4)]]


def transcript(text):
    return Transcript(text, "en", .99, .32, -10, -20, 0, -.1, .01, 1)


def native_proposal(app):
    return [ChatEvent(done=True, tool_calls=({"function": {"name": "open_application", "arguments": {"app_id": app}}},))]


def native_reply(text="A concise answer."):
    return [ChatEvent(text), ChatEvent(done=True)]


class HandsfreeSessionTests(NoExternalEffects):
    def setUp(self):
        super().setUp()
        self.microphone = Microphone()
        self.detector = AcousticDetector()
        self.vad = AcousticVad()
        self.state = ActionState()
        self.console = Mock()
        self.console.poll.return_value = None
        self.recognizer = Mock()
        self.client = Mock()
        self.client.check_model.return_value = ModelInfo(DEFAULT_CHAT_MODEL, "test", "qwen3", "non_thinking")
        self.responses = deque()

        def respond(*_args, **_kwargs):
            self.assertTrue(self.microphone.paused)
            value = self.responses.popleft() if self.responses else native_reply()
            if isinstance(value, BaseException):
                raise value
            return iter(value)

        self.client.stream_chat.side_effect = respond
        patches = {
            "wake_factory": patch("aqua.handsfree.SherpaWakeDetector", return_value=self.detector),
            "vad_factory": patch("aqua.handsfree.StreamingVad", return_value=self.vad),
            "speech_factory": patch("aqua.handsfree.OfflineRecognizer", return_value=self.recognizer),
            "microphone_factory": patch("aqua.handsfree.MicrophoneStream", return_value=self.microphone),
            "console_factory": patch("aqua.handsfree.ConsoleCommands", return_value=self.console),
            "client_factory": patch("aqua.chat.OllamaClient", return_value=self.client),
            "state_factory": patch("aqua.handsfree.ActionState", return_value=self.state),
            "interpret": patch("aqua.actions.semantic.interpret_request", side_effect=AssertionError("No retired classifier calls")),
        }
        for name, manager in patches.items():
            setattr(self, name, manager.start())
            self.addCleanup(manager.stop)

    def invoke(self, utterances=(), *, frames=None, **options):
        self.microphone.frames = deque(frames if frames is not None else [item for _ in utterances for item in turn_frames()])
        responses = iter(transcript(text) if isinstance(text, str) else text for text in utterances)

        def recognize(*_args, **_kwargs):
            self.assertTrue(self.microphone.paused)
            value = next(responses)
            if isinstance(value, BaseException):
                raise value
            return value

        self.recognizer.transcribe.side_effect = recognize
        defaults = dict(end_silence=.128, initial_timeout=.512, max_utterance=1.024, pre_roll=.064, wake_cooldown=0)
        defaults.update(options)
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = handsfree.run_handsfree(**defaults)
        self.process_guard.assert_not_called()
        return status, output.getvalue(), errors.getvalue()

    def test_idle_ordinary_speech_never_reaches_whisper_ollama_or_classifier(self):
        status, output, errors = self.invoke(frames=[frame(.1) for _ in range(100)])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn('Listening for "Hey Aqua"', output)
        self.recognizer.transcribe.assert_not_called()
        self.client_factory.assert_not_called()
        self.interpret.assert_not_called()
        self.assertEqual(self.vad.calls, 0)
        self.assertTrue(self.microphone.closed)

    def test_one_stream_and_models_are_reused_and_conversation_retains_context(self):
        utterances = ["Hey Aqua, what is Formula One?", "Hey Aqua, my favorite drivers are Carlos Sainz and Max Verstappen.", "Hey Aqua, tell me more about them."]
        status, output, errors = self.invoke(utterances, language="en", hotwords="Aqua, Python", diagnostics=True)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.microphone_factory.assert_called_once_with(device=None, sample_rate=None)
        self.wake_factory.assert_called_once_with(None, threshold=.25, score=1)
        self.vad_factory.assert_called_once()
        self.speech_factory.assert_called_once_with()
        self.client.check_model.assert_called_once_with(DEFAULT_CHAT_MODEL)
        self.assertEqual(self.client.stream_chat.call_count, 3)
        messages = self.client.stream_chat.call_args.args[0]
        self.assertEqual([message["content"] for message in messages if message["role"] == "user"],
                         [handsfree.command_text(text) for text in utterances])
        self.assertIn("Wake detector:", output)
        self.assertIn("Speech transcription:", output)
        self.assertIn("Total interaction since wake detection:", output)
        for call in self.recognizer.transcribe.call_args_list:
            self.assertIsInstance(call.args[0], bytes)
            self.assertEqual(call.args[1], 16000)
            self.assertEqual(call.kwargs, {"gain_db": 0, "language": "en", "hotwords": "Aqua, Python"})
        self.assertTrue(self.microphone.closed)

    def test_approved_coding_request_uses_native_tools_and_fixed_launcher(self):
        self.responses.extend((native_proposal("vs code"), native_reply("The launch attempt succeeded.")))
        with patch("aqua.actions.launcher.subprocess.Popen") as launch:
            status, _output, errors = self.invoke(["Hey Aqua, bring up my coding environment."])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        launch.assert_called_once_with(list(ALLOWED_APPS["vs code"]), shell=False)
        self.assertEqual(self.client.stream_chat.call_args_list[0].args[0][-1]["content"], "bring up my coding environment.")
        self.assertEqual(self.client.stream_chat.call_args_list[0].kwargs["tools"], tool_definitions())
        self.assertEqual(self.client.stream_chat.call_args.args[0][-1]["role"], "tool")
        self.assertEqual(self.client.stream_chat.call_args.kwargs["tools"], [])
        self.assertEqual(self.client.stream_chat.call_count, 2)
        self.interpret.assert_not_called()

    def test_unapproved_and_compound_requests_never_launch_even_on_misclassification(self):
        self.responses.extend(native_proposal("chrome") for _ in range(5))
        status, _output, errors = self.invoke([
            "Hey Aqua, open PowerShell.", "Hey Aqua, open Chrome and delete my files.",
            "Hey Aqua, don't open Chrome.", "Hey Aqua, imagine opening Chrome.",
            'Hey Aqua, say "open Chrome".',
        ])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(self.client.stream_chat.call_count, 5)
        self.interpret.assert_not_called()
        self.assertIsNone(self.state.pending_app)

    def test_one_wake_turn_performs_authorized_sequential_operations(self):
        self.responses.extend((native_proposal("notepad"), native_proposal("calculator"),
                               native_reply("All applications are ready!")))
        with patch("aqua.actions.launcher.subprocess.Popen") as launch:
            status, output, errors = self.invoke(["Hey Aqua, open Notepad and Calculator."])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual([item.args[0] for item in launch.call_args_list],
                         [list(ALLOWED_APPS["notepad"]), list(ALLOWED_APPS["calculator"])])
        self.assertTrue(all(item.kwargs == {"shell": False} for item in launch.call_args_list))
        self.assertEqual(self.client.stream_chat.call_count, 3)
        self.recognizer.transcribe.assert_called_once()
        self.microphone_factory.assert_called_once_with(device=None, sample_rate=None)
        messages = self.client.stream_chat.call_args.args[0]
        self.assertEqual([row["content"] for row in messages if row["role"] == "user"],
                         ["open Notepad and Calculator."])
        self.assertEqual([row["role"] for row in messages[1:]],
                         ["user", "assistant", "tool", "assistant", "tool"])
        self.assertEqual(self.client.stream_chat.call_args.kwargs["tools"], [])
        self.assertIn("2 successful launch attempt(s)", output)
        self.assertNotIn("All applications are ready!", output)
        self.interpret.assert_not_called()

    def test_clarification_confirms_only_once_across_separate_wake_turns(self):
        self.responses.extend((native_proposal("vs code"), native_reply("The launch attempt succeeded.")))
        with patch("aqua.actions.launcher.subprocess.Popen") as launch:
            status, output, errors = self.invoke(["Hey Aqua, I feel like programming.", "Hey Aqua, yes, go ahead.", "Hey Aqua, yes, go ahead."])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("Would you like me to open vs code?", output)
        self.interpret.assert_not_called()
        self.assertEqual(self.client.stream_chat.call_count, 2)
        launch.assert_called_once_with(list(ALLOWED_APPS["vs code"]), shell=False)
        self.assertIsNone(self.state.pending_app)

    def test_one_wake_turn_reconsiders_premature_stop_with_same_context(self):
        self.responses.extend((native_proposal("notepad"),
                               native_reply("Would you like me to open Calculator now?"),
                               native_proposal("calculator"), native_reply("Done.")))
        with patch("aqua.actions.launcher.subprocess.Popen") as launch:
            status, output, errors = self.invoke(["Hey Aqua, open Notepad and Calculator."])
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual([item.args[0] for item in launch.call_args_list],
                         [list(ALLOWED_APPS["notepad"]), list(ALLOWED_APPS["calculator"])])
        self.assertEqual(self.client.stream_chat.call_count, 4)
        review = self.client.stream_chat.call_args_list[2]
        self.assertIn("Single bounded completeness review", review.args[0][0]["content"])
        self.assertEqual([m["content"] for m in review.args[0] if m["role"] == "user"],
                         ["open Notepad and Calculator."])
        self.assertEqual(review.args[0][-1]["role"], "tool")
        self.assertEqual(review.kwargs["settings"], self.client.stream_chat.call_args_list[0].kwargs["settings"])
        self.recognizer.transcribe.assert_called_once()
        self.assertIn("2 successful launch attempt(s)", output)
        self.assertIsNone(self.state.pending_app)

    def test_unrelated_awakened_conversation_disarms_late_confirmation(self):
        self.responses.extend((native_proposal("vs code"), native_reply("Formula One is a motorsport.")))
        status, _output, errors = self.invoke([
            "Hey Aqua, I feel like programming.", "Hey Aqua, what is Formula One?", "Hey Aqua, yes go ahead.",
        ])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(self.client.stream_chat.call_count, 2)
        self.assertIsNone(self.state.pending_app)

    def test_clear_control_disarms_pending_and_forgets_conversation(self):
        self.responses.extend((native_reply("I remember teal."), native_proposal("vs code"), native_reply("I do not have a retained color.")))
        cleared = False

        def poll():
            nonlocal cleared
            if self.recognizer.transcribe.call_count == 2 and not cleared:
                cleared = True
                return "/clear"
            return None

        self.console.poll.side_effect = poll
        status, output, errors = self.invoke(["Hey Aqua, remember teal.", "Hey Aqua, I feel like programming.", "Hey Aqua, yes go ahead.", "Hey Aqua, which color?"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("Conversation cleared", output)
        self.assertIsNone(self.state.pending_app)
        messages = self.client.stream_chat.call_args.args[0]
        self.assertEqual([message["content"] for message in messages if message["role"] == "user"], ["yes go ahead.", "which color?"])
        self.assertFalse(any(message["role"] == "tool" for message in messages))
        self.assertFalse(any("remember teal" in message["content"].lower() for message in messages[1:]))
        self.assertEqual(self.client.stream_chat.call_count, 3)

    def test_no_speech_or_maximum_capture_skips_transcription_and_clears_pending(self):
        for after_wake, label in (([frame() for _ in range(16)], "no meaningful speech"),
                                  ([frame(.1) for _ in range(32)], "maximum utterance")):
            with self.subTest(label=label):
                self.state.pending_app = "vs code"
                self.recognizer.transcribe.reset_mock()
                status, output, errors = self.invoke(frames=[frame(.9), *after_wake])
                self.assertEqual(status, 0)
                self.assertEqual(errors, "")
                self.assertIn(label, output)
                self.recognizer.transcribe.assert_not_called()
                self.client_factory.assert_not_called()
                self.interpret.assert_not_called()
                self.assertIsNone(self.state.pending_app)

    def test_empty_wake_only_transcript_never_reaches_ollama(self):
        self.state.pending_app = "vs code"
        status, output, errors = self.invoke(["Hey Aqua."])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("Skipped:", output)
        self.client_factory.assert_not_called()
        self.interpret.assert_not_called()
        self.assertIsNone(self.state.pending_app)

    def test_transcription_and_native_model_errors_clear_pending_safely(self):
        self.responses.extend((native_proposal("vs code"), OllamaError("timeout")))
        status, output, errors = self.invoke(["Hey Aqua, I feel like programming.", SpeechError("recognizer failed"), "Hey Aqua, open Chrome.", "Hey Aqua, yes go ahead."])
        self.assertEqual(status, 0)
        self.assertIn("recognizer failed", errors)
        self.assertIn("Task result: No tool operations were performed.", output)
        self.assertIn("Not attempted: chrome.", output)
        self.assertIn("No matching application is awaiting confirmation.", output)
        self.assertEqual(self.client.stream_chat.call_count, 2)
        self.assertIsNone(self.state.pending_app)

    def test_ctrl_c_during_capture_cancels_only_turn_then_returns_to_idle(self):
        self.state.pending_app = "vs code"
        status, output, errors = self.invoke(frames=[frame(.9), frame(.1), KeyboardInterrupt(), frame(.1)])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("Voice turn cancelled", output)
        self.recognizer.transcribe.assert_not_called()
        self.client_factory.assert_not_called()
        self.assertIsNone(self.state.pending_app)
        self.assertGreaterEqual(self.microphone.resumes, 1)
        self.assertTrue(self.microphone.closed)

    def test_microphone_disconnect_returns_error_and_releases_stream(self):
        self.state.pending_app = "vs code"
        status, _output, errors = self.invoke(frames=[AudioError("microphone disconnected")])
        self.assertEqual(status, 1)
        self.assertIn("microphone disconnected", errors)
        self.assertIsNone(self.state.pending_app)
        self.assertTrue(self.microphone.closed)
        self.client_factory.assert_not_called()

    def test_quit_control_exits_without_capture_or_ollama(self):
        self.console.poll.return_value = "/quit"
        status, _output, errors = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(self.microphone.reads, 0)
        self.recognizer.transcribe.assert_not_called()
        self.client_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
