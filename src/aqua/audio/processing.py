"""Prepare in-memory mono microphone PCM for Whisper's 16 kHz input."""

from __future__ import annotations

from math import isfinite
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

WHISPER_SAMPLE_RATE = 16000
MIN_GAIN_DB = -30.0
MAX_GAIN_DB = 30.0


def to_16khz(pcm: bytes, sample_rate: int, *, gain_db: float = 0.0) -> np.ndarray:
    """Resample native-endian mono int16 PCM and return a float32 waveform.

    PyAV uses an anti-alias filter when changing rates. The original sample
    rate is attached to the source frame, and delayed output is flushed.
    Optional gain changes only the returned recognition input; it does not
    change Windows microphone gain or improve the source signal-to-noise ratio.
    Gain is never chosen automatically. Output is clipped to [-1, 1].
    """
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("Sample rate must be a positive integer.")
    if len(pcm) % 2:
        raise ValueError("int16 PCM must contain complete two-byte samples.")
    if not isfinite(gain_db) or not MIN_GAIN_DB <= gain_db <= MAX_GAIN_DB:
        raise ValueError(f"Gain must be finite and between {MIN_GAIN_DB:g} and {MAX_GAIN_DB:g} dB.")

    # Lazy imports preserve microphone diagnostics without the STT stack.
    import numpy as np

    source = np.frombuffer(pcm, dtype=np.int16)
    if source.size == 0:
        return np.empty(0, dtype=np.float32)
    if sample_rate == WHISPER_SAMPLE_RATE:
        audio = source.astype(np.float32) / np.float32(32768.0)
    else:
        import av

        frame = av.AudioFrame.from_ndarray(source.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = sample_rate
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=WHISPER_SAMPLE_RATE)
        frames = resampler.resample(frame)
        frames.extend(resampler.resample(None))
        audio = (
            np.concatenate([part.to_ndarray().reshape(-1) for part in frames])
            if frames else np.empty(0, dtype=np.float32)
        )
        if audio.size == 0:
            raise ValueError("Audio is too short to resample at this input rate.")
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    if gain_db:
        audio *= np.float32(10 ** (gain_db / 20))
    np.clip(audio, -1.0, 1.0, out=audio)
    return audio
