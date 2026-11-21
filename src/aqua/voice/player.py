"""Audio playback for spoken replies. Tests use FakePlayer; never a real device."""

from collections import deque
from threading import Event, Lock
from typing import Callable, Protocol, runtime_checkable

POLL_SECONDS = .03


@runtime_checkable
class Player(Protocol):
    def play(self, samples, sample_rate: int, *, cancelled: Callable[[], bool] | None = None) -> None:
        """Block until ``samples`` finished playing, stop() was called, or cancelled() is true."""

    def stop(self) -> None:
        """Stop current playback promptly; safe to call from any thread."""


class _Clip:
    __slots__ = ("audio", "position", "done")

    def __init__(self, audio):
        self.audio, self.position, self.done = audio, 0, Event()


def _sounddevice_stream(**options):
    import sounddevice
    return sounddevice.OutputStream(**options)


class SoundDevicePlayer:
    """One output stream for the whole session, fed from a queue of clips.

    Opening a stream per sentence cost about 55 ms plus ~180 ms of output
    latency on the target laptop, an audible gap between sentences. Here the
    stream stays open (silence while idle) and each sentence is appended to
    the queue, so consecutive sentences play back to back.

    play() returns once its samples have been handed to the device; the
    pipeline then waits ``tail_seconds`` (the stream's output latency) after
    the last sentence before reporting that speech ended. stop() empties the
    queue and aborts the stream, discarding audio already buffered, so speech
    stops at once; the next play() opens a fresh stream.

    sounddevice is imported only when audio is actually played, so creating
    a player (or running tests) never opens an audio device. Tests pass a
    fake ``stream_factory``.
    """

    def __init__(self, *, device: int | None = None, stream_factory=None):
        self.device = device
        self._factory = stream_factory or _sounddevice_stream
        self._lock = Lock()
        self._queue: deque[_Clip] = deque()
        self._stream = None
        self._rate = None

    @property
    def tail_seconds(self) -> float:
        stream = self._stream
        latency = getattr(stream, "latency", None) if stream is not None else None
        return float(latency) if isinstance(latency, (int, float)) and 0 <= latency < 2 else .2

    def _open(self, sample_rate: int) -> None:
        """Called with the lock held."""
        if self._stream is not None and self._rate == sample_rate:
            return
        old, self._stream, self._rate = self._stream, None, None
        if old is not None:
            old.abort()
            old.close()
        stream = self._factory(samplerate=sample_rate, channels=1, dtype="float32", device=self.device,
                               callback=self._callback)
        stream.start()
        self._stream, self._rate = stream, sample_rate

    def _callback(self, output, frames, _time, _status) -> None:
        filled = 0
        with self._lock:
            while filled < frames and self._queue:
                clip = self._queue[0]
                chunk = clip.audio[clip.position:clip.position + frames - filled]
                output[filled:filled + len(chunk), 0] = chunk
                filled += len(chunk)
                clip.position += len(chunk)
                if clip.position >= len(clip.audio):
                    self._queue.popleft()
                    clip.done.set()
        output[filled:, 0] = 0  # Silence while idle.

    def play(self, samples, sample_rate: int, *, cancelled=None) -> None:
        import numpy as np

        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not audio.size:
            return
        clip = _Clip(audio)
        with self._lock:
            self._open(int(sample_rate))
            self._queue.append(clip)
        while not clip.done.wait(POLL_SECONDS):
            if cancelled is not None and cancelled():
                self.stop()
                return

    def stop(self) -> None:
        """Empty the queue and silence the device immediately; safe from any thread."""
        with self._lock:
            clips = list(self._queue)
            self._queue.clear()
            stream, self._stream, self._rate = self._stream, None, None
        for clip in clips:
            clip.done.set()
        if stream is not None:
            # Outside the lock: abort waits for a running callback, which takes the lock.
            stream.abort()
            stream.close()

    def close(self) -> None:
        self.stop()


class FakePlayer:
    """Records playback without audio. With ``gate`` set, play() blocks like a
    real device until stop(), cancellation, or ``release()``."""

    def __init__(self, *, gate: bool = False, fail: bool = False):
        self.played: list[tuple[int, int]] = []
        self.stops = 0
        self.fail = fail
        self.playing = Event()
        self._release = Event()
        self._stopped = Event()
        if not gate:
            self._release.set()

    def release(self) -> None:
        self._release.set()

    def play(self, samples, sample_rate: int, *, cancelled=None) -> None:
        if self.fail:
            raise OSError("synthetic output device failure")
        self._stopped.clear()
        self.played.append((len(samples), sample_rate))
        self.playing.set()
        try:
            while not self._release.wait(.005):
                if self._stopped.is_set() or (cancelled is not None and cancelled()):
                    return
        finally:
            self.playing.clear()

    def stop(self) -> None:
        self.stops += 1
        self._stopped.set()
