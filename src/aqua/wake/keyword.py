"""Small CPU acoustic detector for exactly Hey Aqua, with local assets only."""

from dataclasses import dataclass
from math import isfinite
from pathlib import Path

import numpy as np

from .models import WakeError, validate_model
from .privacy import configure_runtime

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512
WAKE_PHRASE = "Hey Aqua"
WAKE_LABEL = "HEY_AQUA"
# Derived from the pinned SentencePiece model, then checked against tokens.txt.
# Fixed pieces avoid an extra sentencepiece dependency at runtime/setup.
WAKE_TOKENS = ("▁HE", "Y", "▁A", "QU", "A")
KEYWORDS_PATH = Path(__file__).with_name("hey_aqua.keywords")


@dataclass(frozen=True)
class WakeEvent:
    keyword: str
    audio_seconds: float
    timestamps: tuple[float, ...] = ()


class SherpaWakeDetector:
    def __init__(self, model_dir: str | Path | None = None, *, threshold: float = .25, score: float = 1.0):
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not isfinite(threshold) or not 0 < threshold <= 1:
            raise WakeError("Wake threshold must be finite and greater than 0 and at most 1.")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not isfinite(score) or not 0 <= score <= 10:
            raise WakeError("Wake score must be finite and between 0 and 10.")
        path = validate_model(model_dir)
        try:
            vocabulary = {line.rsplit(maxsplit=1)[0] for line in (path / "tokens.txt").read_text(encoding="utf-8").splitlines() if line.strip()}
            if not set(WAKE_TOKENS) <= vocabulary:
                raise WakeError("The local model cannot encode the exact Hey Aqua phrase.")
            if KEYWORDS_PATH.read_text(encoding="utf-8").strip() != " ".join(WAKE_TOKENS) + " @" + WAKE_LABEL:
                raise WakeError("Aqua's fixed Hey Aqua keyword definition is missing or invalid.")
            configure_runtime()
            import sherpa_onnx
            self._spotter = sherpa_onnx.KeywordSpotter(
                tokens=str(path / "tokens.txt"),
                encoder=str(path / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
                decoder=str(path / "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
                joiner=str(path / "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
                keywords_file=str(KEYWORDS_PATH),
                num_threads=1, provider="cpu", sample_rate=SAMPLE_RATE,
                keywords_score=float(score), keywords_threshold=float(threshold),
                max_active_paths=4, num_trailing_blanks=1,
            )
            self._stream = self._spotter.create_stream()
            self._samples = 0
        except (ImportError, OSError, RuntimeError, ValueError, AssertionError) as exc:
            if isinstance(exc, WakeError):
                raise
            raise WakeError("Could not load the local Hey Aqua detector; install the approved handsfree extra and check its model files.") from exc

    def feed(self, frame: np.ndarray) -> WakeEvent | None:
        try:
            samples = np.asarray(frame, dtype=np.float32)
        except (TypeError, ValueError, OverflowError) as exc:
            raise WakeError("Wake input must contain normalized mono audio samples.") from exc
        if samples.ndim != 1 or samples.size != FRAME_SAMPLES or not np.isfinite(samples).all() or np.max(np.abs(samples)) > 1:
            raise WakeError("Wake input must be one 512-sample mono float32 frame at 16 kHz, within [-1, 1].")
        try:
            self._samples += samples.size
            self._stream.accept_waveform(SAMPLE_RATE, samples)
            while self._spotter.is_ready(self._stream):
                self._spotter.decode_stream(self._stream)
                result = self._spotter.get_result(self._stream)
                if not result:
                    continue
                if result != WAKE_LABEL:
                    self.reset()
                    raise WakeError("The wake detector returned a keyword other than Hey Aqua.")
                timestamps = tuple(float(value) for value in self._spotter.timestamps(self._stream))
                event = WakeEvent(WAKE_PHRASE, self._samples / SAMPLE_RATE, timestamps)
                self.reset()
                return event
            return None
        except (OSError, RuntimeError, ValueError, TypeError, AssertionError) as exc:
            if isinstance(exc, WakeError):
                raise
            raise WakeError("The local wake detector could not process this audio frame.") from exc

    def reset(self) -> None:
        """Discard acoustic state without reloading any model weights."""
        try:
            self._spotter.reset_stream(self._stream)
            self._stream = self._spotter.create_stream()
            self._samples = 0
        except (OSError, RuntimeError, ValueError, AssertionError) as exc:
            raise WakeError("Could not reset the local wake detector safely.") from exc
