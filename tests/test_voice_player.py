"""SoundDevicePlayer's persistent stream, driven by a fake stream (no audio device)."""

from threading import Event, Thread
from time import perf_counter, sleep
import unittest

import numpy as np

from aqua.voice.engines import FakeEngine
from aqua.voice.output import SpeechOutput
from aqua.voice.player import SoundDevicePlayer


class FakeStream:
    """Stands in for sounddevice.OutputStream; the test pulls audio via pull()."""

    def __init__(self, *, samplerate, channels, dtype, device, callback, latency=.18):
        self.samplerate, self.channels, self.dtype, self.device = samplerate, channels, dtype, device
        self.callback = callback
        self.latency = latency
        self.started = self.aborted = self.closed = False

    def start(self):
        self.started = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True

    def pull(self, frames):
        output = np.full((frames, 1), 9.0, dtype=np.float32)  # 9 = untouched, must be overwritten
        self.callback(output, frames, None, None)
        return output[:, 0].copy()


class Factory:
    def __init__(self):
        self.streams = []

    def __call__(self, **options):
        stream = FakeStream(**options)
        self.streams.append(stream)
        return stream


def until(predicate, timeout=2.0):
    deadline = perf_counter() + timeout
    while not predicate():
        if perf_counter() > deadline:
            raise AssertionError("condition not reached")
        sleep(.001)


class PersistentStreamTests(unittest.TestCase):
    def setUp(self):
        self.factory = Factory()
        self.player = SoundDevicePlayer(device=5, stream_factory=self.factory)
        self.addCleanup(self.player.close)

    def play_async(self, samples, rate=24000, cancelled=None):
        finished = Event()

        def run():
            self.player.play(samples, rate, cancelled=cancelled)
            finished.set()

        Thread(target=run, daemon=True).start()
        return finished

    def test_one_stream_for_consecutive_sentences_without_gaps(self):
        first, second = np.arange(1, 7, dtype=np.float32), np.arange(10, 14, dtype=np.float32)
        done_first = self.play_async(first)
        until(lambda: self.factory.streams)
        stream = self.factory.streams[0]
        self.assertEqual((stream.samplerate, stream.channels, stream.dtype, stream.device), (24000, 1, "float32", 5))
        self.assertTrue(stream.started)
        np.testing.assert_array_equal(stream.pull(4), [1, 2, 3, 4])
        self.assertFalse(done_first.is_set())          # still playing
        np.testing.assert_array_equal(stream.pull(2), [5, 6])
        self.assertTrue(done_first.wait(1))           # returns once handed to the device
        done_second = self.play_async(second)
        until(lambda: len(self.player._queue) == 1)
        np.testing.assert_array_equal(stream.pull(6), [10, 11, 12, 13, 0, 0])  # back to back, then silence
        self.assertTrue(done_second.wait(1))
        self.assertEqual(len(self.factory.streams), 1)  # the same stream, never reopened
        self.assertFalse(stream.aborted)

    def test_queued_clips_are_concatenated_inside_one_callback(self):
        stream_ready = self.play_async(np.ones(3, dtype=np.float32))
        until(lambda: self.factory.streams)
        self.play_async(np.full(3, 2, dtype=np.float32))
        until(lambda: len(self.player._queue) == 2)
        np.testing.assert_array_equal(self.factory.streams[0].pull(8), [1, 1, 1, 2, 2, 2, 0, 0])
        self.assertTrue(stream_ready.wait(1))

    def test_idle_stream_outputs_silence(self):
        done = self.play_async(np.ones(2, dtype=np.float32))
        until(lambda: self.factory.streams)
        stream = self.factory.streams[0]
        stream.pull(2)
        self.assertTrue(done.wait(1))
        np.testing.assert_array_equal(stream.pull(5), np.zeros(5))

    def test_stop_empties_queue_aborts_stream_and_releases_play(self):
        done = self.play_async(np.ones(100, dtype=np.float32))
        until(lambda: self.factory.streams)
        stream = self.factory.streams[0]
        stream.pull(10)
        self.player.stop()
        self.assertTrue(done.wait(1))
        self.assertTrue(stream.aborted and stream.closed)
        self.assertEqual(len(self.player._queue), 0)
        np.testing.assert_array_equal(stream.pull(3), np.zeros(3))  # nothing left to play
        again = self.play_async(np.ones(2, dtype=np.float32))
        until(lambda: len(self.factory.streams) == 2)        # next play opens a fresh stream
        self.factory.streams[1].pull(2)
        self.assertTrue(again.wait(1))

    def test_cancelled_stops_promptly(self):
        flag = Event()
        done = self.play_async(np.ones(1000, dtype=np.float32), cancelled=flag.is_set)
        until(lambda: self.factory.streams)
        flag.set()
        self.assertTrue(done.wait(1))
        self.assertTrue(self.factory.streams[0].aborted)
        self.assertEqual(len(self.player._queue), 0)

    def test_new_sample_rate_reopens_and_empty_audio_opens_nothing(self):
        self.player.play(np.zeros(0, dtype=np.float32), 24000)
        self.assertEqual(self.factory.streams, [])
        done = self.play_async(np.ones(2, dtype=np.float32), rate=22050)
        until(lambda: self.factory.streams)
        self.factory.streams[0].pull(2)
        self.assertTrue(done.wait(1))
        done = self.play_async(np.ones(2, dtype=np.float32), rate=24000)
        until(lambda: len(self.factory.streams) == 2)
        self.assertTrue(self.factory.streams[0].aborted and self.factory.streams[0].closed)
        self.assertEqual(self.factory.streams[1].samplerate, 24000)
        self.factory.streams[1].pull(2)
        self.assertTrue(done.wait(1))

    def test_tail_is_the_stream_latency_and_close_releases_it(self):
        self.assertEqual(self.player.tail_seconds, .2)  # no stream yet
        done = self.play_async(np.ones(1, dtype=np.float32))
        until(lambda: self.factory.streams)
        self.assertAlmostEqual(self.player.tail_seconds, .18)
        self.factory.streams[0].pull(1)
        self.assertTrue(done.wait(1))
        self.player.close()
        self.assertTrue(self.factory.streams[0].closed)


class PipelineWithPersistentStreamTests(unittest.TestCase):
    def test_sentences_play_back_to_back_and_speaking_lasts_through_the_tail(self):
        factory = Factory()
        player = SoundDevicePlayer(stream_factory=factory)
        engine = FakeEngine(samples_per_char=1)
        speaker = SpeechOutput(engine, player, warn=lambda _m: None)
        self.addCleanup(speaker.close)
        speaker.feed("One. Two. Three.")
        speaker.finish_turn()
        until(lambda: factory.streams)
        stream = factory.streams[0]
        received = []
        deadline = perf_counter() + 3
        while sum(len(x) for x in received) < len("One.Two.Three.") and perf_counter() < deadline:
            chunk = stream.pull(4)
            received.append(chunk[chunk != 0])
            sleep(.002)
        self.assertEqual(sum(len(x) for x in received), len("One.") + len("Two.") + len("Three."))
        self.assertEqual(len(factory.streams), 1)
        self.assertTrue(speaker.is_speaking)  # the device is still sounding its buffered tail
        self.assertTrue(speaker.wait(2))
        self.assertFalse(speaker.is_speaking)

    def test_stop_mid_reply_silences_the_persistent_stream(self):
        factory = Factory()
        player = SoundDevicePlayer(stream_factory=factory)
        speaker = SpeechOutput(FakeEngine(samples_per_char=50), player, warn=lambda _m: None)
        self.addCleanup(speaker.close)
        speaker.feed("A long first sentence. And another one.")
        speaker.finish_turn()
        until(lambda: factory.streams and player._queue)
        factory.streams[0].pull(10)
        speaker.stop()
        self.assertFalse(speaker.is_speaking)
        self.assertTrue(factory.streams[0].aborted)
        self.assertEqual(len(player._queue), 0)


if __name__ == "__main__":
    unittest.main()
