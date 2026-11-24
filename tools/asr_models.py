"""Pinned speech models for tools/benchmark_asr.py (benchmark only; Aqua's default is unchanged).

Every file is pinned, like `aqua setup-voice`: nothing is extracted or loaded unless its size and
hash match. Models live in D:\\Aqua\\.cache\\asr-models (gitignored).

Sources (the k2-fsa GitHub release served ~60-80 KB/s on this connection, Hugging Face ~3 MB/s):
- Parakeet TDT 0.6B v3 / v2 int8: the sherpa-onnx author's own Hugging Face repos
  (csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 and -v2-int8), the same sherpa-onnx
  exports as the k2-fsa "asr-models" release. Each repo is pinned to one revision; every ONNX file
  by its Hugging Face LFS SHA-256, tokens.txt and README.md by their git blob ids. Licence:
  CC-BY-4.0 (NVIDIA), as the repos state.
- Whisper small.en: Systran/faster-whisper-small.en at one pinned revision (same scheme). MIT.
- Moonshine v2 English (base/tiny, quantized 2026-02-27): csukuangfj2/sherpa-onnx-moonshine-*-en-
  quantized-2026-02-27. There is no copy under csukuangfj or the k2-fsa organisation itself; the
  user approved csukuangfj2 because that account is a member of the k2-fsa Hugging Face
  organisation (same full name, "fangjun", as csukuangfj). Pinned like the others: .ort files by
  LFS SHA-256, tokens.txt and LICENSE by git blob id. Licence: see LICENSE in each repo (noted in
  benchmarks/asr-v1/report.md).
- The same models' official GitHub archives can also be placed in the folder above by hand: this
  tool verifies each archive's size and the SHA-256 GitHub publishes for it before extracting
  (never extracts an unverified file) and never downloads them.

    .venv\\Scripts\\python.exe tools\\asr_models.py     download small.en, verify + extract archives
"""

import hashlib
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / ".cache" / "asr-models"
RELEASE_PAGE = "https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models"
USER_AGENT = "aqua-asr-benchmark"

# name: (archive file, bytes, SHA-256, licence note) -- official GitHub release assets, local only.
ARCHIVES = {
    "sherpa-onnx-moonshine-base-en-quantized-2026-02-27": (
        "sherpa-onnx-moonshine-base-en-quantized-2026-02-27.tar.bz2", 111266225,
        "43232c1d13013d37317163baec3135bd771a186a4356f28c889bab453bb0e891",
        "Moonshine v2 base (English): see LICENSE in the archive"),
    "sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27": (
        "sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27.tar.bz2", 29858559,
        "9ec31b342d8fa3240c3b81b8f82e1cf7e3ac467c93ca5a999b741d5887164f8d",
        "Moonshine v2 tiny (English): see LICENSE in the archive"),
}

# name: (repo, revision, {file: (bytes, git blob sha1 or None, LFS SHA-256 or None)}, licence)
HF_MODELS = {
    "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8": (
        "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8", "ed5d745623472f97cfc2e6c2916ee248798fc5a8", {
            "encoder.int8.onnx": (652282294, None, "1afbe69c1ffdabee94755f42e4daf1d93549131a3da11356bda84fcef0f2c479"),
            "decoder.int8.onnx": (11845274, None, "0222a86147299d4dee90edf95b6d13827edb42ad8b7c9cf0b2582f030937c0b5"),
            "joiner.int8.onnx": (6355277, None, "cbd02f3696e9944970111fb439d9c3fd365a80374565f13f2b9c7007d253fc98"),
            "tokens.txt": (93939, "fc43e1c723e262df60b70e1919614417162d1fe2", None),
            "README.md": (4905, "c53a4e8f91fcd5988e4b87355693c5a06597d346", None),
        }, "NVIDIA Parakeet TDT 0.6B v3: CC-BY-4.0 (attribution required; commercial use allowed)"),
    "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8": (
        "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8", "84b478d541cdbf0c296a7ab5f1e20a6f8f998114", {
            "encoder.int8.onnx": (652282309, None, "92a10bd9c23936ea0566c29f963bc57c2a5df4261b6a8921c39abea66557ca8b"),
            "decoder.int8.onnx": (7257753, None, "4d097bae3bac5c96018d5293ba39f1b657296102b142affda40378f38b020f41"),
            "joiner.int8.onnx": (1739080, None, "77a29f3e5ac7d37074e0af465391b80f314015d5b97f0f90c6c7767f1d56f428"),
            "tokens.txt": (9384, "f0742785f6073e80b911964c455b05f3609bf23b", None),
            "README.md": (4736, "ebceef90393e92288862a29db023bd43c88bc611", None),
        }, "NVIDIA Parakeet TDT 0.6B v2 (English): CC-BY-4.0"),
    # Approved by the user: csukuangfj2 is a member of the k2-fsa Hugging Face organisation.
    "sherpa-onnx-moonshine-base-en-quantized-2026-02-27": (
        "csukuangfj2/sherpa-onnx-moonshine-base-en-quantized-2026-02-27", "8f4d6c58c03d40bcea40043bb7120a878f2bbef6", {
            "encoder_model.ort": (31326816, None, "7c66495948d0d08ec1af454cd4b5514862ae6511e94712a60e6d83eaec8dc8cf"),
            "decoder_model_merged.ort": (109424400, None,
                                         "d9d7b333af34bc552580576ddcf248a1c6c839e0d3b43b09afb9376ed009899d"),
            "tokens.txt": (549350, "138e8d16af070f5c615e88cfc5432560c3cbf480", None),
            "LICENSE": (13344, "f2b06557a63284695955aba3880ee7f5dab123f3", None),
        }, "Moonshine v2 base (English): see LICENSE"),
    "sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27": (
        "csukuangfj2/sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27", "d1e6c30921780b8508d04b492dfb3ce8a51605d4", {
            "encoder_model.ort": (13281600, None, "94e90a4654fc45cdfedb77c4c08e1739f48862998e58fada384b25118134f221"),
            "decoder_model_merged.ort": (30412256, None,
                                         "cf524c4862d36e9e5ab032eddc73637efd822d70e868ac575cf1a46e1e4708a0"),
            "tokens.txt": (549350, "138e8d16af070f5c615e88cfc5432560c3cbf480", None),
            "LICENSE": (13344, "f2b06557a63284695955aba3880ee7f5dab123f3", None),
        }, "Moonshine v2 tiny (English): see LICENSE"),
    "faster-whisper-small.en": (
        "Systran/faster-whisper-small.en", "d1d751a5f8271d482d14ca55d9e2deeebbae577f", {
            "config.json": (2657, "ced3fd5bbb3e9f7664658065d2d36164712cf1d5", None),
            "tokenizer.json": (2128466, "15d7bdf9ba25718ca2504eec6a8f02bc55af0a6a", None),
            "vocabulary.txt": (422309, "ee695b8d3e3c10d488304e04468efec4ca27554a", None),
            "model.bin": (483545366, None, "62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a"),
        }, "OpenAI Whisper small.en (CTranslate2 conversion by Systran): MIT"),
}

for _f, _s, _sha, _l in ARCHIVES.values():
    assert len(_sha) == 64 and set(_sha) <= set("0123456789abcdef")
for _repo, _rev, _files, _l in HF_MODELS.values():
    assert len(_rev) == 40
    for _size, _blob, _lfs in _files.values():
        assert (_blob and len(_blob) == 40) or (_lfs and len(_lfs) == 64)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_blob(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _fetch(url: str, target: Path, size: int) -> None:
    part = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, open(part, "wb") as out:
        got = 0
        while block := response.read(1 << 20):
            out.write(block)
            got += len(block)
            if got > size:
                break
    part.replace(target)


def _safe_extract(archive: Path, into: Path) -> None:
    with tarfile.open(archive, "r:bz2") as tar:
        tar.extractall(into, filter="data")  # No absolute paths, links out of the folder or devices.


def download(names=None, log=print) -> None:
    """Fetch the Hugging Face models (verified); never downloads the GitHub archives."""
    MODELS.mkdir(parents=True, exist_ok=True)
    for name, (repo, revision, files, _licence) in HF_MODELS.items():
        if names and name not in names:
            continue
        folder = MODELS / name
        folder.mkdir(parents=True, exist_ok=True)
        for file, (size, blob, sha) in files.items():
            path = folder / file
            good = lambda: (path.exists() and path.stat().st_size == size  # noqa: E731
                            and (blob is None or _git_blob(path) == blob) and (sha is None or _sha256(path) == sha))
            if not good():
                log(f"fetch   {repo}/{file} ({size / 1e6:.1f} MB)", flush=True)
                _fetch(f"https://huggingface.co/{repo}/resolve/{revision}/{file}", path, size)
            if not good():
                path.unlink(missing_ok=True)
                raise SystemExit(f"{repo}/{file}: size or hash does not match the pin; deleted.")
        log(f"ok      {name} ({repo} at {revision[:12]})", flush=True)


def extract_local(names=None, log=print) -> list[str]:
    """Verify and extract archives the user placed in MODELS; returns the names still missing.
    Never downloads, and never extracts a file whose size or SHA-256 does not match."""
    missing = []
    for name, (file, size, sha, _licence) in ARCHIVES.items():
        if names and name not in names:
            continue
        if (MODELS / name).is_dir():
            log(f"ok      {name} (extracted)", flush=True)
            continue
        archive = MODELS / file
        if not archive.exists():
            log(f"missing {file}: download it from {RELEASE_PAGE} into {MODELS}", flush=True)
            missing.append(name)
            continue
        if archive.stat().st_size != size:
            log(f"wrong   {file}: {archive.stat().st_size} bytes, expected {size} (incomplete download?); "
                "not extracted", flush=True)
            missing.append(name)
            continue
        if _sha256(archive) != sha:
            log(f"wrong   {file}: SHA-256 does not match the pin; not extracted. Delete it and download again.",
                flush=True)
            missing.append(name)
            continue
        log(f"verify  {file}: size and SHA-256 ok; extracting", flush=True)
        _safe_extract(archive, MODELS)
        archive.unlink()
    return missing


if __name__ == "__main__":
    selected = set(sys.argv[1:]) or None
    download(selected)
    sys.exit(1 if extract_local(selected) else 0)
