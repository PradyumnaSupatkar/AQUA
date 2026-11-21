"""setup-voice: pinned archive download, safe extraction, strict verification.

Everything runs against an in-memory tar.bz2, a fake urlopen and temporary
directories; no network and no real voice files.
"""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import random
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from aqua import cli
from aqua.voice import setup
from aqua.voice.engines import VoiceError
from aqua.voice.setup import ASSETS, VoiceAsset, install, manifest, select_assets, setup_voice, verify

FILES = {"model.onnx": b"synthetic onnx", "tokens.txt": b"a 0\nb 1\n", "espeak-ng-data/phontab": b"phon"}


def tarball(files, *, root="voice-root", extra=()):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as bundle:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{root}/{name}")
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
        for info, data in extra:
            bundle.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class VoiceSetupTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.getaddrinfo", "socket.socket.connect", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Voice setup tests must stay offline."))
            manager.start()
            self.addCleanup(manager.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.manifests = self.root / "manifests"
        self.manifests.mkdir()
        self.use(FILES)

    def use(self, files, archive=None):
        self.archive = archive if archive is not None else tarball(files)
        self.asset = VoiceAsset("fake", "kokoro", None, "fake.tar.bz2", len(self.archive),
                                hashlib.sha256(self.archive).hexdigest(), "voice-root")
        (self.manifests / "fake.json").write_text(json.dumps({"files": {
            name: [len(data), hashlib.sha256(data).hexdigest()] for name, data in files.items()}}))
        for manager in (patch.object(setup, "MANIFEST_DIR", self.manifests),
                        patch.object(setup, "VOICE_ROOT", self.root)):
            manager.start()
            self.addCleanup(manager.stop)

    def serve(self, payload=None):
        payload = self.archive if payload is None else payload
        return patch("aqua.voice.setup.urlopen", side_effect=lambda *_a, **_k: Response(payload))

    def target(self):
        return self.root / "tts-fake"

    def test_verify_only_never_downloads(self):
        with patch("aqua.voice.setup.urlopen", side_effect=AssertionError("no network")):
            with self.assertRaises(VoiceError) as error:
                verify(self.asset, self.target())
        self.assertIn("No download was attempted", str(error.exception))
        self.assertIn("setup-voice --engine kokoro --download", str(error.exception))
        self.assertFalse(self.target().exists())

    def test_install_extracts_only_pinned_files_then_verifies(self):
        extra = tarfile.TarInfo("voice-root/README.md")
        extra.size = 3
        self.use(FILES, tarball(FILES, extra=[(extra, b"doc")]))
        with self.serve() as fetch:
            path = install(self.asset, self.target())
        # A range probe, then a single stream because this fake host ignores ranges.
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args_list[0].args[0].get_header("Range"), f"bytes=0-{len(self.archive) - 1}")
        self.assertIsNone(fetch.call_args.args[0].get_header("Range"))
        self.assertEqual(fetch.call_args.args[0].full_url, self.asset.url)
        self.assertEqual(sorted(str(p.relative_to(path)).replace("\\", "/") for p in path.rglob("*") if p.is_file()),
                         sorted(FILES))
        self.assertFalse((self.root / ".tts-fake.setup").exists())
        with patch("aqua.voice.setup.urlopen", side_effect=AssertionError("reuse offline")):
            self.assertEqual(install(self.asset, path), path)
            self.assertEqual(verify(self.asset, path), path)

    def test_parallel_ranges_assemble_the_exact_archive(self):
        files = {**FILES, "voices.bin": random.Random(7).randbytes(20000)}  # incompressible
        self.use(files, tarball(files))
        ranges = []

        def ranged(request, **_options):
            first, last = map(int, request.get_header("Range").split("=")[1].split("-"))
            ranges.append((first, last))
            response = Response(self.archive[first:last + 1])
            response.status = 206
            return response

        with patch.object(setup, "RANGE_BYTES", 1000), patch("aqua.voice.setup.urlopen", side_effect=ranged):
            path = install(self.asset, self.target())
        self.assertGreater(len(ranges), 3)
        self.assertEqual(sorted(ranges)[-1][1], len(self.archive) - 1)
        self.assertEqual((path / "voices.bin").read_bytes(), files["voices.bin"])

    def test_flaky_range_is_retried(self):
        calls = {"n": 0}

        def flaky(request, **_options):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("reset")
            first, last = map(int, request.get_header("Range").split("=")[1].split("-"))
            response = Response(self.archive[first:last + 1])
            response.status = 206
            return response

        with patch.object(setup, "RANGE_BYTES", 64), patch("aqua.voice.setup.urlopen", side_effect=flaky),                 patch("time.sleep"):
            install(self.asset, self.target())
        self.assertFalse(problems_of(self.asset, self.target()))

    def test_from_archive_installs_without_network_and_checks_the_pin(self):
        local = self.root / "copy.tar.bz2"
        local.write_bytes(self.archive)
        with patch("aqua.voice.setup.urlopen", side_effect=AssertionError("no network")):
            path = install(self.asset, self.target(), archive_path=local)
            self.assertEqual(verify(self.asset, path), path)
            other = self.root / "other.tar.bz2"
            other.write_bytes(self.archive + b"x")
            with self.assertRaisesRegex(VoiceError, "not the pinned"):
                install(self.asset, self.root / "elsewhere", archive_path=other)
        self.assertTrue(local.exists())  # The user's archive is never deleted.

    def test_archive_hash_or_size_mismatch_is_rejected(self):
        for payload in (self.archive[:-1] + b"X", self.archive + b"extra"):
            with self.subTest(length=len(payload)), self.serve(payload), self.assertRaises(VoiceError):
                install(self.asset, self.target())
            self.assertFalse(self.target().exists())

    def test_member_content_mismatch_is_rejected(self):
        tampered = tarball({**FILES, "tokens.txt": b"a 0\nb 2\n"})
        self.use(FILES, tampered)  # Archive pin matches, but a member differs from the manifest.
        with self.serve(), self.assertRaisesRegex(VoiceError, "tokens.txt"):
            install(self.asset, self.target())
        self.assertFalse(self.target().exists())

    def test_traversal_and_link_members_are_never_written(self):
        escape = tarfile.TarInfo("voice-root/../../escaped.txt")
        escape.size = 4
        link = tarfile.TarInfo("voice-root/link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        self.use(FILES, tarball(FILES, extra=[(escape, b"evil"), (link, None)]))
        with self.serve():
            install(self.asset, self.target())
        self.assertFalse((self.root / "escaped.txt").exists())
        self.assertFalse(self.root.parent.joinpath("escaped.txt").exists())
        self.assertFalse((self.target() / "link").exists())

    def test_manifest_with_unsafe_path_is_refused(self):
        (self.manifests / "fake.json").write_text(json.dumps({"files": {"../x": [1, "0" * 64]}}))
        with self.assertRaisesRegex(VoiceError, "unsafe path"):
            manifest(self.asset)

    def test_existing_invalid_directory_is_not_overwritten(self):
        self.target().mkdir()
        (self.target() / "model.onnx").write_bytes(b"local edits")
        with self.serve() as fetch, self.assertRaisesRegex(VoiceError, "not overwritten"):
            install(self.asset, self.target())
        fetch.assert_not_called()
        self.assertEqual((self.target() / "model.onnx").read_bytes(), b"local edits")

    def test_failed_download_keeps_setup_files_and_creates_no_target(self):
        with patch("aqua.voice.setup.urlopen", side_effect=OSError("connection reset")), patch("time.sleep"), \
                self.assertRaisesRegex(VoiceError, "Setup files were kept"):
            install(self.asset, self.target())
        self.assertFalse(self.target().exists())

    def test_verification_detects_a_changed_file(self):
        with self.serve():
            path = install(self.asset, self.target())
        (path / "espeak-ng-data" / "phontab").write_bytes(b"PHON")
        with self.assertRaisesRegex(VoiceError, "phontab"):
            verify(self.asset, path)


class RealPinsTests(unittest.TestCase):
    def test_every_asset_has_a_valid_pinned_manifest(self):
        for asset in ASSETS.values():
            with self.subTest(asset=asset.key):
                files = manifest(asset)
                self.assertTrue(files)
                self.assertEqual(len(asset.sha256), 64)
                self.assertTrue(asset.url.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"))
                self.assertTrue(all(len(digest) == 64 and size > 0 for size, digest in files.values()))
                self.assertIn("tokens.txt", files)
                self.assertTrue(any(name.endswith(".onnx") for name in files))
                # sherpa-onnx 2.x ignores espeak-ng-data; piper_phonemize bundles its own.
                self.assertFalse(any(name.startswith("espeak-ng-data/") for name in files))

    def test_selection(self):
        self.assertEqual([a.key for a in select_assets("piper")], ["piper-lessac", "piper-ryan"])
        self.assertEqual([a.key for a in select_assets("piper", "Ryan")], ["piper-ryan"])
        self.assertEqual([a.key for a in select_assets("kokoro", "af_heart")], ["kokoro"])
        with self.assertRaises(VoiceError):
            select_assets("piper", "nobody")
        with self.assertRaises(VoiceError):
            select_assets("espeak")
        with self.assertRaisesRegex(VoiceError, "single voice"):
            setup_voice("piper", model_dir="D:/x")


class SetupVoiceCliTests(unittest.TestCase):
    def invoke(self, arguments, **patch_options):
        output, errors = StringIO_pair()
        with patch("aqua.cli.setup_voice", **patch_options) as run, redirect_stdout(output), redirect_stderr(errors):
            status = cli.main(arguments)
        return status, run, output.getvalue(), errors.getvalue()

    def test_verify_only_by_default(self):
        status, run, output, _errors = self.invoke(["setup-voice", "--engine", "piper", "--voice", "ryan"],
                                                   return_value=[Path("D:/Aqua/models/tts-piper-ryan")])
        self.assertEqual(status, 0)
        run.assert_called_once_with("piper", "ryan", download=False, model_dir=None, archive=None)
        self.assertIn("tts-piper-ryan", output)

    def test_download_flag_and_errors(self):
        _status, run, _out, _err = self.invoke(["setup-voice", "--engine", "kokoro-int8", "--download"], return_value=[])
        run.assert_called_once_with("kokoro-int8", None, download=True, model_dir=None, archive=None)
        status, _run, _out, errors = self.invoke(["setup-voice", "--engine", "kitten"],
                                                 side_effect=VoiceError("missing; No download was attempted."))
        self.assertEqual(status, 1)
        self.assertIn("Voice setup error", errors)

    def test_engine_is_required_and_validated(self):
        for arguments in (["setup-voice"], ["setup-voice", "--engine", "espeak"]):
            with self.subTest(arguments=arguments), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as exited:
                cli.main(arguments)
            self.assertEqual(exited.exception.code, 2)


def problems_of(asset, path):
    return setup.problems(asset, path)


def StringIO_pair():
    return io.StringIO(), io.StringIO()


if __name__ == "__main__":
    unittest.main()
