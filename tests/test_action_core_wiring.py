"""--engine wiring: CLI flag, text chat, voice front ends and the hands-free follow-up window."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import Mock, patch

from aqua import chat, cli, handsfree
from aqua.actions.tools import ActionState
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, ModelInfo
from aqua.speech.recognizer import Transcript

from action_core_helpers import call, make_engine
from test_voice_handsfree import TAIL, Detector, Microphone, Vad, frame, utterance, wake_turn


class Offline(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Wiring tests must stay offline."))
            manager.start()
            self.addCleanup(manager.stop)
        self.client = Mock()
        self.client.check_model.return_value = ModelInfo(DEFAULT_CHAT_MODEL, "test", "qwen3", "non_thinking")
        self.client.stream_chat.side_effect = AssertionError("The legacy controller must not run with --engine new.")
        manager = patch("aqua.chat.OllamaClient", return_value=self.client)
        manager.start()
        self.addCleanup(manager.stop)

    def use_engine(self, engine):
        manager = patch("aqua.agent.loop.build_engine", return_value=engine)
        self.build = manager.start()
        self.addCleanup(manager.stop)


class CliEngineFlagTests(unittest.TestCase):
    def invoke(self, argv, target):
        with patch(f"aqua.cli.{target}", return_value=0) as run, redirect_stdout(StringIO()), \
                redirect_stderr(StringIO()) as errors:
            try:
                status = cli.main(argv)
            except SystemExit as exc:
                status = exc.code
        return status, run, errors.getvalue()

    def test_engine_new_is_passed_to_every_conversation_command(self):
        for command, target in (("chat", "run_text_chat"), ("voice-chat", "run_voice_chat"), ("handsfree", "run_handsfree")):
            status, run, _ = self.invoke([command, "--engine", "new"], target)
            self.assertEqual(status, 0, command)
            self.assertEqual(run.call_args.kwargs["engine"], "new", command)

    def test_default_is_legacy_and_adds_no_option(self):
        for command, target in (("chat", "run_text_chat"), ("voice-chat", "run_voice_chat"), ("handsfree", "run_handsfree")):
            status, run, _ = self.invoke([command], target)
            self.assertEqual(status, 0)
            self.assertNotIn("engine", run.call_args.kwargs)
            status, run, _ = self.invoke([command, "--engine", "legacy"], target)
            self.assertNotIn("engine", run.call_args.kwargs)

    def test_unknown_engine_is_rejected(self):
        status, run, errors = self.invoke(["chat", "--engine", "turbo"], "run_text_chat")
        self.assertEqual(status, 2)
        run.assert_not_called()
        self.assertIn("invalid choice", errors)


class TextChatEngineTests(Offline):
    def invoke(self, inputs, **options):
        output, errors = StringIO(), StringIO()
        with patch("builtins.input", side_effect=inputs), redirect_stdout(output), redirect_stderr(errors):
            status = chat.run_text_chat(**options)
        return status, output.getvalue(), errors.getvalue()

    def test_new_engine_handles_text_turns(self):
        engine, desktop, model = make_engine(
            [call("launch_app", app="Notepad")], [call("type_text", text="Hello I am Aqua")],
            "Opened Notepad and typed your text.")
        self.use_engine(engine)
        status, output, errors = self.invoke(["Open Notepad and write Hello I am Aqua", "/quit"], engine="new")
        self.assertEqual((status, errors), (0, ""))
        self.assertIn("Using the new action engine", output)
        self.assertIn("Aqua: Opened Notepad and typed your text.", output)
        self.assertEqual(list(desktop.text.values()), ["Hello I am Aqua"])
        self.client.stream_chat.assert_not_called()

    def test_clear_cancels_a_pending_confirmation(self):
        engine, desktop, _ = make_engine([call("power", action="shutdown")], "Okay.")
        self.use_engine(engine)
        status, output, _ = self.invoke(["shut down", "/clear", "yes", "/quit"], engine="new")
        self.assertEqual(status, 0)
        self.assertIn("Shut down the computer? Say yes or no.", output)
        self.assertEqual(desktop.power, [])

    def test_engine_setup_failure_is_reported(self):
        manager = patch("aqua.agent.loop.build_engine", side_effect=OSError("The Windows desktop adapter needs Windows."))
        manager.start()
        self.addCleanup(manager.stop)
        status, _output, errors = self.invoke([], engine="new")
        self.assertEqual(status, 1)
        self.assertIn("Action engine setup error", errors)

    def test_diagnostics_list_the_steps(self):
        engine, _, _ = make_engine([call("volume", action="mute")], "Muted.")
        self.use_engine(engine)
        _status, output, _ = self.invoke(["mute", "/quit"], engine="new", diagnostics=True)
        self.assertIn("Engine: new; chat requests 2; outcome action", output)
        self.assertIn("volume=ok", output)


class SpeakerTests(unittest.TestCase):
    def test_speaker_hears_exactly_the_printed_reply(self):
        engine, _, _ = make_engine("Hello there, how can I help?")
        speaker = Mock()
        output = StringIO()
        with redirect_stdout(output):
            result = chat._respond_agent(engine, "hi", diagnostics=False, speaker=speaker)
        speaker.stop.assert_called_once()
        speaker.finish_turn.assert_called_once()
        spoken = "".join(c.args[0] for c in speaker.feed.call_args_list)
        self.assertEqual(spoken, result.text)
        self.assertIn("Aqua: " + spoken, output.getvalue())


class HandsfreeFollowUpTests(Offline):
    def setUp(self):
        super().setUp()
        self.recognizer = Mock()
        console = Mock()
        console.poll.return_value = None
        for target, value in {"aqua.handsfree.SherpaWakeDetector": Detector(), "aqua.handsfree.StreamingVad": Vad(),
                              "aqua.handsfree.OfflineRecognizer": self.recognizer,
                              "aqua.handsfree.ConsoleCommands": console}.items():
            manager = patch(target, return_value=value)
            manager.start()
            self.addCleanup(manager.stop)

    def invoke(self, items, texts):
        texts = iter(texts)
        self.recognizer.transcribe.side_effect = lambda *_a, **_k: Transcript(next(texts), "en", .99, .32, -10, -20, 0, -.1, .01, 1)
        output, errors = StringIO(), StringIO()
        with patch("aqua.handsfree.MicrophoneStream", return_value=Microphone(items)), \
                patch("aqua.chat._open_speaker", return_value=None), redirect_stdout(output), redirect_stderr(errors):
            status = handsfree.run_handsfree(end_silence=.128, initial_timeout=.512, max_utterance=1.024,
                                             pre_roll=.064, wake_cooldown=0, speak=False, engine="new")
        return status, output.getvalue(), errors.getvalue()

    def test_yes_without_the_wake_word_confirms_the_pending_action(self):
        engine, desktop, _ = make_engine([call("power", action="sleep")], "Going to sleep now.")
        self.use_engine(engine)
        items = [*wake_turn(), *[frame(TAIL) for _ in range(handsfree.FOLLOW_UP_TAIL_FRAMES)], *utterance()]
        status, output, errors = self.invoke(items, ["Hey Aqua, put the computer to sleep.", "Yes."])
        self.assertEqual((status, errors), (0, ""))
        self.assertIn("Put the computer to sleep? Say yes or no.", output)
        self.assertIn("Listening for your answer...", output)
        self.assertIn("Going to sleep now.", output)
        self.assertEqual(desktop.power, [("sleep", 0)])

    def test_plain_action_reply_opens_no_follow_up(self):
        engine, desktop, _ = make_engine([call("volume", action="mute")], "Muted.")
        self.use_engine(engine)
        items = [*wake_turn(), *[frame() for _ in range(handsfree.FOLLOW_UP_TAIL_FRAMES + 10)], *utterance()]
        status, output, _ = self.invoke(items, ["Hey Aqua, mute."])
        self.assertEqual(status, 0)
        self.assertTrue(desktop.muted)
        self.assertNotIn("Listening for your answer", output)

    def test_asked_question_checks_the_new_engine(self):
        engine, _, _ = make_engine([call("power", action="shutdown")])
        state = ActionState()
        self.assertFalse(handsfree._asked_question(Mock(text="Done."), state, engine))
        engine.respond("shut down")
        self.assertTrue(handsfree._asked_question(Mock(text="Say yes or no."), state, engine))
        self.assertTrue(handsfree._asked_question(Mock(text="Which app should I type that into?"), state, None))


if __name__ == "__main__":
    unittest.main()
