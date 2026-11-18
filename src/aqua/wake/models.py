"""Pinned compact keyword-spotting assets, with explicit online setup only."""

import hashlib
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

DEFAULT_MODEL_DIR = Path(r"D:\Aqua\models\sherpa-kws-en-int8")
MODEL_REPO = "pkufool/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
MODEL_REVISION = "d4664d22a51cefcb2232047e72a21cff36797c53"
MODEL_BASE_URL = f"https://modelscope.cn/models/{MODEL_REPO}/resolve/{MODEL_REVISION}"
MODEL_FILES = {
    "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx": (
        4807159, "1e721676515bcd42a186979733981213c66c80db680e1cc582dfedf3be76e678",
    ),
    "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx": (
        277985, "e40ff43297abe815e8898494c17e71bba2152d9d40fa3eb803f75d0f7533329a",
    ),
    "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx": (
        163380, "eae9da0c7e1e6c6a3f4cc42d167899c388f6c6701b94cb96320e4f55df79624c",
    ),
    "tokens.txt": (
        5006, "fd2ded4050a55d2b1578870ba8697d02371980217806b7558bd0a5cc60f3ba53",
    ),
    "bpe.model": (
        244837, "c8a2a0129c4ab8e463164c142f82d25649661b122c8cd0b7aab5c9e80b90ad24",
    ),
}


class WakeError(RuntimeError):
    """Wake dependencies, model files, or inference could not be used safely."""


def _path(model_dir: str | Path | None) -> Path:
    try:
        return Path(DEFAULT_MODEL_DIR if model_dir is None else model_dir).expanduser().resolve()
    except (OSError, RuntimeError, TypeError) as exc:
        raise WakeError("Could not resolve the local wake-model directory.") from exc


def _verified_file(path: Path, size: int, sha256: str) -> bool:
    if not path.is_file() or path.stat().st_size != size:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == sha256


def validate_model(model_dir: str | Path | None = None) -> Path:
    """Validate every pinned local asset without creating or downloading files."""
    path = _path(model_dir)
    try:
        invalid = [name for name, (size, digest) in MODEL_FILES.items()
                   if not _verified_file(path / name, size, digest)]
    except OSError as exc:
        raise WakeError(f"Could not read the local wake model at {path}.") from exc
    if invalid:
        raise WakeError(
            f"The local Hey Aqua wake model is missing or failed verification at {path}: "
            f"{', '.join(invalid)}. No download was attempted. "
            "Run 'python -m aqua setup-wake --download' only after approving "
            "the approximately 5.5 MB model download; manual voice-chat remains available."
        )
    return path


def setup_model(model_dir: str | Path | None = None, *, download: bool = False) -> Path:
    """Download five immutable assets only when explicitly requested.

    Complete files are reused without a request. An existing invalid final
    file is never overwritten. Failed downloads retain only their own .part
    files for inspection/retry; unrelated files and other models are untouched.
    """
    path = _path(model_dir)
    if not download:
        return validate_model(path)
    try:
        for name, (size, digest) in MODEL_FILES.items():
            target = path / name
            if target.exists():
                if _verified_file(target, size, digest):
                    continue
                raise WakeError(
                    f"Existing wake asset failed verification: {target}. "
                    "It was not overwritten; choose a separate setup directory or inspect it."
                )
            path.mkdir(parents=True, exist_ok=True)
            partial = path / (name + ".part")
            request = Request(f"{MODEL_BASE_URL}/{name}", headers={"User-Agent": "Aqua-model-setup"})
            downloaded = 0
            hasher = hashlib.sha256()
            with urlopen(request, timeout=30) as response, partial.open("wb") as output:
                while block := response.read(min(65536, size + 1 - downloaded)):
                    downloaded += len(block)
                    if downloaded > size:
                        raise WakeError(f"Wake asset exceeded its pinned size: {name}.")
                    output.write(block)
                    hasher.update(block)
            if downloaded != size or hasher.hexdigest() != digest:
                raise WakeError(f"Downloaded wake asset failed size/hash verification: {name}.")
            partial.replace(target)
    except (OSError, URLError) as exc:
        raise WakeError(
            f"Wake-model setup failed at {path}; partial setup files were retained. "
            "No other models were modified."
        ) from exc
    return validate_model(path)
