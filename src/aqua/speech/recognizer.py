"""Offline-only CPU INT8 speech recognition from in-memory microphone PCM."""

from dataclasses import dataclass
from math import log10
from math import isfinite
from pathlib import Path

from aqua.audio.processing import to_16khz
from aqua.speech.models import (
    DEFAULT_MODEL_DIR,
    SpeechError,
    configure_runtime,
    validate_model,
)
from aqua.speech.vocabulary import normalize_hotwords


def _whisper_model_class():
    # Disable ONNX Runtime telemetry before Silero VAD creates any sessions.
    import onnxruntime

    onnxruntime.disable_telemetry_events()
    from faster_whisper import WhisperModel

    return WhisperModel


def _dbfs(amplitude: float) -> float:
    return 20 * log10(amplitude) if amplitude else float("-inf")


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    language: str | None
    language_probability: float
    speech_seconds: float
    input_peak_dbfs: float
    input_rms_dbfs: float
    saturated_samples: int
    avg_logprob: float | None = None
    no_speech_probability: float | None = None
    compression_ratio: float | None = None


class OfflineRecognizer:
    """Load a complete local model before recording; never resolve a model ID."""

    def __init__(self, model_dir: Path | str = DEFAULT_MODEL_DIR) -> None:
        model_path = validate_model(model_dir)
        self.name = f"whisper {model_path.name.removeprefix('faster-whisper-')}"
        configure_runtime(offline=True)
        try:
            model_class = _whisper_model_class()
            self._model = model_class(
                str(model_path),
                device="cpu",
                compute_type="int8",
                cpu_threads=4,
                num_workers=1,
                local_files_only=True,
            )
        except Exception as exc:
            raise SpeechError(
                f"Could not load the local speech model at {model_path}: {exc}. "
                "Check the installed dependencies and local model files; no download was attempted."
            ) from exc

    def transcribe(
        self,
        pcm: bytes,
        sample_rate: int,
        *,
        gain_db: float = 0.0,
        language: str | None = None,
        hotwords: str | None = None,
    ) -> Transcript:
        """Resample, optionally amplify, and consume all generated text locally.

        Silero VAD is bundled in faster-whisper, so speech filtering requires
        no additional model download. Energy diagnostics remain independent.
        Optional hotwords supply spelling context only. No prefix, initial
        prompt, or transcript replacement is used; None preserves V0.2 options.
        """
        try:
            import numpy as np

            hint_options = {} if hotwords is None else {"hotwords": normalize_hotwords(hotwords)}
            audio = to_16khz(pcm, sample_rate, gain_db=gain_db)
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            rms = float(np.sqrt(np.mean(audio * audio, dtype=np.float64))) if audio.size else 0.0
            saturated = int(np.count_nonzero(np.abs(audio) >= 1.0))
            if not peak:
                return Transcript("", language, 0.0, 0.0, _dbfs(peak), _dbfs(rms), saturated)
            segments, info = self._model.transcribe(
                audio,
                language=language,
                task="transcribe",
                beam_size=5,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300, "speech_pad_ms": 200},
                **hint_options,
            )
            # Inference happens while iterating: keep generator errors inside
            # this boundary and never mistake generator creation for success.
            pieces = []
            confidence = {"avg_logprob": [], "no_speech_prob": [], "compression_ratio": []}
            for segment in segments:
                if segment.text.strip():
                    pieces.append(segment.text.strip())
                    for name, values in confidence.items():
                        value = getattr(segment, name, None)
                        if isinstance(value, (int, float)) and isfinite(value):
                            values.append(float(value))
            text = " ".join(pieces)
            logprobs = confidence["avg_logprob"]
            no_speech = confidence["no_speech_prob"]
            compression = confidence["compression_ratio"]
            return Transcript(
                text,
                info.language,
                info.language_probability,
                info.duration_after_vad,
                _dbfs(peak),
                _dbfs(rms),
                saturated,
                sum(logprobs) / len(logprobs) if logprobs else None,
                max(no_speech) if no_speech else None,
                max(compression) if compression else None,
            )
        except Exception as exc:
            raise SpeechError(f"Offline speech recognition failed: {exc}") from exc
