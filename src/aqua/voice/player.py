"""Audio playback for spoken replies. Tests use FakePlayer; never a real device."""

from threading import Event, Lock
from typing import Callable, Protocol, runtime_checkable

POLL_SECONDS = .03


@runtime_checkable
class Player(Protocol):
    def play(self, samples, sample_rate: int, *, cancelled: Callable[[], bool] | None = None) -> None:
        """Block until ``samples`` finished playing, stop() was called, or cancelled() is true."""

    def stop(self) -> None:
        """Stop current playback promptly; safe to call from any thread."""


class SoundDevicePlayer:
    """Play through the default Windows output with a callback stream.

    sounddevice is imported only when audio is actually played, so creating
    a player (or running tests) never opens an audio device.
    """

    def __init__(self, *, device: int | None = None):
        self.device = device
        self._stop = Event()

    def play(self, samples, sample_rate: int, *, cancelled=None) -> None:
        import numpy as np
        import sounddevice

        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not audio.size:
            return
        self._stop.clear()
        position = 0
        lock = Lock()
        finished = Event()

        def callback(output, frames, _time, _status):
            nonlocal position
            with lock:
                chunk = audio[position:position + frames]
                position += frames
            output[:len(chunk), 0] = chunk
            if len(chunk) < frames:
                output[len(chunk):, 0] = 0
                raise sounddevice.CallbackStop

        stream = sounddevice.OutputStream(
            samplerate=int(sample_rate), channels=1, dtype="float32", device=self.device,
            callback=callback, finished_callback=finished.set,
        )
        with stream:
            while not finished.wait(POLL_SECONDS):
                if self._stop.is_set() or (cancelled is not None and cancelled()):
                    stream.abort()
                    break

    def stop(self) -> None:
        self._stop.set()


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
