"""Synthetic resampling checks: no microphone, models, or network access."""

import math
import unittest

try:
    import av
    import numpy as np
except ImportError:
    av = None
    np = None

from aqua.audio.processing import to_16khz


@unittest.skipIf(av is None or np is None, "Install Aqua's speech dependencies to test resampling")
class ProcessingTests(unittest.TestCase):
    @staticmethod
    def tone(sample_rate, frequency, seconds=1.0, amplitude=0.5):
        times = np.arange(round(sample_rate * seconds)) / sample_rate
        return np.rint(np.sin(2 * np.pi * frequency * times) * amplitude * 32767).astype(np.int16).tobytes()

    def test_native_rate_pcm_scaling_and_dtype(self):
        source = np.array([-32768, -16384, 0, 16384, 32767], dtype=np.int16)
        actual = to_16khz(source.tobytes(), 16000)
        np.testing.assert_allclose(actual, source.astype(np.float32) / 32768)
        self.assertEqual(actual.dtype, np.float32)
        self.assertEqual(actual.ndim, 1)
        self.assertTrue(actual.flags.c_contiguous)

    def test_five_seconds_at_microphone_rates_becomes_eighty_thousand_samples(self):
        for rate in (44100, 48000, 8000):
            with self.subTest(rate=rate):
                audio = to_16khz(self.tone(rate, 1000, seconds=5), rate)
                self.assertLessEqual(abs(len(audio) - 80000), 1)
                self.assertEqual(audio.dtype, np.float32)

    def test_pitch_is_preserved_when_resampling_not_relabelled(self):
        output = to_16khz(self.tone(44100, 1000), 44100)
        frequencies = np.fft.rfftfreq(len(output), 1 / 16000)
        strongest = frequencies[np.argmax(abs(np.fft.rfft(output)))]
        self.assertAlmostEqual(strongest, 1000, delta=2)
        self.assertAlmostEqual(float(np.sqrt(np.mean(output[100:-100] ** 2))), 0.5 / math.sqrt(2), delta=0.005)

    def test_downsampling_filters_above_nyquist_instead_of_aliasing(self):
        output = to_16khz(self.tone(48000, 12000), 48000)
        rms = float(np.sqrt(np.mean(output[200:-200] ** 2)))
        self.assertLess(rms, 0.005)

    def test_explicit_gain_scales_and_clips_without_mutating_original_pcm(self):
        original = np.array([-30000, -1000, 1000, 30000], dtype=np.int16).tobytes()
        preserved = original[:]
        output = to_16khz(original, 16000, gain_db=20)
        self.assertEqual(original, preserved)
        np.testing.assert_allclose(output, [-1, -10000 / 32768, 10000 / 32768, 1])

    def test_silence_empty_and_attenuation(self):
        self.assertEqual(to_16khz(b"", 44100).size, 0)
        np.testing.assert_array_equal(to_16khz(bytes(44100 * 2), 44100), np.zeros(16000, dtype=np.float32))
        source = np.array([16384] * 10, dtype=np.int16).tobytes()
        np.testing.assert_allclose(to_16khz(source, 16000, gain_db=-20), np.full(10, 0.05))

    def test_nonempty_input_too_short_for_resampling_has_clear_error(self):
        with self.assertRaisesRegex(ValueError, "too short to resample"):
            to_16khz(np.zeros(2, dtype=np.int16).tobytes(), 44100)
        self.assertEqual(to_16khz(np.zeros(2, dtype=np.int16).tobytes(), 16000).size, 2)


class ProcessingValidationTests(unittest.TestCase):
    def test_invalid_rates_pcm_and_gains_are_rejected_before_dependency_import(self):
        for rate in (0, -1, 44100.0, True):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                to_16khz(b"", rate)
        with self.assertRaises(ValueError):
            to_16khz(b"\x00", 44100)
        for gain in (-31, 31, math.inf, -math.inf, math.nan):
            with self.subTest(gain=gain), self.assertRaises(ValueError):
                to_16khz(b"", 44100, gain_db=gain)


if __name__ == "__main__":
    unittest.main()
