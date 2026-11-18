"""Incremental bundled Silero VAD and bounded post-wake utterance capture."""

from dataclasses import dataclass
from math import isfinite

from aqua.audio.backend import AudioError
from aqua.audio.streaming import FRAME_SAMPLES, SAMPLE_RATE


class StreamingVad:
    """Reuse Faster-Whisper's CPU graph, keeping this stream's own RNN state."""

    def __init__(self) -> None:
        try:
            import onnxruntime
            onnxruntime.disable_telemetry_events()
            from faster_whisper.vad import get_vad_model

            self._session = get_vad_model().session
            self.reset()
        except Exception as exc:
            raise AudioError("Could not load the bundled local Silero VAD. Check the existing speech dependencies.") from exc

    def reset(self) -> None:
        import numpy as np

        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)

    def probability(self, frame) -> float:
        import numpy as np

        audio = np.asarray(frame, dtype=np.float32)
        if audio.shape != (FRAME_SAMPLES,) or not np.isfinite(audio).all():
            raise AudioError("VAD requires one finite 512-sample 16 kHz audio frame.")
        try:
            inputs = np.concatenate((self._context, audio.reshape(1, -1)), axis=1)
            output, h, c = self._session.run(None, {"input": inputs, "h": self._h, "c": self._c})
            probability = float(np.asarray(output).reshape(-1)[0])
            if not isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("Invalid VAD probability")
            self._h, self._c = h, c
            self._context = audio[-64:].reshape(1, -1).copy()
            return probability
        except Exception as exc:
            raise AudioError("Local speech activity detection failed; this turn was discarded.") from exc


@dataclass(frozen=True, slots=True)
class UtteranceConfig:
    end_silence: float = .8
    initial_timeout: float = 5.0
    max_utterance: float = 15.0
    pre_roll: float = .5
    min_speech: float = .16
    speech_threshold: float = .5

    def __post_init__(self) -> None:
        bounds = {
            "end_silence": (.1, 3.0), "initial_timeout": (.5, 15.0),
            "max_utterance": (1.0, 30.0), "pre_roll": (0.0, 2.0),
            "min_speech": (.032, 1.0), "speech_threshold": (.01, 1.0),
        }
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be finite and between {low:g} and {high:g}.")
        if self.min_speech > self.max_utterance or self.pre_roll > self.max_utterance:
            raise ValueError("Speech and pre-roll limits must fit the maximum utterance duration.")


@dataclass(frozen=True, slots=True)
class UtteranceEvent:
    status: str
    samples: object | None
    speech_seconds: float
    duration: float


class UtteranceCollector:
    """Capture until sustained silence; never return a truncated long command.

    Pre-roll is context only: the wake phrase in it cannot satisfy meaningful
    post-wake speech. All frame and audio limits use sample counts, not wall
    clock scheduling, so model speed cannot slowly grow buffers or timeouts.
    """

    def __init__(self, config: UtteranceConfig | None = None) -> None:
        self.config = config if config is not None else UtteranceConfig()
        self._active = False
        self._parts = []
        self._pre_samples = self._post_samples = self._speech_samples = self._quiet_samples = 0
        self._speaking = False

    @property
    def duration(self) -> float:
        return (self._pre_samples + self._post_samples) / SAMPLE_RATE

    @property
    def speech_seconds(self) -> float:
        return self._speech_samples / SAMPLE_RATE

    def begin(self, pre_roll) -> None:
        import numpy as np

        audio = np.asarray(pre_roll, dtype=np.float32)
        if audio.ndim != 1 or not np.isfinite(audio).all():
            raise ValueError("Pre-roll must be a finite mono waveform.")
        keep = round(self.config.pre_roll * SAMPLE_RATE)
        audio = audio[-keep:].copy() if keep else np.empty(0, dtype=np.float32)
        self._parts = [audio] if audio.size else []
        self._pre_samples = audio.size
        self._post_samples = self._speech_samples = self._quiet_samples = 0
        self._speaking = False
        self._active = True

    def _finish(self, status: str) -> UtteranceEvent:
        import numpy as np

        samples = np.concatenate(self._parts) if status == "complete" else None
        event = UtteranceEvent(status, samples, self.speech_seconds, self.duration)
        self._active = False
        self._parts = []  # Release audio promptly for rejected turns as well.
        return event

    def push(self, frame, probability: float) -> UtteranceEvent | None:
        import numpy as np

        if not self._active:
            raise ValueError("Begin an utterance before adding audio frames.")
        audio = np.asarray(frame, dtype=np.float32)
        if audio.shape != (FRAME_SAMPLES,) or not np.isfinite(audio).all():
            raise ValueError("Utterances require finite 512-sample audio frames.")
        if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Speech probability must be finite and between zero and one.")
        self._parts.append(audio.copy())
        self._post_samples += FRAME_SAMPLES
        threshold = self.config.speech_threshold
        active = probability >= threshold or (self._speaking and probability >= max(.01, threshold - .15))
        if active:
            self._speaking = True
            self._speech_samples += FRAME_SAMPLES
            self._quiet_samples = 0
        elif self._speaking:
            self._quiet_samples += FRAME_SAMPLES
        # Reject the whole recording, even if a sentence boundary coincides
        # with the cap. A safe action cannot be inferred from a cut-off suffix.
        if self._post_samples >= round(self.config.max_utterance * SAMPLE_RATE):
            return self._finish("too_long")
        if self._speaking and self._quiet_samples >= round(self.config.end_silence * SAMPLE_RATE):
            if self._speech_samples >= round(self.config.min_speech * SAMPLE_RATE):
                return self._finish("complete")
            # A short noise burst is not an utterance. Continue waiting for
            # meaningful speech until the original initial timeout expires.
            self._speech_samples = self._quiet_samples = 0
            self._speaking = False
        if not self._speaking and self._post_samples >= round(self.config.initial_timeout * SAMPLE_RATE):
            return self._finish("no_speech")
        return None
