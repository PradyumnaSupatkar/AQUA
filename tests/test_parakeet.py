"""Parakeet recogniser, setup, recogniser selection and the English default (no model files needed)."""

import hashlib
import io
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from aqua import chat, cli
from aqua.speech import parakeet
from aqua.speech.models import ModelSetupError, SpeechError


def small_model(folder: Path) -> tuple:
    """Tiny stand-in files and their pins (the real files are ~670 MB)."""
    files = []
    for name, _size, _sha in parakeet.FILES:
        data = f"fake {name}".encode()
        (folder / name).write_bytes(data)
        files.append((name, len(data), hashlib.sha256(data).hexdigest()))
    return tuple(files)


class FakeSherpa:
    def __init__(self, text="Open notepad."):
        self.text = text
        self.waveforms = []
        self.kwargs = None
        outer = self

        class Stream:
            def __init__(self):
                self.result = types.SimpleNamespace(text="")

            def accept_waveform(self, rate, samples):
                outer.waveforms.append((rate, np.array(samples)))

        class Recognizer:
            def create_stream(self):
                return Stream()

            def decode_stream(self, stream):
                stream.result.text = outer.text

        class OfflineRecognizer:
            @staticmethod
            def from_transducer(**kwargs):
                outer.kwargs = kwargs
                return Recognizer()

        self.module = types.SimpleNamespace(OfflineRecognizer=OfflineRecognizer)


class ParakeetRecognizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        files = patch.object(parakeet, "FILES", small_model(self.folder))
        files.start()
        self.addCleanup(files.stop)
        self.sherpa = FakeSherpa()
        for target in (patch.dict(sys.modules, {"sherpa_onnx": self.sherpa.module}),
                       patch("aqua.wake.privacy.configure_runtime")):
            target.start()
            self.addCleanup(target.stop)

    def test_loads_the_transducer_without_hotwords(self):
        recognizer = parakeet.ParakeetRecognizer(self.folder)
        kwargs = self.sherpa.kwargs
        self.assertEqual((kwargs["model_type"], kwargs["decoding_method"], kwargs["num_threads"]),
                         ("nemo_transducer", "greedy_search", 4))
        self.assertNotIn("hotwords_file", kwargs)
        self.assertEqual(recognizer.name, "parakeet-tdt-0.6b-v3-int8")

    def test_pads_half_a_second_and_ignores_language_and_hotwords(self):
        recognizer = parakeet.ParakeetRecognizer(self.folder)
        pcm = (np.full(16000, 3000, dtype="<i2")).tobytes()  # 1 s at 16 kHz
        transcript = recognizer.transcribe(pcm, 16000, language="fr", hotwords="write")
        rate, samples = self.sherpa.waveforms[0]
        self.assertEqual(rate, 16000)
        self.assertEqual(len(samples), 16000 + 2 * 8000)
        self.assertTrue(np.all(samples[:8000] == 0) and np.all(samples[-8000:] == 0))
        self.assertEqual((transcript.text, transcript.language), ("Open notepad.", "en"))
        self.assertIsNone(transcript.avg_logprob)

    def test_silence_returns_empty_text_without_decoding(self):
        recognizer = parakeet.ParakeetRecognizer(self.folder)
        transcript = recognizer.transcribe(bytes(32000), 16000)
        self.assertEqual(transcript.text, "")
        self.assertEqual(self.sherpa.waveforms, [])

    def test_missing_or_wrong_files_name_the_setup_command(self):
        (self.folder / "joiner.int8.onnx").write_bytes(b"changed!!")
        with self.assertRaises(ModelSetupError) as caught:
            parakeet.verify(self.folder)
        self.assertIn("aqua setup-model --model parakeet --download", str(caught.exception))
        self.assertIn("--asr whisper", str(caught.exception))
        with self.assertRaises(SpeechError):
            parakeet.ParakeetRecognizer(Path(self.temp.name) / "nowhere")

    def test_setup_without_download_only_verifies(self):
        with patch("huggingface_hub.snapshot_download") as download:
            self.assertEqual(parakeet.setup(self.folder), self.folder)
            (self.folder / "tokens.txt").unlink()
            with self.assertRaises(ModelSetupError):
                parakeet.setup(self.folder)
        download.assert_not_called()

    def test_setup_downloads_the_pinned_revision_when_asked(self):
        (self.folder / "tokens.txt").unlink()

        def fetch(**kwargs):
            small = dict((n, None) for n, _s, _d in parakeet.FILES)
            self.assertEqual(set(kwargs["allow_patterns"]), set(small))
            (self.folder / "tokens.txt").write_bytes(b"fake tokens.txt")
        with patch("huggingface_hub.snapshot_download", side_effect=fetch) as download, \
                patch("aqua.speech.models.configure_runtime"):
            parakeet.setup(self.folder, download=True)
        kwargs = download.call_args.kwargs
        self.assertEqual((kwargs["repo_id"], kwargs["revision"]), (parakeet.REPO, parakeet.REVISION))


class PinsTests(unittest.TestCase):
    def test_benchmark_tool_uses_the_same_pins(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import asr_models
        repo, revision, files, _licence = asr_models.HF_MODELS["sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"]
        self.assertEqual((repo, revision), (parakeet.REPO, parakeet.REVISION))
        for name, size, sha in parakeet.FILES:
            pinned_size, _blob, pinned_sha = files[name]
            self.assertEqual(size, pinned_size, name)
            if pinned_sha:
                self.assertEqual(sha, pinned_sha, name)


class SelectionTests(unittest.TestCase):
    def test_load_recognizer(self):
        whisper, fast = Mock(return_value="W"), Mock(return_value="P")
        self.assertEqual(chat.load_recognizer("parakeet", whisper=whisper, parakeet=fast), "P")
        self.assertEqual(chat.load_recognizer("whisper", Path("x"), whisper=whisper, parakeet=fast), "W")
        whisper.assert_called_once_with(Path("x"))
        with self.assertRaises(SpeechError):
            chat.load_recognizer("vosk", whisper=whisper, parakeet=fast)

    def test_cli_defaults_parakeet_and_english(self):
        for command in (["voice-chat"], ["handsfree"], ["transcribe"]):
            args = cli._parser().parse_args(command)
            self.assertEqual((args.asr, args.language), ("parakeet", "en"), command)
        self.assertEqual(cli._parser().parse_args(["compare"]).language, "en")

    def test_whisper_only_flags_need_asr_whisper(self):
        for argv in (["voice-chat", "--speech-model", "small"], ["handsfree", "--hints"],
                     ["transcribe", "--model", "base"]):
            with redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
                cli.main(argv)
            self.assertIn("--asr whisper", err.getvalue(), argv)

    def test_language_auto_means_detection_and_asr_is_forwarded(self):
        with patch("aqua.cli.run_voice_chat", return_value=0) as voice:
            self.assertEqual(cli.main(["voice-chat", "--asr", "whisper", "--language", "auto"]), 0)
        self.assertIsNone(voice.call_args.kwargs["language"])
        self.assertEqual(voice.call_args.kwargs["asr"], "whisper")
        with patch("aqua.cli.run_handsfree", return_value=0) as handsfree:
            cli.main(["handsfree"])
        self.assertEqual((handsfree.call_args.kwargs["asr"], handsfree.call_args.kwargs["language"]),
                         ("parakeet", "en"))
        self.assertIsNone(handsfree.call_args.kwargs["speech_model_dir"])

    def test_setup_model_parakeet_verifies_without_download(self):
        with patch("aqua.speech.parakeet.setup", return_value=Path("D:/m")) as setup, \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["setup-model", "--model", "parakeet"]), 0)
        setup.assert_called_once_with(parakeet.PARAKEET_DIR, download=False)
        self.assertIn("CC-BY-4.0", out.getvalue())
        with patch("aqua.speech.parakeet.setup", side_effect=ModelSetupError("missing; run aqua setup-model")), \
                redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["setup-model", "--model", "parakeet"]), 1)
        self.assertIn("setup-model", err.getvalue())

    def test_diagnostics_name_the_recogniser(self):
        recognizer = Mock()
        recognizer.name = "parakeet-tdt-0.6b-v3-int8"
        recognizer.transcribe.return_value = types.SimpleNamespace(text="Open notepad.", speech_seconds=1.0)
        recording = Mock(device_info=None, duration=2.0, sample_rate=16000, pcm=b"",
                         levels=types.SimpleNamespace(peak_dbfs=-3.0, rms_dbfs=-20.0))
        with redirect_stdout(io.StringIO()) as out:
            chat._transcribe_recording(recognizer, recording, capture_seconds=2.0, gain_db=0.0, language="en",
                                       hotwords=None, diagnostics=True)
        self.assertIn("(parakeet-tdt-0.6b-v3-int8)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
