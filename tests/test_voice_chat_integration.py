"""Spoken replies in chat sessions and CLI flags, with fake speech only."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import Mock, call, patch

from aqua import chat, cli
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, ChatEvent, GenerationSettings, ModelInfo
from aqua.voice.engines import FakeEngine
from aqua.voice.output import SpeechOutput
from aqua.voice.player import FakePlayer


def reply(*chunks):
    return iter([*(ChatEvent(chunk) for chunk in chunks), ChatEvent(done=True)])


class Offline(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.getaddrinfo", "socket.socket.connect", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Voice chat tests must stay offline."))
            manager.start()
            self.addCleanup(manager.stop)
        self.client = Mock()
        self.client.check_model.return_value = ModelInfo(DEFAULT_CHAT_MODEL, "local-test", "qwen3", "non_thinking")
        manager = patch("aqua.actions.semantic.interpret_request", side_effect=AssertionError("No classifier"))
        manager.start()
        self.addCleanup(manager.stop)

    def respond(self, speaker, chunks, *, diagnostics=False):
        self.client.stream_chat.side_effect = lambda *_a, **_k: reply(*chunks)
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(StringIO()):
            ok = chat._respond(self.client, Conversation(), "Question", model="m",
                               settings=GenerationSettings(), diagnostics=diagnostics, speaker=speaker)
        return ok, output.getvalue()


class RespondSpeakerTests(Offline):
    def fake_speaker(self):
        self.engine, self.player = FakeEngine(), FakePlayer()
        speaker = SpeechOutput(self.engine, self.player, warn=lambda _m: None)
        self.addCleanup(speaker.close)
        return speaker

    def test_text_is_identical_with_and_without_speaker_and_is_spoken(self):
        chunks = ("Formula One is a ", "motorsport. It races **worldwide**.", " Want details?")
        _ok, plain = self.respond(None, chunks)
        speaker = self.fake_speaker()
        ok, spoken = self.respond(speaker, chunks)
        self.assertTrue(ok)
        self.assertEqual(spoken, plain)
        self.assertTrue(speaker.wait(5))
        self.assertEqual(self.engine.texts, ["Formula One is a motorsport.", "It races worldwide.", "Want details?"])

    def test_new_turn_stops_previous_speech_first_and_finishes_turn(self):
        speaker = Mock()
        self.respond(speaker, ("Hi there.",))
        names = [entry[0] for entry in speaker.method_calls]
        self.assertEqual(names[0], "stop")
        self.assertEqual(names[-1], "finish_turn")
        self.assertIn(call.feed("Hi there."), speaker.method_calls)

    def test_speaker_failure_never_changes_text_or_result(self):
        warnings = []
        speaker = SpeechOutput(FakeEngine(fail=True), FakePlayer(), warn=warnings.append)
        self.addCleanup(speaker.close)
        ok, text = self.respond(speaker, ("One. Two. Three. Four.",))
        self.assertTrue(ok)
        self.assertIn("One. Two. Three. Four.", text)
        speaker.wait(2)
        self.assertEqual(len(warnings), 1)

    def test_diagnostics_print_speech_timings(self):
        speaker = self.fake_speaker()
        _ok, text = self.respond(speaker, ("Short answer.",), diagnostics=True)
        self.assertIn("Speech output: first audio", text)
        self.assertIn("sentences spoken 1", text)


class SessionSpeakerTests(Offline):
    def run_chat(self, inputs, **options):
        output, errors = StringIO(), StringIO()
        with patch("aqua.chat.OllamaClient", return_value=self.client), \
                patch("builtins.input", side_effect=inputs), redirect_stdout(output), redirect_stderr(errors):
            status = chat.run_text_chat(**options)
        return status, output.getvalue(), errors.getvalue()

    def test_text_chat_is_silent_by_default(self):
        self.client.stream_chat.side_effect = lambda *_a, **_k: reply("Hello.")
        with patch("aqua.voice.output.open_speaker") as opener:
            status, _out, _err = self.run_chat(["Hi", "/quit"])
        self.assertEqual(status, 0)
        opener.assert_not_called()

    def test_speak_loads_once_feeds_each_turn_and_closes(self):
        self.client.stream_chat.side_effect = lambda *_a, **_k: reply("Hello.")
        speaker = Mock()
        speaker.timings = None
        with patch("aqua.voice.output.open_speaker", return_value=speaker) as opener:
            status, _out, _err = self.run_chat(["Hi", "Again", "/quit"], speak=True, tts_engine="piper",
                                               voice="0", voice_model_dir=Path("D:/v"), speech_rate=1.25)
        self.assertEqual(status, 0)
        opener.assert_called_once_with(engine="piper", voice="0", model_dir=Path("D:/v"), rate=1.25)
        self.assertEqual(speaker.finish_turn.call_count, 2)
        speaker.close.assert_called_once()

    def test_missing_voice_prints_one_note_and_continues_text_only(self):
        self.client.stream_chat.side_effect = lambda *_a, **_k: reply("Hello there.")
        status, output, errors = self.run_chat(["Hi", "/quit"], speak=True,
                                               voice_model_dir=Path(r"D:\definitely\absent\voice"))
        self.assertEqual(status, 0)
        self.assertEqual(output.count("speech output unavailable"), 1)
        self.assertIn("aqua setup-voice --download", output)
        self.assertIn("Hello there.", output)
        self.assertEqual(errors, "")


class VoiceCliTests(unittest.TestCase):
    def invoke(self, arguments, target):
        with patch(f"aqua.cli.{target}", return_value=0) as run, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            status = cli.main(arguments)
        self.assertEqual(status, 0)
        return run.call_args.kwargs

    def test_defaults_speak_only_in_handsfree(self):
        for command, target, expected in (("chat", "run_text_chat", False), ("voice-chat", "run_voice_chat", False),
                                          ("handsfree", "run_handsfree", True)):
            with self.subTest(command=command):
                options = self.invoke([command], target)
                self.assertIs(options["speak"], expected)
                self.assertEqual(options["tts_engine"], "kokoro")
                self.assertIsNone(options["voice"])
                self.assertIsNone(options["voice_model_dir"])
                self.assertEqual(options["speech_rate"], 1.0)

    def test_speak_flags_and_voice_options_forward(self):
        self.assertTrue(self.invoke(["chat", "--speak"], "run_text_chat")["speak"])
        self.assertTrue(self.invoke(["voice-chat", "--speak"], "run_voice_chat")["speak"])
        options = self.invoke(["handsfree", "--no-speak", "--tts-engine", "kitten", "--voice", "expr-voice-2-m",
                               "--voice-model-dir", "D:/voices/k", "--speech-rate", "1.5"], "run_handsfree")
        self.assertFalse(options["speak"])
        self.assertEqual((options["tts_engine"], options["voice"], options["speech_rate"]),
                         ("kitten", "expr-voice-2-m", 1.5))
        self.assertEqual(options["voice_model_dir"], Path("D:/voices/k"))

    def test_invalid_voice_options_fail_before_runner(self):
        for arguments in (["chat", "--tts-engine", "espeak"], ["chat", "--speech-rate", "3"],
                          ["chat", "--speech-rate", "nan"], ["chat", "--speak", "--no-speak"]):
            with self.subTest(arguments=arguments), patch("aqua.cli.run_text_chat") as run, \
                    redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaises(SystemExit) as exit_:
                cli.main(arguments)
            self.assertEqual(exit_.exception.code, 2)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
