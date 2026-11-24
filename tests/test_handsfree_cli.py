"""Hands-free CLI wiring and validation, without wake assets or hardware."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aqua import cli
from aqua.brain.ollama import DEFAULT_CHAT_MODEL
from aqua.speech.models import DEFAULT_MODEL_DIR, MODEL_DIRS
from aqua.speech.vocabulary import DEFAULT_HOTWORDS
from aqua.wake.models import WakeError


class HandsfreeCliTests(unittest.TestCase):
    def setUp(self):
        # These tests cover Whisper; the CLI default recogniser is Parakeet.
        _asr = patch("aqua.cli.DEFAULT_ASR", "whisper")
        _asr.start()
        self.addCleanup(_asr.stop)
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("CLI tests must not perform external effects."))
            manager.start()
            self.addCleanup(manager.stop)

    def invoke(self, arguments):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = cli.main(arguments)
        return status, output.getvalue(), errors.getvalue()

    def test_defaults_preserve_fast_qwen_small_and_current_input(self):
        with patch("aqua.cli.run_handsfree", return_value=0) as run, \
                patch("aqua.cli.run_voice_chat") as manual, \
                patch("aqua.cli.record_test") as capture:
            status, output, errors = self.invoke(["handsfree"])
        self.assertEqual(status, 0)
        self.assertEqual(output + errors, "")
        manual.assert_not_called()
        capture.assert_not_called()
        options = run.call_args.kwargs
        self.assertEqual(options["model"], DEFAULT_CHAT_MODEL)
        self.assertEqual(options["settings"].num_ctx, 4096)
        self.assertEqual(options["settings"].temperature, .2)
        self.assertEqual(options["speech_model_dir"], DEFAULT_MODEL_DIR)
        self.assertIsNone(options["device"])
        self.assertIsNone(options["sample_rate"])
        self.assertIsNone(options["hotwords"])
        self.assertEqual(options["gain_db"], 0)
        self.assertIsNone(options["wake_model_dir"])
        self.assertEqual(options["wake_threshold"], .25)
        self.assertEqual(options["wake_score"], 1)
        self.assertEqual(options["wake_cooldown"], 2)
        self.assertEqual(options["end_silence"], .8)
        self.assertEqual(options["initial_timeout"], 5)
        self.assertEqual(options["max_utterance"], 15)
        self.assertEqual(options["pre_roll"], .5)
        self.assertEqual(options["vad_threshold"], .5)

    def test_explicit_audio_wake_vad_and_generation_settings_are_forwarded(self):
        wake_path = Path("D:/Aqua/models/custom-kws")
        with patch("aqua.cli.run_handsfree", return_value=0) as run:
            status, _output, errors = self.invoke([
                "handsfree", "--model", "other:local", "--speech-model", "base",
                "--device", "3", "--sample-rate", "44100", "--gain-db", "-2",
                "--language", "en", "--hints", "--diagnostics", "--wake-model-dir", str(wake_path),
                "--wake-threshold", ".4", "--wake-score", "1.5", "--wake-cooldown", "3",
                "--end-silence", "1.1", "--initial-timeout", "6", "--max-utterance", "20",
                "--pre-roll", ".7", "--vad-threshold", ".6", "--context-size", "2048",
                "--max-turns", "4", "--history-chars", "6000", "--timeout", "90",
            ])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        options = run.call_args.kwargs
        self.assertEqual(options["model"], "other:local")
        self.assertEqual(options["speech_model_dir"], MODEL_DIRS["base"])
        self.assertEqual(options["device"], 3)
        self.assertEqual(options["sample_rate"], 44100)
        self.assertEqual(options["gain_db"], -2)
        self.assertEqual(options["language"], "en")
        self.assertEqual(options["hotwords"], DEFAULT_HOTWORDS)
        self.assertTrue(options["diagnostics"])
        self.assertEqual(options["wake_model_dir"], wake_path)
        self.assertEqual(options["wake_threshold"], .4)
        self.assertEqual(options["wake_score"], 1.5)
        self.assertEqual(options["wake_cooldown"], 3)
        self.assertEqual(options["end_silence"], 1.1)
        self.assertEqual(options["initial_timeout"], 6)
        self.assertEqual(options["max_utterance"], 20)
        self.assertEqual(options["pre_roll"], .7)
        self.assertEqual(options["vad_threshold"], .6)
        self.assertEqual(options["settings"].num_ctx, 2048)
        self.assertEqual(options["max_turns"], 4)
        self.assertEqual(options["max_chars"], 6000)
        self.assertEqual(options["timeout"], 90)

    def test_custom_whisper_path_and_hint_text_remain_independent(self):
        custom = Path("D:/Aqua/models/custom-whisper")
        with patch("aqua.cli.run_handsfree", return_value=0) as run:
            status, _output, errors = self.invoke([
                "handsfree", "--speech-model-dir", str(custom), "--hints", "--hotwords", " Aqua,   Python ",
            ])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(run.call_args.kwargs["speech_model_dir"], custom)
        self.assertEqual(run.call_args.kwargs["hotwords"], "Aqua, Python")
        self.assertIsNone(run.call_args.kwargs["wake_model_dir"])

    def test_invalid_detector_and_audio_settings_fail_before_runner(self):
        invalid = (
            ("--wake-threshold", "-.1"), ("--wake-threshold", "1.1"),
            ("--wake-score", "0"), ("--wake-score", "11"),
            ("--wake-cooldown", "-1"), ("--wake-cooldown", "31"),
            ("--end-silence", ".09"), ("--end-silence", "3.1"),
            ("--initial-timeout", ".4"), ("--initial-timeout", "15.1"),
            ("--max-utterance", ".9"), ("--max-utterance", "31"),
            ("--pre-roll", "-1"), ("--pre-roll", "2.1"),
            ("--vad-threshold", "0"), ("--vad-threshold", "1.1"),
            ("--device", "-1"), ("--sample-rate", "0"), ("--gain-db", "31"),
            ("--context-size", "511"), ("--temperature", "nan"),
            *((flag, value) for flag in ("--wake-threshold", "--wake-score", "--wake-cooldown",
                                        "--end-silence", "--initial-timeout", "--max-utterance",
                                        "--pre-roll", "--vad-threshold") for value in ("nan", "inf")),
        )
        with patch("aqua.cli.run_handsfree") as run:
            for options in invalid:
                with self.subTest(options=options):
                    self.assert_invalid(["handsfree", *options])
        run.assert_not_called()

    def test_hint_and_model_selection_validation_matches_manual_voice(self):
        invalid = (
            ["--hotwords", "Aqua"], ["--hints", "--no-hints"],
            ["--hints", "--hotwords", " \n\t "],
            ["--speech-model", "small", "--speech-model-dir", "local"],
            ["--host", "https://remote.invalid"], ["--download"],
        )
        with patch("aqua.cli.run_handsfree") as run:
            for options in invalid:
                with self.subTest(options=options):
                    self.assert_invalid(["handsfree", *options])
        run.assert_not_called()

    def test_existing_manual_voice_route_receives_no_wake_options(self):
        with patch("aqua.cli.run_voice_chat", return_value=0) as manual, \
                patch("aqua.cli.run_handsfree") as automatic:
            status, _output, errors = self.invoke(["voice-chat", "--language", "en", "--hints"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        automatic.assert_not_called()
        self.assertFalse(any(name.startswith("wake_") for name in manual.call_args.kwargs))
        self.assertNotIn("end_silence", manual.call_args.kwargs)
        self.assertEqual(manual.call_args.kwargs["hotwords"], DEFAULT_HOTWORDS)

    def test_runner_result_is_relayed(self):
        for expected in (0, 1, 130):
            with self.subTest(expected=expected), patch("aqua.cli.run_handsfree", return_value=expected):
                status, _output, errors = self.invoke(["handsfree"])
                self.assertEqual(status, expected)
                self.assertEqual(errors, "")

    def test_wake_setup_is_verify_only_without_download_flag(self):
        installed = Path("D:/Aqua/models/sherpa-kws-en-int8")
        with patch("aqua.cli.setup_wake_model", return_value=installed) as setup, \
                patch("aqua.cli.run_handsfree") as run:
            status, output, errors = self.invoke(["setup-wake"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("Offline Hey Aqua", output)
        setup.assert_called_once_with(None, download=False)
        run.assert_not_called()

    def test_explicit_setup_download_forwards_only_selected_wake_path(self):
        chosen = Path("D:/Aqua/models/wake-copy")
        with patch("aqua.cli.setup_wake_model", return_value=chosen) as setup:
            status, _output, errors = self.invoke(["setup-wake", "--download", "--model-dir", str(chosen)])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        setup.assert_called_once_with(chosen, download=True)

    def test_missing_wake_assets_produce_helpful_error_without_runner(self):
        with patch("aqua.cli.setup_wake_model", side_effect=WakeError("Missing wake assets; no download attempted.")), \
                patch("aqua.cli.run_handsfree") as run:
            status, output, errors = self.invoke(["setup-wake"])
        self.assertEqual(status, 1)
        self.assertEqual(output, "")
        self.assertIn("Wake setup error", errors)
        self.assertIn("no download attempted", errors)
        run.assert_not_called()

    def test_handsfree_runner_is_imported_only_by_its_lazy_wrapper(self):
        run = Mock(return_value=130)
        with patch.dict("sys.modules", {"aqua.handsfree": SimpleNamespace(run_handsfree=run)}):
            self.assertEqual(cli.run_handsfree(language="en"), 130)
        run.assert_called_once_with(language="en")

    def assert_invalid(self, arguments):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as errors:
            with self.assertRaises(SystemExit) as exited:
                cli.main(arguments)
        self.assertEqual(exited.exception.code, 2)
        self.assertIn("error:", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
