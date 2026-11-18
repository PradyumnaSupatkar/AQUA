"""Model setup is tested with fake local files and a mocked Hub only."""

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aqua.speech.models import (
    BASE_MODEL_DIR,
    DEFAULT_MODEL_DIR,
    HF_CACHE_DIR,
    MODEL_FILES,
    MODEL_REPO,
    MODEL_REVISION,
    ModelSetupError,
    configure_runtime,
    setup_model,
    validate_model,
)


class ModelSetupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "model"
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)

    def create_model(self):
        self.path.mkdir(parents=True, exist_ok=True)
        for name in MODEL_FILES:
            (self.path / name).write_bytes(b"synthetic test asset")

    def test_default_model_and_caches_are_on_requested_drive(self):
        self.assertEqual(str(DEFAULT_MODEL_DIR), r"D:\Aqua\models\faster-whisper-small")
        self.assertEqual(str(BASE_MODEL_DIR), r"D:\Aqua\models\faster-whisper-base")
        self.assertEqual(str(HF_CACHE_DIR), r"D:\Aqua\.cache\huggingface")

    def test_runtime_configuration_disables_network_and_telemetry(self):
        configure_runtime()
        self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
        self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")
        self.assertEqual(os.environ["HF_HUB_DISABLE_TELEMETRY"], "1")
        self.assertEqual(os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"], "1")
        self.assertEqual(os.environ["HF_HUB_DISABLE_XET"], "1")
        self.assertEqual(os.environ["HF_HOME"], str(HF_CACHE_DIR))
        self.assertEqual(os.environ["HF_HUB_CACHE"], str(HF_CACHE_DIR / "hub"))

    def test_missing_model_validation_does_not_create_a_directory(self):
        with self.assertRaises(ModelSetupError) as error:
            validate_model(self.path)
        self.assertFalse(self.path.exists())
        self.assertIn("setup-model --download", str(error.exception))
        self.assertIn("No download was attempted", str(error.exception))

    def test_missing_tokenizer_and_empty_model_are_rejected(self):
        self.create_model()
        (self.path / "tokenizer.json").unlink()
        (self.path / "model.bin").write_bytes(b"")
        with self.assertRaises(ModelSetupError) as error:
            validate_model(self.path)
        self.assertIn("tokenizer.json", str(error.exception))
        self.assertIn("model.bin", str(error.exception))

    def test_complete_model_validates_without_importing_hub(self):
        self.create_model()
        with patch.dict("sys.modules", {"huggingface_hub": None}):
            self.assertEqual(validate_model(self.path), self.path.resolve())
            self.assertEqual(setup_model(self.path), self.path.resolve())
            self.assertEqual(setup_model(self.path, download=True), self.path.resolve())

    def test_setup_without_download_flag_never_imports_hub(self):
        with patch.dict("sys.modules", {"huggingface_hub": None}):
            with self.assertRaises(ModelSetupError):
                setup_model(self.path)
        self.assertFalse(self.path.exists())

    def test_explicit_setup_downloads_only_pinned_base_files_without_auth(self):
        download = Mock(side_effect=lambda **_kwargs: self.create_model())
        with patch.dict("sys.modules", {"huggingface_hub": SimpleNamespace(snapshot_download=download)}):
            result = setup_model(self.path, download=True)
        self.assertEqual(result, self.path.resolve())
        download.assert_called_once_with(
            repo_id=MODEL_REPO,
            revision=MODEL_REVISION,
            allow_patterns=list(MODEL_FILES),
            local_dir=str(self.path.resolve()),
            cache_dir=str(HF_CACHE_DIR / "hub"),
            token=False,
        )
        self.assertEqual(len(MODEL_REVISION), 40)
        self.assertEqual(os.environ["HF_HUB_DISABLE_TELEMETRY"], "1")
        self.assertEqual(os.environ["HF_HUB_OFFLINE"], "0")

    def test_download_failure_preserves_partial_model(self):
        self.path.mkdir()
        partial = self.path / "model.bin.incomplete"
        partial.write_bytes(b"partial bytes")
        download = Mock(side_effect=ConnectionError("test connection failed"))
        with patch.dict("sys.modules", {"huggingface_hub": SimpleNamespace(snapshot_download=download)}):
            with self.assertRaises(ModelSetupError) as error:
                setup_model(self.path, download=True)
        self.assertEqual(partial.read_bytes(), b"partial bytes")
        self.assertIn("retry", str(error.exception))

    def test_incomplete_download_is_not_reported_as_ready(self):
        download = Mock()
        with patch.dict("sys.modules", {"huggingface_hub": SimpleNamespace(snapshot_download=download)}):
            with self.assertRaises(ModelSetupError):
                setup_model(self.path, download=True)

    def test_cancellation_propagates_instead_of_wrapping(self):
        download = Mock(side_effect=KeyboardInterrupt())
        with patch.dict("sys.modules", {"huggingface_hub": SimpleNamespace(snapshot_download=download)}):
            with self.assertRaises(KeyboardInterrupt):
                setup_model(self.path, download=True)


if __name__ == "__main__":
    unittest.main()
