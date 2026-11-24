"""One bounded microphone stream, continuously resampled to 16 kHz frames."""

from collections import deque
from collections.abc import Callable
from fractions import Fraction
from queue import Empty, Full, Queue
from threading import Lock
from time import monotonic

from aqua.audio.backend import AudioError, get_backend
from aqua.audio.devices import resolve_input_device

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512
QUEUE_BLOCKS = 100          # 2 s of 20 ms blocks: room for the consumer to fall behind briefly.
STALL_SECONDS = 2.0
DEFAULT_LATENCY = "high"    # Larger device buffers: a callback that runs late under CPU load is not lost.


class MicrophoneFailure(AudioError):
    """The input stream itself failed (not one dropped block); ``kind`` says how:
    "stall" (no audio for 2 s), "device" (the stream stopped), "invalid" (bad blocks)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class StreamingResampler:
    """Keep the anti-alias filter and fractional position across source blocks."""

    def __init__(self, sample_rate: int, *, on_resampled_audio: Callable[[object, int], None] | None = None) -> None:
        if type(sample_rate) is not int or not 0 < sample_rate <= 384000:
            raise ValueError("Sample rate must be a positive integer at most 384000 Hz.")
        import numpy as np

        self.sample_rate = sample_rate
        self._on_resampled_audio = on_resampled_audio
        self.diagnostic_errors: list[str] = []
        self._position = 0
        self._pending = np.empty(0, dtype=np.float32)
        self._resampler = None
        if sample_rate != SAMPLE_RATE:
            import av

            self._resampler = av.AudioResampler(format="fltp", layout="mono", rate=SAMPLE_RATE)

    def feed(self, pcm: bytes) -> list:
        """Accept mono int16 PCM; emit complete contiguous float32 frames only.

        A remainder shorter than 32 ms is retained for the next call. The
        resampler is deliberately not flushed between chunks of a live stream.
        """
        import numpy as np

        if len(pcm) % 2:
            raise ValueError("int16 PCM must contain complete two-byte samples.")
        if not pcm:
            return []
        source = np.frombuffer(pcm, dtype=np.int16)
        if self._resampler is None:
            parts = [source.astype(np.float32) / np.float32(32768.0)]
        else:
            import av

            frame = av.AudioFrame.from_ndarray(source.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = self.sample_rate
            frame.time_base = Fraction(1, self.sample_rate)
            frame.pts = self._position
            parts = [part.to_ndarray().reshape(-1) for part in self._resampler.resample(frame)]
        self._position += source.size
        if self._on_resampled_audio is not None and parts:
            # Observe only NEW output, before clipping/framing. A copy keeps
            # diagnostic code from modifying the samples delivered to models.
            # A faulty optional observer must not interrupt or attenuate audio.
            try:
                self._on_resampled_audio(np.concatenate(parts).copy(), SAMPLE_RATE)
            except Exception as exc:
                self.diagnostic_errors.append(f"resampled: {type(exc).__name__}")
                self._on_resampled_audio = None
        combined = np.concatenate([self._pending, *parts]) if parts else self._pending
        np.clip(combined, -1.0, 1.0, out=combined)
        complete = combined.size // FRAME_SAMPLES * FRAME_SAMPLES
        result = [combined[offset:offset + FRAME_SAMPLES].copy() for offset in range(0, complete, FRAME_SAMPLES)]
        self._pending = combined[complete:].copy()
        return result


class MicrophoneStream:
    """A single PortAudio callback stream with bounded, fail-closed buffering.

    Only the consumer thread resamples or runs models. The callback copies a
    small PCM block into a fixed-size queue. Pause discards audio during model
    processing; resume clears old audio and resets the filter across that gap.
    """

    def __init__(
        self, *, device: int | None = None, sample_rate: int | None = None,
        on_native_audio: Callable[[bytes, int], None] | None = None,
        on_resampled_audio: Callable[[object, int], None] | None = None,
        latency: str | float = DEFAULT_LATENCY, queue_blocks: int = QUEUE_BLOCKS,
    ) -> None:
        if sample_rate is not None and (type(sample_rate) is not int or not 0 < sample_rate <= 384000):
            raise ValueError("Sample rate must be a positive integer at most 384000 Hz.")
        self._device = device
        self._requested_rate = sample_rate
        self._on_native_audio = on_native_audio
        self._on_resampled_audio = on_resampled_audio
        self.diagnostic_errors: list[str] = []
        self._resampled_diagnostic_failed = False
        self.device_info = None
        self.native_sample_rate = None
        self._stream = None
        self._backend = None
        self._resampler = None
        self._latency = latency
        self._pcm = Queue(maxsize=queue_blocks)
        self._frames = deque()
        self._lock = Lock()
        self._paused = True
        self._failure = None
        self._block_frames = 0
        self._last_audio_at = None
        # Audio lost without the stream failing: counts by cause, and the cause of the newest
        # gap not yet reported to the consumer (take_gap). The audio around a gap is not continuous.
        self.gaps = {"input_overflow": 0, "queue_full": 0}
        self._pending_gap = None
        self.max_queue_depth = 0
        self.last_status = ""

    def __enter__(self):
        if self._stream is not None:
            raise AudioError("This microphone stream is already open.")
        self.device_info = resolve_input_device(self._device)
        rate = self._requested_rate or round(self.device_info.default_sample_rate)
        if not 0 < rate <= 384000:
            raise AudioError("The microphone has no usable native rate. Specify --sample-rate.")
        self.native_sample_rate = rate
        self._block_frames = max(1, round(rate * .02))
        self._backend = get_backend()
        try:
            self._resampler = StreamingResampler(rate, on_resampled_audio=self._on_resampled_audio)
            self._backend.check_input_settings(device=self.device_info.index, channels=1, dtype="int16", samplerate=rate)
            self._stream = self._backend.RawInputStream(
                device=self.device_info.index, channels=1, dtype="int16", samplerate=rate,
                blocksize=self._block_frames, latency=self._latency, callback=self._callback,
            )
            self._paused = False
            self._last_audio_at = monotonic()
            self._stream.start()
        except (KeyboardInterrupt, SystemExit):
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise AudioError(f"Could not open the microphone at {rate} Hz. Check the input device and Windows microphone permissions.") from exc
        return self

    def _gap(self, kind: str) -> None:
        self.gaps[kind] += 1
        self._pending_gap = kind

    def _callback(self, data, frames, _time, status) -> None:
        with self._lock:
            if self._paused or self._failure is not None:
                return
            self._last_audio_at = monotonic()
            if status:
                # PortAudio flags an input overflow when this callback ran too late (CPU load):
                # some audio was lost before this block. Drop the block and report a gap; the
                # stream itself is fine.
                self.last_status = str(status)  # PortAudio's own words, e.g. "input overflow".
                self._gap("input_overflow")
                return
            if not 0 < frames <= self._block_frames * 4:
                self._failure = MicrophoneFailure("invalid", "The microphone returned an invalid audio block.")
                return
            block = bytes(data)
            if len(block) != frames * 2:
                self._failure = MicrophoneFailure("invalid", "The microphone returned incomplete audio.")
                return
            try:
                self._pcm.put_nowait(block)
                self.max_queue_depth = max(self.max_queue_depth, self._pcm.qsize())
            except Full:
                self._gap("queue_full")  # The consumer fell more than 2 s behind: drop, never block.

    def take_gap(self) -> str | None:
        """The cause of audio lost since the last call ("input_overflow" or "queue_full"), or None.
        Audio around a gap is not continuous, so a capture in progress must be discarded."""
        with self._lock:
            gap, self._pending_gap = self._pending_gap, None
            return gap

    def _clear_buffers(self) -> None:
        self._frames.clear()
        while True:
            try:
                self._pcm.get_nowait()
            except Empty:
                break

    def pause(self) -> None:
        with self._lock:
            self._paused = True
            self._clear_buffers()

    def resume(self) -> None:
        if self._stream is None:
            raise AudioError("The microphone stream is not open.")
        if self._failure is not None:
            raise self._failure
        # The callback keeps dropping data while a fresh filter is prepared.
        self.pause()
        resampler = StreamingResampler(self.native_sample_rate, on_resampled_audio=self._on_resampled_audio)
        with self._lock:
            self._clear_buffers()
            self._resampler = resampler
            self._last_audio_at = monotonic()
            self._pending_gap = None
            self._paused = False

    def read_frame(self, timeout: float = .1):
        """Return 512 float32 samples, or None when no complete frame is ready."""
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 <= timeout <= 1:
            raise ValueError("Audio polling timeout must be between zero and one second.")
        if self._stream is None:
            raise AudioError("The microphone stream is not open.")
        deadline = monotonic() + timeout
        while True:
            if self._failure is not None:
                raise self._failure
            if self._paused:
                return None
            try:
                if not self._stream.active:
                    raise MicrophoneFailure("device", "The microphone disconnected or stopped.")
            except AudioError:
                raise
            except Exception as exc:
                raise MicrophoneFailure("device", "Could not read the microphone state.") from exc
            if self._frames:
                return self._frames.popleft()
            try:
                block = self._pcm.get(timeout=max(0, deadline - monotonic()))
            except Empty:
                if self._last_audio_at is not None and monotonic() - self._last_audio_at >= STALL_SECONDS:
                    raise MicrophoneFailure("stall", f"The microphone delivered no audio for {STALL_SECONDS:g} s.")
                return None
            if self._on_native_audio is not None:
                # Consumer-thread observation, never work in PortAudio's
                # real-time callback. Immutable PCM cannot be changed here.
                try:
                    self._on_native_audio(block, self.native_sample_rate)
                except Exception as exc:
                    self.diagnostic_errors.append(f"native: {type(exc).__name__}")
                    self._on_native_audio = None
            try:
                self._frames.extend(self._resampler.feed(block))
                if self._resampler.diagnostic_errors and not self._resampled_diagnostic_failed:
                    self.diagnostic_errors.extend(self._resampler.diagnostic_errors)
                    self._resampled_diagnostic_failed = True
                    self._on_resampled_audio = None
            except Exception as exc:
                raise AudioError("Could not resample the microphone audio.") from exc
            if not self._frames and monotonic() >= deadline:
                return None

    def close(self) -> None:
        self.pause()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass  # Cleanup must not hide an input error or Ctrl+C.

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()
