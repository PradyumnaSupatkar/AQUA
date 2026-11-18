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

    def generate(self, text, sid=0, speed=1.0):
        self.calls.append((text, sid, speed))
        return SimpleNamespace(samples=[0.0, .25, -.25], sample_rate=self.sample_rate)


def fake_sherpa():
    return SimpleNamespace(
        OfflineTtsKokoroModelConfig=Recorder("kokoro"), OfflineTtsVitsModelConfig=Recorder("vits"),
        OfflineTtsKittenModelConfig=Recorder("kitten"), OfflineTtsModelConfig=Recorder("model"),
        OfflineTtsConfig=Recorder("config"), OfflineTts=FakeTts,
    )


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

    def install(self, name, files):
        path = self.root / name
        (path / "espeak-ng-data").mkdir(parents=True)
        for item in files:
            (path / item).write_bytes(b"x")
        return path

    def test_missing_files_name_the_setup_command_and_load_nothing(self):
        for engine in (KokoroEngine, PiperEngine, KittenEngine):
            with self.subTest(engine=engine.__name__):
                with self.assertRaises(VoiceError) as error:
                    engine(self.root / "absent")
                message = str(error.exception)
                self.assertIn(SETUP_COMMAND, message)
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
        path = self.install("kokoro", ["model.onnx", "voices.bin", "tokens.txt", "lexicon-us-en.txt"])
        engine = KokoroEngine(path, voice="af_heart", speed=1.2)
        self.runtime.assert_called_once()
        tts = FakeTts.instances[-1]
        model = tts.config.model
        self.assertEqual((model.num_threads, model.provider, model.debug), (2, "cpu", False))
        self.assertEqual(model.kokoro.kind, "kokoro")
        self.assertEqual(model.kokoro.model, str(path / "model.onnx"))
        self.assertEqual(model.kokoro.voices, str(path / "voices.bin"))
        self.assertEqual(model.kokoro.data_dir, str(path / "espeak-ng-data"))
        self.assertEqual(model.kokoro.lexicon, str(path / "lexicon-us-en.txt"))
        self.assertEqual(engine.sample_rate, 24000)
        samples = engine.synthesize("Hello.")
        self.assertEqual(samples.dtype, np.float32)
        self.assertEqual(tts.calls, [("Hello.", 3, 1.2)])
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
        path = self.install("kokoro", ["model.onnx", "voices.bin", "tokens.txt"])
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


if __name__ == "__main__":
    unittest.main()
