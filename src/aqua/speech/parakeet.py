"""Parakeet TDT 0.6B v3 int8 (NVIDIA, CC-BY-4.0) through sherpa-onnx: Aqua's default recogniser.

Chosen in benchmarks/asr-v1: the lowest word error rate on Aqua's commands and about 6x faster
than Whisper Small with language=en. Strictly local CPU inference from in-memory audio; nothing is
downloaded except by `aqua setup-model --model parakeet --download`.

- Each utterance gets 0.5 s of silence on both sides: without that margin the transducer returned
  nothing for clips that start with speech (benchmark finding).
- No hotwords: contextual biasing made Parakeet less accurate in the benchmark.
- Speech duration for the reliability gate comes from the Silero VAD that faster-whisper bundles.
"""

import hashlib
from math import log10
from pathlib import Path

from aqua.audio.processing import to_16khz
from aqua.speech.models import ModelSetupError, SpeechError
from aqua.speech.recognizer import Transcript

NAME = "parakeet-tdt-0.6b-v3-int8"
PARAKEET_DIR = Path(r"D:\Aqua\models\parakeet-tdt-0.6b-v3-int8")
MODEL_PAGE = "https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3"
# The sherpa-onnx author's export, pinned to one revision; every file by SHA-256 (the ONNX files'
# Hugging Face LFS SHA-256; tokens.txt by SHA-256 of the copy verified against its git blob id).
# tools/asr_models.py (benchmark) uses these same pins.
REPO = "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
REVISION = "ed5d745623472f97cfc2e6c2916ee248798fc5a8"
FILES = (
    ("encoder.int8.onnx", 652282294, "1afbe69c1ffdabee94755f42e4daf1d93549131a3da11356bda84fcef0f2c479"),
    ("decoder.int8.onnx", 11845274, "0222a86147299d4dee90edf95b6d13827edb42ad8b7c9cf0b2582f030937c0b5"),
    ("joiner.int8.onnx", 6355277, "cbd02f3696e9944970111fb439d9c3fd365a80374565f13f2b9c7007d253fc98"),
    ("tokens.txt", 93939, "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d"),
)
APPROXIMATE_MB = 671
PAD_SECONDS = .5
THREADS = 4
SETUP_COMMAND = "aqua setup-model --model parakeet --download"


def _missing_message(path: Path, detail: str) -> str:
    return (f"The Parakeet speech model is not installed or is incomplete at {path} ({detail}). "
            f"No download was attempted. To install it (about {APPROXIMATE_MB} MB), run: {SETUP_COMMAND}. "
            "Or use Whisper: add --asr whisper.")


def verify(model_dir: str | Path = PARAKEET_DIR, *, hashes: bool = True) -> Path:
    """Check every pinned file's size (and SHA-256 unless ``hashes`` is False); never changes files."""
    path = Path(model_dir)
    problems = []
    for name, size, digest in FILES:
        target = path / name
        try:
            if not target.is_file() or target.stat().st_size != size:
                problems.append(f"{name} missing or wrong size")
                continue
            if hashes:
                hasher = hashlib.sha256()
                with target.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        hasher.update(chunk)
                if hasher.hexdigest() != digest:
                    problems.append(f"{name} SHA-256 mismatch")
        except OSError as exc:
            problems.append(f"{name}: {exc}")
    if problems:
        raise ModelSetupError(_missing_message(path, "; ".join(problems)))
    return path


def setup(model_dir: str | Path | None = None, *, download: bool = False) -> Path:
    """Verify the model; download the pinned files only when ``download`` is True."""
    path = Path(model_dir) if model_dir is not None else PARAKEET_DIR
    try:
        return verify(path)
    except ModelSetupError:
        if not download:
            raise
    from aqua.speech.models import HF_CACHE_DIR, configure_runtime
    configure_runtime(offline=False)
    try:
        from huggingface_hub import snapshot_download
    except (ImportError, OSError) as exc:
        raise ModelSetupError("huggingface_hub is unavailable; install Aqua's dependencies first.") from exc
    try:
        snapshot_download(repo_id=REPO, revision=REVISION, allow_patterns=[name for name, _s, _d in FILES],
                          local_dir=str(path), cache_dir=str(HF_CACHE_DIR / "hub"), token=False)
    except Exception as exc:
        raise ModelSetupError(f"Parakeet setup failed: {exc}. Partial files were kept in {path}; "
                              "run the same command again to resume.") from exc
    return verify(path)


def _dbfs(amplitude: float) -> float:
    return 20 * log10(amplitude) if amplitude else float("-inf")


class ParakeetRecognizer:
    """Same interface as OfflineRecognizer: ``transcribe(pcm, sample_rate, ...) -> Transcript``."""

    name = NAME

    def __init__(self, model_dir: str | Path = PARAKEET_DIR) -> None:
        # Sizes only at load time (hashing 670 MB per start is slow); setup-model checks hashes.
        path = verify(model_dir, hashes=False)
        try:
            from aqua.wake.privacy import configure_runtime
            configure_runtime()  # Telemetry off in sherpa's bundled ONNX Runtime before model creation.
            import sherpa_onnx
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(path / "encoder.int8.onnx"), decoder=str(path / "decoder.int8.onnx"),
                joiner=str(path / "joiner.int8.onnx"), tokens=str(path / "tokens.txt"),
                num_threads=THREADS, model_type="nemo_transducer", decoding_method="greedy_search")
        except Exception as exc:
            raise SpeechError(f"Could not load the local Parakeet model at {path}: {exc}. No download was "
                              "attempted.") from exc
        self._vad = None

    def _speech_seconds(self, audio) -> float:
        try:
            from faster_whisper.vad import VadOptions, get_speech_timestamps
            spans = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300, speech_pad_ms=200))
            return sum(span["end"] - span["start"] for span in spans) / 16000
        except Exception:
            return len(audio) / 16000  # No VAD: the whole clip counts.

    def transcribe(self, pcm: bytes, sample_rate: int, *, gain_db: float = 0.0, language: str | None = None,
                   hotwords: str | None = None) -> Transcript:
        """English recognition. ``language`` and ``hotwords`` are accepted for interface
        compatibility and ignored (Parakeet detects language itself; hotwords hurt accuracy)."""
        try:
            import numpy as np
            audio = to_16khz(pcm, sample_rate, gain_db=gain_db)
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            rms = float(np.sqrt(np.mean(audio * audio, dtype=np.float64))) if audio.size else 0.0
            saturated = int(np.count_nonzero(np.abs(audio) >= 1.0))
            if not peak:
                return Transcript("", "en", 0.0, 0.0, _dbfs(peak), _dbfs(rms), saturated)
            pad = np.zeros(round(PAD_SECONDS * 16000), dtype=np.float32)
            padded = np.concatenate([pad, audio.astype(np.float32), pad])
            stream = self._recognizer.create_stream()
            stream.accept_waveform(16000, padded)
            self._recognizer.decode_stream(stream)
            text = " ".join(stream.result.text.split())
            return Transcript(text, "en", 1.0, self._speech_seconds(audio), _dbfs(peak), _dbfs(rms), saturated)
        except SpeechError:
            raise
        except Exception as exc:
            raise SpeechError(f"Parakeet speech recognition failed: {exc}") from exc
