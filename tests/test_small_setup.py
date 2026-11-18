"""setup-model --model small: pinned hashes, verify-only default, download path.

Synthetic files in a temporary directory and a fake huggingface_hub only; no
network and no real model files.
"""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aqua import cli
from aqua.speech import models
from aqua.speech.models import MODEL_FILES, ModelSetupError, ModelSource, setup_model, verify_files


def pinned(contents):
    return tuple((name, len(data), hashlib.sha256(data).hexdigest()) for name, data in contents.items())


class SmallSetupTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.getaddrinfo", "socket.socket.connect", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Setup tests must stay offline."))
            manager.start()
            self.addCleanup(manager.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.small, self.base = root / "faster-whisper-small", root / "faster-whisper-base"
        self.contents = {name: f"synthetic {name}".encode() for name in MODEL_FILES}
        source = ModelSource("Systran/faster-whisper-small", models.SMALL_MODEL_REVISION, 486, pinned(self.contents))
        for manager in (
            patch.dict(models.MODEL_DIRS, {"base": self.base, "small": self.small}),
            patch.dict(models.MODEL_SOURCES, {"small": source}),
            patch("aqua.speech.models.configure_runtime"),
        ):
            manager.start()
            self.addCleanup(manager.stop)

    def install(self, path=None, **overrides):
        path = path or self.small
        path.mkdir(parents=True, exist_ok=True)
        for name, data in {**self.contents, **overrides}.items():
            (path / name).write_bytes(data)
        return path

    def fake_hub(self, *, writes=None):
        def download(**options):
            target = Path(options["local_dir"])
            target.mkdir(parents=True, exist_ok=True)
            for name, data in (writes if writes is not None else self.contents).items():
                (target / name).write_bytes(data)
        hub = SimpleNamespace(snapshot_download=Mock(side_effect=download))
        return hub, patch.dict("sys.modules", {"huggingface_hub": hub})

    def test_verify_only_accepts_matching_files_without_the_hub(self):
        self.install()
        with patch.dict("sys.modules", {"huggingface_hub": None}):
            self.assertEqual(setup_model(model="small"), self.small)

    def test_verify_only_reports_size_and_hash_mismatches_and_deletes_nothing(self):
        self.install(**{"model.bin": b"synthetic model.biX", "vocabulary.txt": b"short"})
        before = sorted(item.name for item in self.small.iterdir())
        with patch.dict("sys.modules", {"huggingface_hub": None}), self.assertRaises(ModelSetupError) as error:
            setup_model(model="small")
        message = str(error.exception)
        self.assertIn("model.bin (SHA-256)", message)
        self.assertIn("vocabulary.txt (size)", message)
        self.assertIn("No files were deleted", message)
        self.assertEqual(sorted(item.name for item in self.small.iterdir()), before)

    def test_missing_small_without_download_never_imports_the_hub(self):
        with patch.dict("sys.modules", {"huggingface_hub": None}), self.assertRaises(ModelSetupError) as error:
            setup_model(model="small")
        self.assertIn("setup-model --model small --download", str(error.exception))
        self.assertFalse(self.small.exists())

    def test_download_uses_the_pinned_small_revision_and_verifies(self):
        hub, modules = self.fake_hub()
        with modules:
            self.assertEqual(setup_model(model="small", download=True), self.small)
        hub.snapshot_download.assert_called_once()
        options = hub.snapshot_download.call_args.kwargs
        self.assertEqual(options["repo_id"], "Systran/faster-whisper-small")
        self.assertEqual(options["revision"], "536b0662742c02347bc0e980a01041f333bce120")
        self.assertEqual(options["allow_patterns"], list(MODEL_FILES))
        self.assertEqual(options["local_dir"], str(self.small))
        self.assertIs(options["token"], False)

    def test_downloaded_files_that_do_not_match_are_rejected(self):
        hub, modules = self.fake_hub(writes={**self.contents, "tokenizer.json": b"tampered"})
        with modules, self.assertRaises(ModelSetupError) as error:
            setup_model(model="small", download=True)
        self.assertIn("tokenizer.json", str(error.exception))
        self.assertTrue((self.small / "tokenizer.json").exists())

    def test_complete_small_is_reused_without_download(self):
        self.install()
        hub, modules = self.fake_hub()
        with modules:
            setup_model(model="small", download=True)
        hub.snapshot_download.assert_not_called()

    def test_cross_model_downloads_are_refused(self):
        hub, modules = self.fake_hub()
        with modules:
            with self.assertRaisesRegex(ModelSetupError, "Use --model base"):
                setup_model(self.base, model="small", download=True)
            with self.assertRaisesRegex(ModelSetupError, "Use --model small"):
                setup_model(self.small, model="base", download=True)
        hub.snapshot_download.assert_not_called()

    def test_unknown_model_is_rejected(self):
        with self.assertRaisesRegex(ModelSetupError, "Unknown speech model"):
            setup_model(model="large")

    def test_verify_files_helper(self):
        self.install()
        self.assertEqual(verify_files(self.small, models.MODEL_SOURCES["small"].files), self.small)


class PinnedReleaseTests(unittest.TestCase):
    def test_small_pins_match_the_verified_release(self):
        source = models.MODEL_SOURCES["small"]
        self.assertEqual((source.repo, source.revision),
                         ("Systran/faster-whisper-small", "536b0662742c02347bc0e980a01041f333bce120"))
        files = {name: (size, digest) for name, size, digest in source.files}
        self.assertEqual(set(files), set(MODEL_FILES))
        self.assertEqual(files["model.bin"],
                         (483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"))
        self.assertTrue(all(len(digest) == 64 for _size, digest in files.values()))
        base = models.MODEL_SOURCES["base"]
        self.assertEqual((base.repo, base.revision, base.files),
                         ("Systran/faster-whisper-base", "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66", ()))


class SetupModelCliTests(unittest.TestCase):
    def invoke(self, arguments):
        output, errors = StringIO(), StringIO()
        with patch("aqua.cli.setup_model", side_effect=lambda path, **_k: path) as setup, \
                redirect_stdout(output), redirect_stderr(errors):
            status = cli.main(arguments)
        return status, setup, output.getvalue()

    def test_small_verify_only_by_default(self):
        status, setup, output = self.invoke(["setup-model", "--model", "small"])
        self.assertEqual(status, 0)
        setup.assert_called_once_with(models.MODEL_DIRS["small"], download=False, model="small")
        self.assertIn("Offline small model ready", output)
        self.assertNotIn("Setting up", output)

    def test_small_download_announces_size(self):
        status, setup, output = self.invoke(["setup-model", "--model", "small", "--download"])
        self.assertEqual(status, 0)
        setup.assert_called_once_with(models.MODEL_DIRS["small"], download=True, model="small")
        self.assertIn("approximately 486 MB", output)

    def test_default_stays_base(self):
        _status, setup, _output = self.invoke(["setup-model"])
        setup.assert_called_once_with(models.MODEL_DIRS["base"], download=False, model="base")

    def test_unknown_model_choice_fails(self):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaises(SystemExit) as exited:
            cli.main(["setup-model", "--model", "large"])
        self.assertEqual(exited.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
