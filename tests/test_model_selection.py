"""Local model names and setup guards use synthetic assets, never the network."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aqua.speech.models import (
    BASE_MODEL_DIR,
    DEFAULT_MODEL,
    DEFAULT_MODEL_DIR,
    MODEL_DIRS,
    MODEL_FILES,
    ModelSetupError,
    resolve_model_dir,
    setup_model,
    validate_model,
)


class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # Resolve so Windows 8.3 short temp paths (e.g. RUNNER~1 on CI) match
        # the long-form paths that validate_model reports.
        root = Path(temporary.name).resolve()
        self.base = root / "faster-whisper-base"
        self.small = root / "faster-whisper-small"

    def registry(self):
        return patch.dict(MODEL_DIRS, {"base": self.base, "small": self.small}, clear=True)

    @staticmethod
    def create_assets(path):
        path.mkdir(parents=True)
        for name in MODEL_FILES:
            (path / name).write_bytes(b"synthetic model asset")

    def test_small_is_default_and_base_retains_its_own_local_path(self):
        self.assertEqual(DEFAULT_MODEL, "small")
        self.assertEqual(resolve_model_dir(), DEFAULT_MODEL_DIR)
        self.assertEqual(resolve_model_dir("base"), BASE_MODEL_DIR)
        self.assertEqual(
            resolve_model_dir("small"), Path(r"D:\Aqua\models\faster-whisper-small")
        )

    def test_selecting_an_uninstalled_model_does_not_create_or_download_it(self):
        with self.registry(), patch.dict("sys.modules", {"huggingface_hub": None}):
            self.assertEqual(resolve_model_dir("small"), self.small)
        self.assertFalse(self.small.exists())

    def test_explicit_directory_override_preserves_existing_api(self):
        self.assertEqual(resolve_model_dir(model_dir=self.base), self.base)
        self.assertEqual(resolve_model_dir(model_dir=str(self.base)), self.base)

    def test_unknown_model_lists_supported_choices_without_fallback(self):
        with self.assertRaises(ModelSetupError) as error:
            resolve_model_dir("large")
        message = str(error.exception)
        self.assertIn("Unknown", message)
        self.assertIn("base", message)
        self.assertIn("small", message)
        self.assertIn("No download was attempted", message)

    def test_any_explicit_name_and_directory_override_are_rejected(self):
        for name in ("base", "small"):
            with self.subTest(name=name), self.assertRaisesRegex(ModelSetupError, "either"):
                resolve_model_dir(name, self.base)

    def test_missing_small_gives_installation_error_without_base_download_command(self):
        with self.registry(), patch.dict("sys.modules", {"huggingface_hub": None}):
            with self.assertRaises(ModelSetupError) as error:
                validate_model(resolve_model_dir("small"))
        message = str(error.exception)
        self.assertIn("Small", message)
        self.assertIn(str(self.small), message)
        self.assertIn("No download was attempted", message)
        self.assertIn("--model base", message)
        self.assertNotIn("setup-model --download", message)
        self.assertIn("setup-model --model small --download", message)
        self.assertFalse(self.small.exists())

    def test_complete_small_model_uses_same_file_validation_without_hub_import(self):
        self.create_assets(self.small)
        with self.registry(), patch.dict("sys.modules", {"huggingface_hub": None}):
            self.assertEqual(validate_model(resolve_model_dir("small")), self.small.resolve())
            self.assertEqual(setup_model(self.small), self.small.resolve())

    def test_missing_generic_directory_has_model_neutral_error(self):
        with self.registry():
            with self.assertRaises(ModelSetupError) as error:
                validate_model(self.base)
        self.assertIn("offline speech model", str(error.exception))
        self.assertNotIn("offline base model is incomplete", str(error.exception))
        self.assertIn("setup-model --download", str(error.exception))

    def test_base_download_cannot_be_misrouted_to_missing_small_directory(self):
        with self.registry(), patch.dict("sys.modules", {"huggingface_hub": None}):
            with self.assertRaises(ModelSetupError) as error:
                setup_model(self.small, download=True)
        self.assertIn("Use --model small", str(error.exception))
        self.assertIn("No download was attempted", str(error.exception))
        self.assertFalse(self.small.exists())

    def test_download_guard_preserves_existing_small_assets(self):
        self.create_assets(self.small)
        before = {asset.name: asset.read_bytes() for asset in self.small.iterdir()}
        with self.registry(), patch.dict("sys.modules", {"huggingface_hub": None}):
            with self.assertRaises(ModelSetupError):
                setup_model(self.small, download=True)
        self.assertEqual(
            {asset.name: asset.read_bytes() for asset in self.small.iterdir()}, before
        )


if __name__ == "__main__":
    unittest.main()
