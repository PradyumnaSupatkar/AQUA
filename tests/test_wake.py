"""Wake setup and detector tests use no network, microphone, or native model."""

import ctypes
import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from aqua.wake import keyword, models, privacy
from aqua.wake.models import WakeError


class WakeModelTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "wake"
        self.assets = {"asset.onnx": (3, hashlib.sha256(b"abc").hexdigest())}
        manager = patch.object(models, "MODEL_FILES", self.assets)
        manager.start()
        self.addCleanup(manager.stop)
        manager = patch.object(models, "urlopen", side_effect=AssertionError("Unexpected network request"))
        self.open_url = manager.start()
        self.addCleanup(manager.stop)

    def install_fixture(self, data=b"abc"):
        self.directory.mkdir()
        (self.directory / "asset.onnx").write_bytes(data)

    def test_missing_local_model_does_not_create_directory_or_download(self):
        with self.assertRaisesRegex(WakeError, "No download was attempted"):
            models.setup_model(self.directory)
        self.assertFalse(self.directory.exists())
        self.open_url.assert_not_called()

    def test_valid_files_are_reused_even_with_download_flag(self):
        self.install_fixture()
        self.assertEqual(models.setup_model(self.directory, download=True), self.directory.resolve())
        self.open_url.assert_not_called()

    def test_wrong_content_or_size_fails_verification(self):
        self.install_fixture(b"abd")
        for content in (b"abd", b"", b"abcd"):
            (self.directory / "asset.onnx").write_bytes(content)
            with self.subTest(content=content), self.assertRaises(WakeError):
                models.validate_model(self.directory)
        self.open_url.assert_not_called()

    def test_explicit_download_uses_pinned_source_and_verifies_before_final_name(self):
        self.open_url.side_effect = lambda *_args, **_kwargs: io.BytesIO(b"abc")
        result = models.setup_model(self.directory, download=True)
        self.assertEqual(result, self.directory.resolve())
        self.assertEqual((self.directory / "asset.onnx").read_bytes(), b"abc")
        self.assertFalse((self.directory / "asset.onnx.part").exists())
        request = self.open_url.call_args.args[0]
        self.assertEqual(request.full_url, models.MODEL_BASE_URL + "/asset.onnx")
        self.assertIn(models.MODEL_REVISION, request.full_url)

    def test_invalid_existing_final_asset_is_not_overwritten(self):
        self.install_fixture(b"bad")
        with self.assertRaisesRegex(WakeError, "not overwritten"):
            models.setup_model(self.directory, download=True)
        self.assertEqual((self.directory / "asset.onnx").read_bytes(), b"bad")
        self.open_url.assert_not_called()

    def test_failed_hash_or_excess_size_leaves_partial_and_no_final_asset(self):
        for data in (b"abd", b"abcd", b"a"):
            with self.subTest(data=data):
                self.open_url.side_effect = lambda *_args, **_kwargs: io.BytesIO(data)
                with self.assertRaises(WakeError):
                    models.setup_model(self.directory, download=True)
                self.assertTrue((self.directory / "asset.onnx.part").is_file())
                self.assertFalse((self.directory / "asset.onnx").exists())

    def test_connection_failure_is_safe_and_does_not_delete_other_files(self):
        self.install_fixture()
        (self.directory / "asset.onnx").unlink()
        marker = self.directory / "unrelated.txt"
        marker.write_text("retain", encoding="utf-8")
        self.open_url.side_effect = TimeoutError("offline")
        with self.assertRaisesRegex(WakeError, "No other models were modified"):
            models.setup_model(self.directory, download=True)
        self.assertEqual(marker.read_text(encoding="utf-8"), "retain")


class WakeDetectorTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        (self.directory / "tokens.txt").write_text(
            "\n".join(f"{token} {index}" for index, token in enumerate(keyword.WAKE_TOKENS)), encoding="utf-8",
        )
        self.engine = Mock()
        self.engine.is_ready.return_value = False
        self.engine.get_result.return_value = ""
        self.engine.timestamps.return_value = [.1, .2, .3, .4, .5]
        self.module = SimpleNamespace(KeywordSpotter=Mock(return_value=self.engine))
        for manager in (
            patch.object(keyword, "validate_model", return_value=self.directory),
            patch.object(keyword, "configure_runtime"),
            patch.dict("sys.modules", {"sherpa_onnx": self.module}),
            patch("socket.create_connection", side_effect=AssertionError("Tests must remain offline")),
            patch("socket.socket.connect", side_effect=AssertionError("Tests must remain offline")),
        ):
            manager.start()
            self.addCleanup(manager.stop)

    def frame(self):
        return np.zeros(512, dtype=np.float32)

    def test_loads_once_on_cpu_and_uses_exact_phrase_definition(self):
        detector = keyword.SherpaWakeDetector(threshold=.4, score=1.5)
        options = self.module.KeywordSpotter.call_args.kwargs
        self.assertEqual(options["provider"], "cpu")
        self.assertEqual(options["num_threads"], 1)
        self.assertEqual(options["max_active_paths"], 4)
        self.assertEqual(options["keywords_threshold"], .4)
        self.assertEqual(options["keywords_score"], 1.5)
        self.assertEqual(Path(options["keywords_file"]).read_text(encoding="utf-8").strip(), "\u2581HE Y \u2581A QU A @HEY_AQUA")
        detector.feed(self.frame())
        detector.reset()
        detector.reset()
        self.module.KeywordSpotter.assert_called_once()

    def test_no_keyword_event_does_not_invent_activation(self):
        detector = keyword.SherpaWakeDetector()
        self.engine.is_ready.side_effect = [True, False]
        self.assertIsNone(detector.feed(self.frame()))
        self.engine.decode_stream.assert_called_once()
        self.engine.reset_stream.assert_not_called()

    def test_exact_keyword_returns_timestamps_and_resets_immediately(self):
        detector = keyword.SherpaWakeDetector()
        detector.feed(self.frame())
        self.engine.is_ready.return_value = True
        self.engine.get_result.return_value = "HEY_AQUA"
        event = detector.feed(self.frame())
        self.assertEqual(event.keyword, "Hey Aqua")
        self.assertAlmostEqual(event.audio_seconds, 1024 / 16000)
        self.assertEqual(event.timestamps, (.1, .2, .3, .4, .5))
        self.engine.reset_stream.assert_called_once()
        self.assertEqual(self.engine.create_stream.call_count, 2)
        self.assertEqual(detector._samples, 0)

    def test_unexpected_keyword_cannot_activate(self):
        detector = keyword.SherpaWakeDetector()
        self.engine.is_ready.return_value = True
        self.engine.get_result.return_value = "HEY_JARVIS"
        with self.assertRaisesRegex(WakeError, "other than Hey Aqua"):
            detector.feed(self.frame())
        self.engine.reset_stream.assert_called_once()

    def test_bad_audio_is_rejected_before_native_inference(self):
        detector = keyword.SherpaWakeDetector()
        for frame in (np.zeros(511), np.zeros((1, 512)), np.full(512, np.nan), np.full(512, 2), "bad"):
            with self.subTest(frame_type=type(frame).__name__), self.assertRaises(WakeError):
                detector.feed(frame)
        self.engine.is_ready.assert_not_called()

    def test_invalid_parameters_fail_before_loading_model(self):
        for options in ({"threshold": 0}, {"threshold": 1.1}, {"threshold": float("nan")},
                        {"threshold": True}, {"score": -1}, {"score": float("inf")}, {"score": True}):
            with self.subTest(options=options), self.assertRaises(WakeError):
                keyword.SherpaWakeDetector(**options)
        keyword.validate_model.assert_not_called()
        self.module.KeywordSpotter.assert_not_called()

    def test_missing_optional_dependency_produces_setup_error(self):
        with patch.dict("sys.modules", {"sherpa_onnx": None}), self.assertRaisesRegex(WakeError, "handsfree extra"):
            keyword.SherpaWakeDetector()

    def test_missing_target_token_rejects_model_before_native_load(self):
        (self.directory / "tokens.txt").write_text("unrelated 0", encoding="utf-8")
        with self.assertRaisesRegex(WakeError, "cannot encode"):
            keyword.SherpaWakeDetector()
        self.module.KeywordSpotter.assert_not_called()

    def test_model_and_processing_errors_are_not_silently_accepted(self):
        self.module.KeywordSpotter.side_effect = RuntimeError("bad graph")
        with self.assertRaises(WakeError):
            keyword.SherpaWakeDetector()
        self.module.KeywordSpotter.side_effect = None
        detector = keyword.SherpaWakeDetector()
        self.engine.is_ready.side_effect = RuntimeError("inference failed")
        with self.assertRaises(WakeError):
            detector.feed(self.frame())


class WakePrivacyTests(unittest.TestCase):
    def test_windows_config_disables_correct_bundled_runtime_once(self):
        owner = object()
        spec = SimpleNamespace(origin=r"D:\fake\sherpa_onnx\__init__.py")
        with patch.dict("os.environ", {}, clear=True), patch.object(privacy, "_runtime_owner", None), \
                patch.object(privacy.sys, "platform", "win32"), \
                patch.object(privacy.importlib.util, "find_spec", return_value=spec), \
                patch.object(Path, "is_file", return_value=True), \
                patch.object(privacy, "_disable_bundled_runtime", return_value=owner) as disable:
            privacy.configure_runtime()
            privacy.configure_runtime()
            disable.assert_called_once_with(Path(spec.origin).parent / "lib" / "onnxruntime.dll")
            self.assertEqual(privacy.os.environ["ORT_DISABLE_TELEMETRY"], "1")
            self.assertEqual(privacy.os.environ["DO_NOT_TRACK"], "1")
            self.assertIs(privacy._runtime_owner, owner)

    def test_windows_privacy_failure_stops_startup(self):
        spec = SimpleNamespace(origin=r"D:\fake\sherpa_onnx\__init__.py")
        with patch.object(privacy, "_runtime_owner", None), patch.object(privacy.sys, "platform", "win32"), \
                patch.object(privacy.importlib.util, "find_spec", return_value=spec), \
                patch.object(Path, "is_file", return_value=True), \
                patch.object(privacy, "_disable_bundled_runtime", side_effect=OSError("missing API")):
            with self.assertRaisesRegex(WakeError, "telemetry; startup stopped"):
                privacy.configure_runtime()
            self.assertIsNone(privacy._runtime_owner)

    def test_native_public_api_creates_environment_and_disables_telemetry(self):
        def create(_level, _label, output):
            output._obj.value = 1234
            return None

        api = SimpleNamespace(CreateEnv=Mock(side_effect=create), DisableTelemetryEvents=Mock(return_value=None))
        pointer = SimpleNamespace(contents=api)
        get_api = Mock(return_value=pointer)
        library = Mock()
        library.OrtGetApiBase.return_value = SimpleNamespace(contents=SimpleNamespace(GetApi=get_api))
        with patch.object(ctypes, "WinDLL", return_value=library):
            owner = privacy._disable_bundled_runtime(Path(r"D:\fixed\onnxruntime.dll"))
        get_api.assert_called_once_with(1)
        self.assertEqual(owner[1].value, 1234)
        api.DisableTelemetryEvents.assert_called_once_with(owner[1])
        self.assertEqual(api.CreateEnv.call_args.args[:2], (3, b"Aqua-wake-private"))

    def test_native_telemetry_error_fails_closed(self):
        def create(_level, _label, output):
            output._obj.value = 1234
            return None

        api = SimpleNamespace(CreateEnv=Mock(side_effect=create), DisableTelemetryEvents=Mock(return_value=1))
        library = Mock()
        library.OrtGetApiBase.return_value = SimpleNamespace(contents=SimpleNamespace(GetApi=Mock(return_value=SimpleNamespace(contents=api))))
        with patch.object(ctypes, "WinDLL", return_value=library), self.assertRaisesRegex(WakeError, "disable telemetry"):
            privacy._disable_bundled_runtime(Path(r"D:\fixed\onnxruntime.dll"))


if __name__ == "__main__":
    unittest.main()
