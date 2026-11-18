"""Separate explicit model setup from strictly local inference.

setup-model can explicitly download Base or Small, each pinned to one Hugging
Face revision. Importing this module, checking setup, and validating a path
never access the network or create directories.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

DEFAULT_MODEL = "small"
MODEL_DIRS = {
    "base": Path(r"D:\Aqua\models\faster-whisper-base"),
    "small": Path(r"D:\Aqua\models\faster-whisper-small"),
}
DEFAULT_MODEL_DIR = MODEL_DIRS[DEFAULT_MODEL]
BASE_MODEL_DIR = MODEL_DIRS["base"]
HF_CACHE_DIR = Path(r"D:\Aqua\.cache\huggingface")
MODEL_REPO = "Systran/faster-whisper-base"
MODEL_REVISION = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
SMALL_MODEL_REPO = "Systran/faster-whisper-small"
SMALL_MODEL_REVISION = "536b0662742c02347bc0e980a01041f333bce120"


@dataclass(frozen=True, slots=True)
class ModelSource:
    """One pinned Hugging Face snapshot; ``files`` maps name -> (bytes, SHA-256)."""
    repo: str
    revision: str
    approximate_mb: int
    files: tuple[tuple[str, int, str], ...] = ()


# Small's sizes and hashes come from the previously verified installation and
# match the sizes, LFS SHA-256 (model.bin) and git blob ids Hugging Face lists
# for this revision. Base keeps its original presence-only verification.
MODEL_SOURCES = {
    "base": ModelSource(MODEL_REPO, MODEL_REVISION, 148),
    "small": ModelSource(SMALL_MODEL_REPO, SMALL_MODEL_REVISION, 486, (
        ("config.json", 2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
        ("model.bin", 483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
        ("tokenizer.json", 2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
        ("vocabulary.txt", 459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
    )),
}


class SpeechError(RuntimeError):
    """A local speech-recognition operation could not complete."""


class ModelSetupError(SpeechError):
    """Recognition dependencies or model files need explicit setup."""


def configure_runtime(*, offline: bool = True) -> None:
    """Configure privacy and D: caches before importing inference libraries.

    Offline execution additionally passes an already-validated local directory
    and ``local_files_only=True`` to the model, so cache flags are not the only
    barrier to an accidental download. Online mode is reserved for setup.
    """
    os.environ["HF_HOME"] = str(HF_CACHE_DIR)
    os.environ["HF_HUB_CACHE"] = str(HF_CACHE_DIR / "hub")
    os.environ["HF_XET_CACHE"] = str(HF_CACHE_DIR / "xet")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    # Plain HTTPS downloads avoid an extra transfer/cache layer for this model.
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1" if offline else "0"
    os.environ["TRANSFORMERS_OFFLINE"] = "1" if offline else "0"


def _model_path(model_dir: str | Path) -> Path:
    try:
        return Path(model_dir).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise ModelSetupError(f"Could not resolve model directory {model_dir}: {exc}") from exc


def resolve_model_dir(model: str | None = None, model_dir: str | Path | None = None) -> Path:
    """Select a local directory without loading, downloading, or creating it.

    Small is the recognition default. Explicit names and directory overrides
    are alternatives; neither a missing model nor a custom path triggers a
    fallback or download.
    """
    if model_dir is not None:
        if model is not None:
            raise ModelSetupError("Choose either a named speech model or --model-dir, not both.")
        return Path(model_dir)
    selected = DEFAULT_MODEL if model is None else model
    if selected not in MODEL_DIRS:
        raise ModelSetupError(
            f"Unknown local speech model {model!r}; choose one of: {', '.join(MODEL_DIRS)}. "
            "No download was attempted."
        )
    return MODEL_DIRS[selected]


def _registered_model_name(path: Path) -> str | None:
    return next(
        (name for name, directory in MODEL_DIRS.items() if _model_path(directory) == path),
        None,
    )


def validate_model(model_dir: str | Path = DEFAULT_MODEL_DIR) -> Path:
    """Require every runtime asset locally, including the tokenizer.

    Requiring tokenizer.json matters: faster-whisper can otherwise fall back to
    a remote tokenizer even when some model files are already present.
    """
    path = _model_path(model_dir)
    try:
        missing = [
            name for name in MODEL_FILES
            if not (path / name).is_file() or (path / name).stat().st_size == 0
        ]
    except OSError as exc:
        raise ModelSetupError(f"Could not inspect local model files in {path}: {exc}") from exc
    if missing:
        if _registered_model_name(path) == "small":
            raise ModelSetupError(
                f"The local Small speech model is not installed or is incomplete at {path}; "
                f"missing or empty: {', '.join(missing)}. No download was attempted. "
                "To install it, approve its approximately 486 MB download and run "
                "'python -m aqua setup-model --model small --download'. Or use the Base model: "
                "'python -m aqua transcribe --model base' "
                "(for comparisons, 'python -m aqua compare --model base'). "
                "Aqua will not switch models automatically."
            )
        raise ModelSetupError(
            f"The offline speech model is incomplete at {path}; missing or empty: "
            f"{', '.join(missing)}. No download was attempted. To install Base here, "
            "after approving its approximately 148 MB download, run "
            f'python -m aqua setup-model --download --model-dir "{path}".'
        )
    return path


def verify_files(model_dir: str | Path, files) -> Path:
    """Check pinned sizes and SHA-256 hashes; never modifies or deletes files."""
    path = _model_path(model_dir)
    mismatched = []
    try:
        for name, size, digest in files:
            target = path / name
            if not target.is_file() or target.stat().st_size != size:
                mismatched.append(f"{name} (size)")
                continue
            hasher = hashlib.sha256()
            with target.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    hasher.update(chunk)
            if hasher.hexdigest() != digest:
                mismatched.append(f"{name} (SHA-256)")
    except OSError as exc:
        raise ModelSetupError(f"Could not verify local model files in {path}: {exc}") from exc
    if mismatched:
        raise ModelSetupError(
            f"Local model files in {path} do not match the pinned release: {', '.join(mismatched)}. "
            "No files were deleted."
        )
    return path


def _check(path: Path, source: ModelSource) -> Path:
    validate_model(path)
    return verify_files(path, source.files) if source.files else path


def setup_model(
    model_dir: str | Path | None = None, *, download: bool = False, model: str = "base",
) -> Path:
    """Verify a named model, downloading it only when explicitly requested.

    Without ``model_dir`` the model's registered directory is used. Existing
    complete models are reused without contacting the Hub. An interrupted
    download leaves its files in place for the Hub to resume; this function
    never deletes model files, caches, or any Ollama assets.
    """
    if model not in MODEL_SOURCES:
        raise ModelSetupError(f"Unknown speech model {model!r}; choose one of: {', '.join(MODEL_SOURCES)}.")
    source = MODEL_SOURCES[model]
    path = _model_path(MODEL_DIRS[model] if model_dir is None else model_dir)
    registered = _registered_model_name(path)
    if download and registered is not None and registered != model:
        raise ModelSetupError(
            f"setup-model --model {model} --download cannot write into the registered "
            f"{registered.title()} model directory at {path}. No download was attempted. "
            f"Use --model {registered} to install {registered.title()} there."
        )
    try:
        return _check(path, source)
    except ModelSetupError:
        if not download:
            raise

    configure_runtime(offline=False)
    try:
        from huggingface_hub import snapshot_download
    except (ImportError, OSError) as exc:
        raise ModelSetupError(
            "Speech dependencies are unavailable. Install Aqua's dependencies "
            r"with .\.venv\Scripts\python.exe -m pip install -e ."
        ) from exc
    try:
        # An explicit local_dir keeps both assets and download metadata on D:.
        # No other models, large variants, or auxiliary assets are requested.
        snapshot_download(
            repo_id=source.repo,
            revision=source.revision,
            allow_patterns=list(MODEL_FILES),
            local_dir=str(path),
            cache_dir=str(HF_CACHE_DIR / "hub"),
            token=False,
        )
    except Exception as exc:
        raise ModelSetupError(
            f"Model setup failed: {exc}. Partial files were retained in {path}; "
            "retry the same explicit setup command to resume."
        ) from exc
    return _check(path, source)
