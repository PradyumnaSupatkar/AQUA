"""Audio-level activity checks using synthetic native-endian int16 PCM."""

from array import array
import math
import unittest

from aqua.audio.diagnostics import analyze_audio


def pcm(samples):
    return array("h", samples).tobytes()


class DiagnosticsTests(unittest.TestCase):
    def test_empty_and_silent_input_have_no_activity(self):
        for audio in (b"", pcm([0] * 16000)):
            with self.subTest(length=len(audio)):
                result = analyze_audio(audio, 16000)
                self.assertEqual(result.active_fraction, 0.0)
                self.assertEqual(result.active_rms_dbfs, -math.inf)
                self.assertEqual(result.quiet_frame_rms_dbfs, -math.inf)
                self.assertEqual(result.p90_frame_rms_dbfs, -math.inf)
                self.assertEqual(result.levels.peak, 0.0)

    def test_active_frame_rms_does_not_dilute_signal_with_silent_gaps(self):
        # Half-scale constant signal occupies one quarter of the capture.
        result = analyze_audio(pcm([0] * 12000 + [16384] * 4000), 16000)
        self.assertEqual(result.frame_count, 50)
        self.assertEqual(result.duration, 1.0)
        self.assertAlmostEqual(result.active_fraction, 0.26)
        self.assertAlmostEqual(result.levels.rms, 0.25)
        self.assertAlmostEqual(result.p90_frame_rms_dbfs, 20 * math.log10(0.5))
        self.assertGreater(result.active_rms_dbfs, result.levels.rms_dbfs)

    def test_steady_signal_is_measured_without_claiming_speech(self):
        result = analyze_audio(pcm([3277] * 16000), 16000)
        self.assertEqual(result.active_fraction, 1.0)
        self.assertAlmostEqual(result.active_rms_dbfs, result.levels.rms_dbfs)
        self.assertLess(result.activity_threshold_dbfs, result.p90_frame_rms_dbfs)

    def test_quiet_signal_remains_visible_even_when_below_activity_gate(self):
        result = analyze_audio(pcm([33] * 16000), 16000)
        self.assertEqual(result.active_fraction, 0.0)
        self.assertLess(result.p90_frame_rms_dbfs, -55)
        self.assertGreater(result.p90_frame_rms_dbfs, -math.inf)
        self.assertEqual(result.activity_threshold_dbfs, -55)

    def test_original_clipping_is_reported(self):
        result = analyze_audio(pcm([-32768, 32767, 0]), 16000)
        self.assertEqual(result.levels.clipped_samples, 2)
        self.assertEqual(result.levels.peak, 1.0)

    def test_short_final_window_is_weighted_by_its_duration(self):
        result = analyze_audio(pcm([0] * 20 + [16384]), 1000)
        self.assertEqual(result.frame_count, 2)
        self.assertAlmostEqual(result.active_fraction, 1 / 21)
        self.assertAlmostEqual(result.active_rms_dbfs, 20 * math.log10(0.5))

    def test_invalid_rates_and_partial_pcm_sample_are_rejected(self):
        for rate in (0, -1, 16000.0, True):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                analyze_audio(b"", rate)
        with self.assertRaises(ValueError):
            analyze_audio(b"\x00", 16000)


if __name__ == "__main__":
    unittest.main()
