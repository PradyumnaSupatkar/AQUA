"""Measure native-endian mono int16 PCM without NumPy or file access."""

from array import array
from dataclasses import dataclass
from math import log10, sqrt


def _dbfs(amplitude: float) -> float:
    return 20 * log10(amplitude) if amplitude else float("-inf")


@dataclass(frozen=True, slots=True)
class AudioLevels:
    """Peak and RMS as fractions of full scale; silence is negative infinity dBFS."""

    peak: float
    rms: float
    clipped_samples: int

    @property
    def peak_dbfs(self) -> float:
        return _dbfs(self.peak)

    @property
    def rms_dbfs(self) -> float:
        return _dbfs(self.rms)


class LevelMeter:
    """Accumulate integer energy to weight blocks correctly, including short ones."""

    def __init__(self) -> None:
        self._peak = 0
        self._sum_squares = 0
        self._count = 0
        self._clipped = 0

    def update(self, pcm: bytes) -> AudioLevels:
        if len(pcm) % 2:
            raise ValueError("int16 PCM must contain complete two-byte samples.")
        samples = array("h")
        samples.frombytes(pcm)
        for sample in samples:
            self._peak = max(self._peak, abs(sample))
            self._sum_squares += sample * sample
            self._clipped += sample in (-32768, 32767)
        self._count += len(samples)
        return self.levels

    @property
    def levels(self) -> AudioLevels:
        rms = sqrt(self._sum_squares / self._count) / 32768 if self._count else 0.0
        return AudioLevels(self._peak / 32768, rms, self._clipped)


def measure_levels(pcm: bytes) -> AudioLevels:
    """Return levels for an in-memory block, including empty or silent input."""
    return LevelMeter().update(pcm)
