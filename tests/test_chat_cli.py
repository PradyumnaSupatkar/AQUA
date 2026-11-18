"""Chat CLI routing and validation without models, microphone, or network."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch

from aqua.cli import main
from aqua.brain.ollama import DEFAULT_CHAT_MODEL
from aqua.speech.models import DEFAULT_MODEL_DIR, MODEL_DIRS
from aqua.speech.vocabulary import DEFAULT_HOTWORDS


class ChatCliTests(unittest.TestCase):
    def invoke(self, arguments):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = main(arguments)
        return status, output.getvalue(), errors.getvalue()

    def test_text_defaults_select_fast_local_qwen_and_bounded_context(self):
        with patch("aqua.cli.run_text_chat", return_value=0) as run, patch(
            "aqua.cli.run_voice_chat"
        ) as voice, patch("aqua.cli.OfflineRecognizer") as speech, patch(
            "aqua.cli.record_test"
        ) as capture:
            status, output, errors = self.invoke(["chat"])
        self.assertEqual(status, 0)
        self.assertEqual(output, "")
        self.assertEqual(errors, "")
        options = run.call_args.kwargs
        self.assertEqual(options["model"], DEFAULT_CHAT_MODEL)
        self.assertEqual(options["settings"].temperature, 0.5)
        self.assertEqual(options["settings"].num_predict, 1024)
        self.assertEqual(options["settings"].num_ctx, 4096)
        self.assertEqual(options["timeout"], 120.0)
        self.assertEqual(options["max_turns"], 8)
        self.assertEqual(options["max_chars"], 12000)
        self.assertFalse(options["diagnostics"])
        run.assert_called_once()
        voice.assert_not_called()
        speech.assert_not_called()
        capture.assert_not_called()

    def test_explicit_generation_and_history_settings_are_forwarded(self):
        with patch("aqua.cli.run_text_chat", return_value=0) as run:
            status, _output, errors = self.invoke([
                "chat", "--model", "local-model:tag", "--temperature", "0.1",
                "--max-tokens", "1024", "--context-size", "8192", "--timeout", "180",
                "--max-turns", "4", "--history-chars", "6000", "--diagnostics",
            ])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        options = run.call_args.kwargs
        self.assertEqual(options["model"], "local-model:tag")
        self.assertEqual(options["settings"].temperature, 0.1)
        self.assertEqual(options["settings"].num_predict, 1024)
        self.assertEqual(options["settings"].num_ctx, 8192)
        self.assertEqual(options["timeout"], 180)
        self.assertEqual(options["max_turns"], 4)
        self.assertEqual(options["max_chars"], 6000)
        self.assertTrue(options["diagnostics"])

    def test_voice_defaults_are_small_default_microphone_and_no_gain_or_hints(self):
        with patch("aqua.cli.run_voice_chat", return_value=0) as run, patch(
            "aqua.cli.run_text_chat"
        ) as text:
            status, _output, errors = self.invoke(["voice-chat"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        options = run.call_args.kwargs
        self.assertEqual(options["model"], DEFAULT_CHAT_MODEL)
        self.assertEqual(options["speech_model_dir"], DEFAULT_MODEL_DIR)
        self.assertEqual(options["speech_model_dir"], MODEL_DIRS["small"])
        self.assertIsNone(options["device"])
        self.assertIsNone(options["sample_rate"])
        self.assertEqual(options["gain_db"], 0.0)
        self.assertIsNone(options["language"])
        self.assertIsNone(options["hotwords"])
        self.assertEqual(options["timeout"], 120)
        self.assertEqual(options["settings"].num_predict, 1024)
        run.assert_called_once()
        text.assert_not_called()

    def test_voice_speech_model_and_llm_model_are_independent(self):
        with patch("aqua.cli.run_voice_chat", return_value=0) as run:
            status, _output, errors = self.invoke([
                "voice-chat", "--model", "other:local", "--speech-model", "base",
                "--device", "7", "--sample-rate", "48000", "--gain-db", "-3",
                "--language", "en", "--diagnostics",
            ])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        options = run.call_args.kwargs
        self.assertEqual(options["model"], "other:local")
        self.assertEqual(options["speech_model_dir"], MODEL_DIRS["base"])
        self.assertEqual(options["device"], 7)
        self.assertEqual(options["sample_rate"], 48000)
        self.assertEqual(options["gain_db"], -3)
        self.assertEqual(options["language"], "en")
        self.assertTrue(options["diagnostics"])

    def test_custom_local_speech_directory_is_preserved(self):
        custom = Path("D:/Aqua/models/custom-whisper")
        with patch("aqua.cli.run_voice_chat", return_value=0) as run:
            status, _output, errors = self.invoke([
                "voice-chat", "--speech-model-dir", str(custom),
            ])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(run.call_args.kwargs["speech_model_dir"], custom)

    def test_speech_hint_flags_select_only_recognition_hotwords(self):
        cases = [
            (["--hints"], DEFAULT_HOTWORDS),
            (["--no-hints"], None),
            (["--hints", "--hotwords", "  Aqua,\n FastAPI  "], "Aqua, FastAPI"),
        ]
        for options, expected in cases:
            with self.subTest(options=options), patch("aqua.cli.run_voice_chat", return_value=0) as run:
                status, _output, errors = self.invoke(["voice-chat", *options])
            self.assertEqual(status, 0)
            self.assertEqual(errors, "")
            self.assertEqual(run.call_args.kwargs["hotwords"], expected)
            self.assertEqual(run.call_args.kwargs["model"], DEFAULT_CHAT_MODEL)

    def test_invalid_hint_and_speech_selection_combinations_prevent_runners(self):
        cases = [
            ["--hints", "--no-hints"],
            ["--hotwords", "Aqua"],
            ["--no-hints", "--hotwords", "Aqua"],
            ["--hints", "--hotwords", " \n\t "],
            ["--speech-model", "small", "--speech-model-dir", "local/model"],
            ["--speech-model", "base", "--speech-model-dir", "local/model"],
        ]
        with patch("aqua.cli.run_text_chat") as text, patch("aqua.cli.run_voice_chat") as voice:
            for options in cases:
                with self.subTest(options=options):
                    self.assert_invalid(["voice-chat", *options])
        text.assert_not_called()
        voice.assert_not_called()

    def test_invalid_numeric_settings_prevent_runners(self):
        cases = [
            ["--temperature", "nan"], ["--temperature", "inf"],
            ["--temperature", "-0.1"], ["--temperature", "2.1"],
            ["--max-tokens", "0"], ["--max-tokens", "4097"],
            ["--context-size", "511"], ["--context-size", "32769"],
            ["--max-turns", "0"], ["--history-chars", "0"],
            ["--timeout", "nan"], ["--timeout", "inf"],
            ["--timeout", "0"], ["--timeout", "601"],
        ]
        with patch("aqua.cli.run_text_chat") as text, patch("aqua.cli.run_voice_chat") as voice:
            for command in ("chat", "voice-chat"):
                for options in cases:
                    with self.subTest(command=command, options=options):
                        self.assert_invalid([command, *options])
        text.assert_not_called()
        voice.assert_not_called()

    def test_invalid_voice_audio_settings_prevent_runners(self):
        cases = [
            ["--device", "-1"], ["--sample-rate", "0"],
            ["--gain-db", "nan"], ["--gain-db", "31"],
        ]
        with patch("aqua.cli.run_voice_chat") as run:
            for options in cases:
                with self.subTest(options=options):
                    self.assert_invalid(["voice-chat", *options])
        run.assert_not_called()

    def test_remote_host_option_is_not_exposed(self):
        with patch("aqua.cli.run_text_chat") as text, patch("aqua.cli.run_voice_chat") as voice:
            for command in ("chat", "voice-chat"):
                with self.subTest(command=command):
                    self.assert_invalid([command, "--host", "https://remote.example"])
        text.assert_not_called()
        voice.assert_not_called()

    def test_runners_return_status_is_relayed(self):
        for command, function in (("chat", "run_text_chat"), ("voice-chat", "run_voice_chat")):
            for result in (0, 1, 130):
                with self.subTest(command=command, status=result), patch(f"aqua.cli.{function}", return_value=result):
                    status, _output, errors = self.invoke([command])
                    self.assertEqual(status, result)
                    self.assertEqual(errors, "")

    def assert_invalid(self, arguments):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as errors:
            with self.assertRaises(SystemExit) as exited:
                main(arguments)
        self.assertEqual(exited.exception.code, 2)
        self.assertIn("error:", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
