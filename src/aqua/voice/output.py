"""Streaming speech output: sentences are synthesised and played in the background.

Two worker threads connected by bounded queues: the synth worker prepares
sentence N+1 while the player worker plays sentence N. Text output never waits
on audio, and any engine or device failure disables speech for the session
after a single warning instead of breaking the turn.
"""

from dataclasses import dataclass
from queue import Empty, Full, Queue
import sys
from threading import Condition, Lock, Thread, current_thread
from time import perf_counter, sleep

from .engines import VoiceError
from .text import MAX_SPOKEN_SENTENCES, SentenceSplitter, SpokenBudget, speakable

_CLOSE = object()
STOP_ACK_SECONDS = .5


@dataclass(frozen=True, slots=True)
class SpeechTimings:
    first_audio_seconds: float | None
    synthesis_seconds: float
    audio_seconds: float
    sentences_spoken: int  # chunks played (a long sentence is several chunks)
    longest_gap_seconds: float = 0.0
    total_gap_seconds: float = 0.0
    llm_tokens_per_second: float | None = None

    @property
    def real_time_factor(self) -> float | None:
        return self.synthesis_seconds / self.audio_seconds if self.audio_seconds else None

    def describe(self) -> str:
        first = f"{self.first_audio_seconds:.3f}s" if self.first_audio_seconds is not None else "n/a"
        rtf = f"{self.real_time_factor:.3f}" if self.real_time_factor is not None else "n/a"
        rate = f"{self.llm_tokens_per_second:.1f} tok/s" if self.llm_tokens_per_second is not None else "n/a"
        return (f"Speech output: first audio {first} after reply start; synthesis {self.synthesis_seconds:.3f}s "
                f"for {self.audio_seconds:.3f}s audio (RTF {rtf}); chunks spoken {self.sentences_spoken}; "
                f"longest mid-reply gap {self.longest_gap_seconds:.3f}s (total {self.total_gap_seconds:.3f}s); "
                f"model generation {rate}")


class _Turn:
    __slots__ = ("started", "first_audio", "synthesis", "audio", "sentences", "audio_end",
                 "longest_gap", "total_gap", "llm_rate")

    def __init__(self, started: float):
        self.started = started
        self.first_audio = None
        self.synthesis = self.audio = 0.0
        self.sentences = 0
        self.audio_end = None  # clock time at which the audio handed to the player runs out
        self.longest_gap = self.total_gap = 0.0
        self.llm_rate = None

    def schedule(self, now: float, duration: float) -> None:
        """Model the device timeline: a gap is time it sat idle waiting for the next chunk."""
        if self.audio_end is not None and now > self.audio_end:
            gap = now - self.audio_end
            self.total_gap += gap
            self.longest_gap = max(self.longest_gap, gap)
        start = now if self.audio_end is None else max(now, self.audio_end)
        self.audio_end = start + duration

    def snapshot(self) -> SpeechTimings:
        return SpeechTimings(self.first_audio, self.synthesis, self.audio, self.sentences,
                             self.longest_gap, self.total_gap, self.llm_rate)


def _default_warn(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class SpeechOutput:
    def __init__(self, engine, player=None, *, max_sentences: int = MAX_SPOKEN_SENTENCES,
                 queue_size: int = 8, audio_queue_size: int = 1, warn=None, clock=perf_counter):
        if player is None:
            from .player import SoundDevicePlayer
            player = SoundDevicePlayer()
        self.engine, self.player = engine, player
        self._warn = warn if warn is not None else _default_warn
        self._clock = clock
        self._splitter = SentenceSplitter()
        self._budget = SpokenBudget(max_sentences)
        self._synth_queue: Queue = Queue(maxsize=queue_size)
        self._audio_queue: Queue = Queue(maxsize=audio_queue_size)
        self._lock = Lock()
        self._idle = Condition(Lock())
        self._pending = 0
        self._generation = 0
        self._turn: _Turn | None = None
        self._last_turn: _Turn | None = None
        self._disabled = False
        self._closed = False
        self.dropped = 0
        self._threads = (
            Thread(target=self._synth_worker, name="aqua-voice-synth", daemon=True),
            Thread(target=self._player_worker, name="aqua-voice-player", daemon=True),
        )
        for thread in self._threads:
            thread.start()

    # ----- public API (main thread) -------------------------------------
    @property
    def enabled(self) -> bool:
        return not self._disabled and not self._closed

    @property
    def is_speaking(self) -> bool:
        with self._idle:
            return self._pending > 0

    @property
    def timings(self) -> SpeechTimings | None:
        turn = self._turn or self._last_turn
        return turn.snapshot() if turn is not None else None

    def note_generation_rate(self, tokens_per_second: float | None) -> None:
        """Record the language model's generation speed for this turn's diagnostics."""
        turn = self._turn or self._last_turn
        if turn is not None and isinstance(tokens_per_second, (int, float)) and tokens_per_second > 0:
            turn.llm_rate = float(tokens_per_second)

    def feed(self, chunk: str) -> None:
        if not self.enabled:
            return
        try:
            with self._lock:
                if self._turn is None:
                    self._turn = _Turn(self._clock())
                    self._last_turn = self._turn
                for piece in self._splitter.feed_chunks(chunk):
                    self._speak_chunk(piece)
        except Exception as exc:  # Never let speech affect the printed reply.
            self._fail(exc)

    def finish_turn(self) -> None:
        if not self.enabled:
            return
        try:
            with self._lock:
                for piece in self._splitter.flush_chunks():
                    self._speak_chunk(piece)
                for text in self._budget.finish():
                    self._enqueue(text)
                self._turn = None
        except Exception as exc:
            self._fail(exc)

    def stop(self) -> None:
        """Silence immediately: drop queued sentences and audio, stop playback.

        Returns once in-flight playback has acknowledged (bounded), so
        is_speaking is False afterwards unless a device hangs.
        """
        self._halt()
        if current_thread() not in self._threads:
            self.wait(STOP_ACK_SECONDS)

    def _halt(self) -> None:
        with self._lock:
            self._generation += 1
            self._splitter.reset()
            self._budget.reset()
            self._turn = None
            dropped = self._drain(self._synth_queue) + self._drain(self._audio_queue)
        for _ in range(dropped):
            self._done()
        try:
            self.player.stop()
        except Exception:
            pass

    def wait(self, timeout: float | None = None) -> bool:
        """Wait until all speech for this turn has played; True when idle."""
        deadline = None if timeout is None else self._clock() + timeout
        with self._idle:
            while self._pending > 0:
                remaining = .1 if deadline is None else min(.1, deadline - self._clock())
                if remaining <= 0:
                    return False
                self._idle.wait(remaining)  # Short waits keep Ctrl+C responsive.
            return True

    def close(self) -> None:
        if self._closed:
            return
        self.stop()
        self._closed = True
        closer = getattr(self.player, "close", None)
        if callable(closer):
            try:
                closer()  # Release a persistent output stream.
            except Exception:
                pass
        for queue in (self._synth_queue, self._audio_queue):
            try:
                queue.put(_CLOSE, timeout=1)
            except Full:
                pass
        for thread in self._threads:
            thread.join(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    # ----- internals ----------------------------------------------------
    def _speak_chunk(self, piece) -> None:
        for text in self._budget.push(speakable(piece.text), end=piece.sentence_end):
            self._enqueue(text)

    def _enqueue(self, text: str) -> None:
        with self._idle:
            self._pending += 1
        try:
            self._synth_queue.put_nowait((self._generation, text, self._turn or self._last_turn))
        except Full:
            self.dropped += 1
            self._done()

    @staticmethod
    def _drain(queue: Queue) -> int:
        count = 0
        while True:
            try:
                item = queue.get_nowait()
            except Empty:
                return count
            if item is not _CLOSE:
                count += 1

    def _done(self) -> None:
        with self._idle:
            self._pending = max(0, self._pending - 1)
            if not self._pending:
                self._idle.notify_all()

    def _stale(self, generation: int) -> bool:
        return generation != self._generation or self._disabled or self._closed

    def _fail(self, exc: BaseException) -> None:
        with self._lock:
            if self._disabled:
                return
            self._disabled = True
        reason = str(exc) or type(exc).__name__
        self._warn(f"Speech output stopped ({reason}); replies continue as text for the rest of this session.")
        self._halt()

    def _synth_worker(self) -> None:
        while True:
            item = self._synth_queue.get()
            if item is _CLOSE:
                return
            generation, text, turn = item
            if self._stale(generation):
                self._done()
                continue
            started = self._clock()
            try:
                samples = self.engine.synthesize(text)
                if samples is None:
                    raise VoiceError("the speech engine returned no audio")
            except Exception as exc:
                self._fail(exc)
                self._done()
                continue
            if turn is not None:
                turn.synthesis += self._clock() - started
            if not len(samples):
                self._done()
                continue
            while True:
                if self._stale(generation):
                    self._done()
                    break
                try:
                    self._audio_queue.put((generation, samples, self.engine.sample_rate, turn), timeout=.05)
                    break
                except Full:
                    continue

    def _player_worker(self) -> None:
        while True:
            item = self._audio_queue.get()
            if item is _CLOSE:
                return
            generation, samples, sample_rate, turn = item
            if self._stale(generation):
                self._done()
                continue
            if turn is not None:
                now = self._clock()
                if turn.first_audio is None:
                    turn.first_audio = now - turn.started
                turn.schedule(now, len(samples) / sample_rate)
            try:
                self.player.play(samples, sample_rate, cancelled=lambda: self._stale(generation))
                if turn is not None and not self._stale(generation):
                    turn.audio += len(samples) / sample_rate
                    turn.sentences += 1
                self._drain_tail(generation)
            except Exception as exc:
                self._fail(exc)
            finally:
                self._done()

    def _drain_tail(self, generation: int) -> None:
        """A persistent stream is still sounding after play() returns. Stay
        "speaking" for its output latency, unless the next sentence arrives
        first (it is then queued straight behind, with no gap)."""
        tail = getattr(self.player, "tail_seconds", 0.0)
        if not isinstance(tail, (int, float)) or tail <= 0:
            return
        deadline = self._clock() + min(float(tail), 1.0)
        while self._audio_queue.empty() and not self._stale(generation) and self._clock() < deadline:
            sleep(.005)


def open_speaker(*, engine: str = "kokoro", voice=None, model_dir=None, rate: float = 1.0,
                 threads: int | None = None, player=None, out=print) -> SpeechOutput | None:
    """Load the engine once for a session; a missing voice means text-only, not an error."""
    from .engines import load_engine
    try:
        loaded = load_engine(engine, model_dir=model_dir, voice=voice, speed=rate, num_threads=threads)
    except VoiceError as exc:
        out(f"Note: speech output unavailable. {exc} Continuing with text only.")
        return None
    return SpeechOutput(loaded, player)
