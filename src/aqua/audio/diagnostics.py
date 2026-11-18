"""Lightweight level/activity diagnostics, without a speech model or file I/O."""

from array import array
from dataclasses import dataclass
from math import ceil, log10, sqrt

from aqua.audio.levels import AudioLevels, measure_levels

FRAME_SECONDS = 0.020
ACTIVITY_FLOOR_DBFS = -55.0


def _dbfs(amplitude: float) -> float:
    return 20 * log10(amplitude) if amplitude else float("-inf")


def _percentile(values: list[float], quantile: float) -> float:
    """Nearest-rank percentile, including an empty-input result."""
    return sorted(values)[max(0, ceil(len(values) * quantile) - 1)] if values else 0.0


@dataclass(frozen=True, slots=True)
class AudioDiagnostics:
    """Original capture measurements; activity is an energy heuristic, not VAD.

    Quiet and p90 frame values summarize the quieter and louder 20ms windows.
    Neither proves the presence/absence of speech or estimates calibrated SPL.
    """

    levels: AudioLevels
    duration: float
    frame_count: int
    active_fraction: float
    active_rms_dbfs: float
    quiet_frame_rms_dbfs: float
    p90_frame_rms_dbfs: float
    activity_threshold_dbfs: float


def analyze_audio(pcm: bytes, sample_rate: int) -> AudioDiagnostics:
    """Measure original PCM before optional recognition gain/resampling.

    The activity gate is 10 dB above the 20th-percentile frame RMS, bounded
    below by -55 dBFS. It is capped at 3 dB below the 90th-percentile frame RMS
    so a steady louder signal is measurable. Background noise can count as
    activity, and quiet speech below the floor can be missed. RMS over active
    frames excludes quiet gaps but is not a definitive speech-only level.
    """
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("Sample rate must be a positive integer.")
    levels = measure_levels(pcm)
    samples = array("h")
    samples.frombytes(pcm)
    frame_samples = max(1, round(sample_rate * FRAME_SECONDS))
    frames: list[tuple[float, int, int]] = []
    for offset in range(0, len(samples), frame_samples):
        block = samples[offset:offset + frame_samples]
        energy = sum(sample * sample for sample in block)
        rms = sqrt(energy / len(block)) / 32768
        frames.append((rms, energy, len(block)))
    quiet_dbfs = _dbfs(_percentile([rms for rms, _, _ in frames], 0.20))
    loud_dbfs = _dbfs(_percentile([rms for rms, _, _ in frames], 0.90))
    threshold_dbfs = max(ACTIVITY_FLOOR_DBFS, min(quiet_dbfs + 10, loud_dbfs - 3))
    threshold = 10 ** (threshold_dbfs / 20)
    active = [(energy, count) for rms, energy, count in frames if rms > threshold]
    active_samples = sum(count for _, count in active)
    active_energy = sum(energy for energy, _ in active)
    active_rms = sqrt(active_energy / active_samples) / 32768 if active_samples else 0.0
    return AudioDiagnostics(
        levels=levels,
        duration=len(samples) / sample_rate,
        frame_count=len(frames),
        active_fraction=active_samples / len(samples) if samples else 0.0,
        active_rms_dbfs=_dbfs(active_rms),
        quiet_frame_rms_dbfs=quiet_dbfs,
        p90_frame_rms_dbfs=loud_dbfs,
        activity_threshold_dbfs=threshold_dbfs,
    )
