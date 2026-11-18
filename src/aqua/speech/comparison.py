"""Compare decoding options against the exact same in-memory recording."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from aqua.audio.recording import Recording
from aqua.speech.models import validate_model
from aqua.speech.recognizer import OfflineRecognizer, Transcript
from aqua.speech.vocabulary import DEFAULT_HOTWORDS, normalize_hotwords


@dataclass(frozen=True, slots=True)
class LoadedModel:
    name: str
    model_dir: Path
    recognizer: OfflineRecognizer
    load_seconds: float


@dataclass(frozen=True, slots=True)
class ComparisonPass:
    model_name: str
    hints_enabled: bool
    transcript: Transcript
    transcription_seconds: float
    audio_seconds: float

    @property
    def real_time_factor(self) -> float:
        """Wall time per captured audio second; lower is faster."""
        return self.transcription_seconds / self.audio_seconds


def load_models(model_dirs: Mapping[str, Path]) -> list[LoadedModel]:
    """Preflight every requested directory before loading any model.

    The CLI calls this before microphone capture, so missing Small cannot
    waste a recording. Each model is loaded once and reused for both passes.
    Load timing includes backend initialization and excludes this preflight.
    """
    for path in model_dirs.values():
        validate_model(path)
    models = []
    for name, path in model_dirs.items():
        started = perf_counter()
        recognizer = OfflineRecognizer(path)
        elapsed = perf_counter() - started
        models.append(LoadedModel(name, path, recognizer, elapsed))
    return models


def compare_recording(
    recording: Recording,
    models: Sequence[LoadedModel],
    *,
    hotwords: str = DEFAULT_HOTWORDS,
    language: str | None = None,
    gain_db: float = 0.0,
) -> list[ComparisonPass]:
    """Decode each model with hints off, then on, without recapturing audio.

    Timings include resampling, VAD, and complete transcription, but exclude
    microphone capture and model loading. There is no warm-up pass: later
    passes can benefit from caches. Results do not measure accuracy without
    a human reference transcript.
    """
    if recording.sample_rate <= 0 or not recording.frames:
        raise ValueError("Comparison requires a nonempty recording with a positive sample rate.")
    vocabulary = normalize_hotwords(hotwords)
    passes = []
    for model in models:
        for hints in (False, True):
            options = {"gain_db": gain_db, "language": language}
            if hints:
                options["hotwords"] = vocabulary
            started = perf_counter()
            transcript = model.recognizer.transcribe(
                recording.pcm, recording.sample_rate, **options
            )
            elapsed = perf_counter() - started
            passes.append(
                ComparisonPass(model.name, hints, transcript, elapsed, recording.duration)
            )
    return passes
