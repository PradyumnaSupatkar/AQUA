"""Separate explicit model setup from strictly local inference.

The legacy setup command can explicitly download Base only. Importing
this module, checking setup, and validating a path never access the network or
create directories.
"""

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
                "Install the CTranslate2 multilingual Small model into this directory "
                "through a separately approved setup, or run 'python -m aqua transcribe --model base' "
                "(for comparisons, use 'python -m aqua compare --model base'). "
                "Aqua will not switch models automatically. "
                "Aqua's setup-model command downloads Base only."
            )
        raise ModelSetupError(
            f"The offline speech model is incomplete at {path}; missing or empty: "
            f"{', '.join(missing)}. No download was attempted. To install Base here, "
            "after approving its approximately 148 MB download, run "
            f'python -m aqua setup-model --download --model-dir "{path}".'
        )
    return path


def setup_model(
    model_dir: str | Path = BASE_MODEL_DIR, *, download: bool = False
) -> Path:
    """Validate setup, downloading Base only when explicitly requested.

    Existing complete models are reused without contacting the Hub. An
    interrupted download leaves its files in place for the Hub to resume; this
    function never deletes model files, caches, or any Ollama assets.
    """
    path = _model_path(model_dir)
    if download and _registered_model_name(path) == "small":
        raise ModelSetupError(
            "setup-model --download installs Base only and cannot write into the "
            f"registered Small model directory at {path}. No download was attempted. "
            "Small requires a separately approved installation."
        )
    try:
        return validate_model(path)
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
            repo_id=MODEL_REPO,
            revision=MODEL_REVISION,
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
    return validate_model(path)
