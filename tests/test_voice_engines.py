"""TTS engine setup against a fake sherpa_onnx module: no models, no native code."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from aqua.voice import engines
from aqua.voice.engines import (
    SETUP_COMMAND, FakeEngine, KittenEngine, KokoroEngine, NullEngine, PiperEngine, SpeechEngine, VoiceError,
    load_engine,
)


class Recorder:
    """Stand-in for each sherpa config class: remembers its keyword arguments."""
    def __init__(self, name):
        self.name = name

    def __call__(self, **kwargs):
        return SimpleNamespace(kind=self.name, **kwargs)


class FakeTts:
    instances = []

    def __init__(self, config):
        self.config = config
        self.sample_rate = 24000
        self.num_speakers = 30
        self.calls = []
        FakeTts.instances.append(self)

    lang = "en-us"

    def generate(self, text, config):
        self.calls.append((text, config.sid, config.speed, config.phoneme_codepoints))
        return SimpleNamespace(samples=[0.0, .25, -.25], sample_rate=self.sample_rate)


class GenerationConfig:
    sid, speed, phoneme_codepoints = 0, 1.0, []


def fake_sherpa():
    return SimpleNamespace(
        OfflineTtsKokoroModelConfig=Recorder("kokoro"), OfflineTtsVitsModelConfig=Recorder("vits"),
        OfflineTtsKittenModelConfig=Recorder("kitten"), OfflineTtsModelConfig=Recorder("model"),
        OfflineTtsConfig=Recorder("config"), OfflineTts=FakeTts, GenerationConfig=GenerationConfig,
    )


def fake_espeak(text, lang):
    """Deterministic stand-in for piper_phonemize.phonemize_espeak."""
    return [list(text.lower())]


TOKENS = "\n".join(f"{symbol} {index}" for index, symbol in enumerate(" .,?abcdefghijklmnopqrstuvwxyzəO")) + "\n"


class EngineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        FakeTts.instances.clear()
        runtime = patch("aqua.voice.engines._configure_runtime")
        self.runtime = runtime.start()
        self.addCleanup(runtime.stop)
        modules = patch.dict("sys.modules", {"sherpa_onnx": fake_sherpa()})
        modules.start()
        self.addCleanup(modules.stop)
        espeak = patch("aqua.voice.phonemes._espeak", return_value=fake_espeak)
        self.espeak = espeak.start()
        self.addCleanup(espeak.stop)

    def install(self, name, files):
        path = self.root / name
        path.mkdir(parents=True)
        for item in files:
            content = {"tokens.txt": TOKENS, "lexicon-us-en.txt": "hello h ə l O\n",
                       "lexicon-gb-en.txt": "hello h ə l ə ʊ\n"}.get(item, "x")
            (path / item).write_text(content, encoding="utf-8")
        return path

    def test_missing_files_name_the_setup_command_and_load_nothing(self):
        for engine in (KokoroEngine, PiperEngine, KittenEngine):
            with self.subTest(engine=engine.__name__):
                with self.assertRaises(VoiceError) as error:
                    engine(self.root / "absent")
                message = str(error.exception)
                self.assertIn("aqua setup-voice --engine", message)
                self.assertIn("No download was attempted", message)
        self.runtime.assert_not_called()
        self.assertEqual(FakeTts.instances, [])

    def test_incomplete_kokoro_lists_missing_files(self):
        path = self.install("kokoro", ["model.onnx", "tokens.txt"])
        with self.assertRaises(VoiceError) as error:
            KokoroEngine(path)
        self.assertIn("voices.bin", str(error.exception))
        self.assertNotIn("tokens.txt", str(error.exception))

    def test_kokoro_uses_installed_sherpa_kokoro_config(self):
        path = self.install("kokoro", ["model.onnx", "voices.bin", "tokens.txt", "lexicon-us-en.txt", "lexicon-gb-en.txt"])
        engine = KokoroEngine(path, voice="af_heart", speed=1.2)
        self.runtime.assert_called_once()
        tts = FakeTts.instances[-1]
        model = tts.config.model
        self.assertEqual((model.num_threads, model.provider, model.debug), (4, "cpu", False))  # Kokoro default
        self.assertEqual(model.kokoro.kind, "kokoro")
        self.assertEqual(model.kokoro.model, str(path / "model.onnx"))
        self.assertEqual(model.kokoro.voices, str(path / "voices.bin"))
        self.assertFalse(hasattr(model.kokoro, "data_dir"))  # sherpa-onnx 2.x ignores espeak-ng-data
        self.assertEqual(model.kokoro.lexicon, str(path / "lexicon-us-en.txt"))
        self.assertEqual(engine.sample_rate, 24000)
        samples = engine.synthesize("Hello, Aqua?")
        self.assertEqual(samples.dtype, np.float32)
        # Lexicon word "hello" -> həlO; unknown "aqua" -> espeak; punctuation kept; text itself not sent.
        self.assertEqual(tts.calls, [("", 3, 1.2, [[ord(c) for c in "həlO, aqua?"]])])
        self.assertEqual(engine.synthesize("--- !").size, 0)  # nothing speakable -> no generate call
        self.assertEqual(len(tts.calls), 1)
        self.assertIsInstance(engine, SpeechEngine)

    def test_piper_uses_vits_and_finds_its_single_onnx(self):
        path = self.install("piper", ["en_US-amy-low.onnx", "tokens.txt"])
        engine = PiperEngine(path, voice="0", num_threads=1)
        model = FakeTts.instances[-1].config.model
        self.assertEqual(model.vits.kind, "vits")
        self.assertEqual(model.vits.model, str(path / "en_US-amy-low.onnx"))
        self.assertEqual(model.num_threads, 1)
        self.assertEqual(engine.speaker_id, 0)

    def test_kitten_named_voice(self):
        path = self.install("kitten", ["model.fp16.onnx", "voices.bin", "tokens.txt"])
        engine = KittenEngine(path, voice="expr-voice-3-f")
        self.assertEqual(FakeTts.instances[-1].config.model.kitten.kind, "kitten")
        self.assertEqual(engine.speaker_id, 3)

    def test_invalid_voice_speed_threads_and_engine(self):
        path = self.install("kokoro", ["model.onnx", "voices.bin", "tokens.txt", "lexicon-us-en.txt", "lexicon-gb-en.txt"])
        for options in ({"voice": "nobody"}, {"speed": 0}, {"speed": float("nan")}, {"num_threads": 0}, {"voice": "99"}):
            with self.subTest(options=options), self.assertRaises(VoiceError):
                KokoroEngine(path, **options)
        with self.assertRaises(VoiceError):
            load_engine("espeak")

    def test_none_and_fake_engines(self):
        self.assertIsInstance(load_engine("none"), NullEngine)
        self.assertEqual(NullEngine().synthesize("hi").size, 0)
        fake = FakeEngine(fail_on=2)
        self.assertEqual(fake.synthesize("ab").size, 8)
        with self.assertRaises(VoiceError):
            fake.synthesize("x")
        self.assertEqual(fake.texts, ["ab", "x"])

    def test_default_directories_live_under_the_project_models_folder(self):
        for name, path in engines.ENGINE_DIRS.items():
            self.assertEqual(path.parent, Path(r"D:\Aqua\models"))

    def test_directories_match_setup_voice(self):
        from aqua.voice.setup import ASSETS
        self.assertEqual(engines.engine_dir("kokoro"), ASSETS["kokoro"].directory)
        self.assertEqual(engines.engine_dir("kitten"), ASSETS["kitten"].directory)
        for voice in engines.PIPER_VOICES:
            self.assertEqual(engines.engine_dir("piper", voice), ASSETS[f"piper-{voice}"].directory)
            self.assertEqual(engines.engine_dir("piper", voice.upper()), ASSETS[f"piper-{voice}"].directory)
        with self.assertRaisesRegex(VoiceError, "lessac, ryan"):
            engines.engine_dir("piper")

    def test_piper_voice_selects_its_directory_and_missing_names_exact_command(self):
        with patch.object(engines, "VOICE_ROOT", self.root):
            with self.assertRaises(VoiceError) as error:
                PiperEngine(voice="ryan")
            self.assertIn(str(self.root / "tts-piper-ryan"), str(error.exception))
            self.assertIn("aqua setup-voice --engine piper --voice ryan --download", str(error.exception))
            with self.assertRaisesRegex(VoiceError, "Choose a Piper voice"):
                PiperEngine()
            path = self.install("tts-piper-lessac", ["en_US-lessac-medium.onnx", "tokens.txt"])
            engine = PiperEngine(voice="lessac")
        self.assertEqual(engine.model_dir, path)
        self.assertEqual(engine.speaker_id, 0)
        with self.assertRaises(VoiceError):
            engine.resolve_voice("amy")

    def test_kokoro_lexicon_follows_voice_accent(self):
        files = ["model.onnx", "voices.bin", "tokens.txt", "lexicon-us-en.txt", "lexicon-gb-en.txt"]
        path = self.install("kokoro-both", files)
        for voice, lexicon in (("bm_george", "lexicon-gb-en.txt"), ("bf_emma", "lexicon-gb-en.txt"),
                               ("21", "lexicon-gb-en.txt"), ("am_adam", "lexicon-us-en.txt"), ("af_heart", "lexicon-us-en.txt")):
            with self.subTest(voice=voice):
                KokoroEngine(path, voice=voice)
                self.assertEqual(FakeTts.instances[-1].config.model.kokoro.lexicon, str(path / lexicon))
        engine = load_engine("kokoro", model_dir=path, voice="bm_lewis")
        self.assertEqual(FakeTts.instances[-1].config.model.kokoro.model, str(path / "model.onnx"))
        self.assertEqual(engine.speaker_id, 27)

    def test_int8_kokoro_engine_was_removed(self):
        self.assertNotIn("kokoro-int8", engines.ENGINE_NAMES)
        self.assertNotIn("kokoro-int8", engines.ENGINE_CLASSES)
        with self.assertRaises(VoiceError):
            load_engine("kokoro-int8")

    def test_thread_defaults_per_engine_and_override(self):
        self.assertEqual(engines.DEFAULT_THREADS, {"kokoro": 4, "piper": 2, "kitten": 2})
        kokoro = self.install("k", ["model.onnx", "voices.bin", "tokens.txt", "lexicon-us-en.txt", "lexicon-gb-en.txt"])
        piper = self.install("p", ["en_US-lessac-medium.onnx", "tokens.txt"])
        kitten = self.install("c", ["model.fp16.onnx", "voices.bin", "tokens.txt"])
        for engine, path, expected in (("kokoro", kokoro, 4), ("piper", piper, 2), ("kitten", kitten, 2)):
            with self.subTest(engine=engine):
                voice = "lessac" if engine == "piper" else None
                loaded = load_engine(engine, model_dir=path, voice=voice)
                self.assertEqual((loaded.num_threads, FakeTts.instances[-1].config.model.num_threads), (expected, expected))
                load_engine(engine, model_dir=path, voice=voice, num_threads=3)
                self.assertEqual(FakeTts.instances[-1].config.model.num_threads, 3)

    def test_default_and_documented_voices(self):
        self.assertEqual(engines.DEFAULT_ENGINE, "kokoro")
        self.assertEqual(engines.DEFAULT_VOICES["kokoro"], "af_heart")
        self.assertEqual(engines.MALE_VOICE, "am_puck")
        self.assertEqual(engines.KOKORO_ENGLISH_VOICES["am_puck"], ("male", "American"))
        self.assertIsNone(engines.DEFAULT_VOICES["piper"])  # Piper is never a default

    def test_voice_tables_match_the_sherpa_onnx_docs(self):
        self.assertEqual(len(engines.KOKORO_VOICES), 28)
        self.assertEqual((engines.KOKORO_VOICES["af_alloy"], engines.KOKORO_VOICES["am_santa"],
                          engines.KOKORO_VOICES["bf_alice"], engines.KOKORO_VOICES["bm_lewis"]), (0, 19, 20, 27))
        self.assertEqual(sum(1 for g, a in engines.KOKORO_ENGLISH_VOICES.values() if a == "British"), 8)
        self.assertEqual(engines.KITTEN_VOICES["expr-voice-2-m"], 0)
        self.assertEqual(engines.KITTEN_VOICES["expr-voice-5-f"], 7)
        self.assertEqual(set(engines.PIPER_VOICES), {"lessac", "ryan"})


if __name__ == "__main__":
    unittest.main()
