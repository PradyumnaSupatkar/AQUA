"""Capture exactly five seconds of mono int16 PCM in process memory only."""

from collections.abc import Callable
from dataclasses import dataclass

from aqua.audio.backend import AudioError, get_backend
from aqua.audio.devices import DeviceInfo, resolve_input_device
from aqua.audio.levels import AudioLevels, LevelMeter

RECORD_SECONDS = 5


@dataclass(frozen=True, slots=True)
class Recording:
    pcm: bytes
    sample_rate: int
    levels: AudioLevels
    overflow_count: int
    device_info: DeviceInfo | None = None

    @property
    def frames(self) -> int:
        return len(self.pcm) // 2

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate


def record_test(
    *,
    device: int | None = None,
    sample_rate: int | None = None,
    on_progress: Callable[[float, AudioLevels], None] | None = None,
) -> Recording:
    """Record five seconds; explicit cleanup releases the mic even on Ctrl+C.

    Progress levels cover all captured samples so far. Overflow reports mean
    the five seconds of returned samples may contain gaps in wall-clock time.
    No audio is saved or played; callers control the returned bytes' lifetime.
    """
    if sample_rate is not None and (not isinstance(sample_rate, int) or sample_rate <= 0):
        raise ValueError("Sample rate must be a positive integer.")
    selected = resolve_input_device(device)
    rate = sample_rate if sample_rate is not None else round(selected.default_sample_rate)
    if rate <= 0:
        raise AudioError("The device has no valid default sample rate. Specify --sample-rate.")
    backend = get_backend()
    pcm = bytearray()
    meter = LevelMeter()
    overflow_count = 0
    target_frames = RECORD_SECONDS * rate
    block_frames = max(1, rate // 10)
    captured_frames = 0
    try:
        backend.check_input_settings(
            device=selected.index, channels=1, dtype="int16", samplerate=rate
        )
        stream = backend.RawInputStream(
            device=selected.index, channels=1, dtype="int16", samplerate=rate, latency="high"
        )
        try:
            stream.start()
            while captured_frames < target_frames:
                frames = min(block_frames, target_frames - captured_frames)
                data, overflowed = stream.read(frames)
                chunk = bytes(data)
                if len(chunk) != frames * 2:
                    raise AudioError("The microphone returned incomplete audio. Try another input device.")
                pcm.extend(chunk)
                levels = meter.update(chunk)
                overflow_count += bool(overflowed)
                captured_frames += frames
                if on_progress is not None:
                    on_progress(captured_frames / rate, levels)
        finally:
            # PortAudio closes active streams by discarding pending input. Do
            # this even if start/read fails; sounddevice's context manager can
            # skip close if start or stop raises a driver error.
            stream.close()
    except (backend.PortAudioError, ValueError, OSError) as exc:
        raise AudioError(
            f"Could not record from device {selected.index} at {rate} Hz: {exc}. "
            "Check Windows microphone access, close other recording apps, and try "
            "'aqua devices' or omit --sample-rate to use the device default."
        ) from exc
    return Recording(bytes(pcm), rate, meter.levels, overflow_count, selected)
