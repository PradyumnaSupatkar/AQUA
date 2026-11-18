"""Offline CLI behavior with synthetic audio and mocked speech models."""

from array import array
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch

from aqua.audio.backend import AudioError
from aqua.audio.devices import DeviceInfo
from aqua.audio.levels import AudioLevels
from aqua.audio.recording import Recording
from aqua.cli import main
from aqua.speech.models import BASE_MODEL_DIR, DEFAULT_MODEL_DIR, ModelSetupError, SpeechError
from aqua.speech.recognizer import Transcript


class TranscribeCliTests(unittest.TestCase):
    @staticmethod
    def recording(*, sample_rate=44100, selected=True):
        microphone = DeviceInfo(
            11, "Synthetic Realtek microphone", "Windows WASAPI", 2, 0,
            float(sample_rate), False,
        ) if selected else None
        return Recording(
            array("h", [1024] * (sample_rate * 5)).tobytes(), sample_rate,
            AudioLevels(peak=1 / 32, rms=1 / 32, clipped_samples=0),
            0, microphone,
        )

    @staticmethod
    def transcript(*, text="Aqua keeps my voice local.", saturated_samples=0):
        return Transcript(
            text=text, language="en", language_probability=0.95,
            speech_seconds=2.75, input_peak_dbfs=-18.0,
            input_rms_dbfs=-24.0, saturated_samples=saturated_samples,
        )

    def invoke(self, arguments):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = main(arguments)
        return status, output.getvalue(), errors.getvalue()

    def test_transcribe_passes_native_audio_and_options_and_prints_text(self):
        source = self.recording()
        model_dir = Path("D:/Aqua/models/test-base")
        with patch("aqua.cli.OfflineRecognizer") as model_class, patch(
            "aqua.cli.record_test", return_value=source
        ) as record:
            model_class.return_value.transcribe.return_value = self.transcript()
            status, output, errors = self.invoke([
                "transcribe", "--device", "11", "--sample-rate", "44100",
                "--model-dir", str(model_dir), "--language", "en", "--gain-db", "12",
            ])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        model_class.assert_called_once_with(model_dir)
        self.assertEqual(record.call_args.kwargs["device"], 11)
        self.assertEqual(record.call_args.kwargs["sample_rate"], 44100)
        self.assertTrue(callable(record.call_args.kwargs["on_progress"]))
        model_class.return_value.transcribe.assert_called_once_with(
            source.pcm, 44100, gain_db=12.0, language="en"
        )
        self.assertEqual(source.duration, 5.0)
        self.assertIn("5.0s", output)
        self.assertIn("Aqua keeps my voice local.", output)
        self.assertIn("no recording was saved", output)

    def test_transcribe_defaults_to_zero_gain_and_language_autodetection(self):
        source = self.recording(sample_rate=1000)
        with patch("aqua.cli.OfflineRecognizer") as model_class, patch(
            "aqua.cli.record_test", return_value=source
        ) as record:
            model_class.return_value.transcribe.return_value = self.transcript()
            status, _output, _errors = self.invoke(["transcribe"])
        self.assertEqual(status, 0)
        model_class.assert_called_once_with(DEFAULT_MODEL_DIR)
        model_class.return_value.transcribe.assert_called_once_with(
            source.pcm, 1000, gain_db=0.0, language=None
        )
        self.assertIsNone(record.call_args.kwargs["device"])
        self.assertIsNone(record.call_args.kwargs["sample_rate"])

    def test_blank_transcript_has_helpful_no_speech_response(self):
        with patch("aqua.cli.OfflineRecognizer") as model_class, patch(
            "aqua.cli.record_test", return_value=self.recording(sample_rate=1000)
        ):
            model_class.return_value.transcribe.return_value = self.transcript(text="")
            status, output, errors = self.invoke(["transcribe"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("No speech recognized", output)
        self.assertIn("--diagnostics", output)
        self.assertNotIn("Recognized text:", output)

    def test_diagnostics_report_original_energy_vad_and_gain_saturation(self):
        with patch("aqua.cli.OfflineRecognizer") as model_class, patch(
            "aqua.cli.record_test", return_value=self.recording(sample_rate=1000)
        ):
            model_class.return_value.transcribe.return_value = self.transcript(saturated_samples=7)
            status, output, errors = self.invoke([
                "transcribe", "--diagnostics", "--gain-db", "12"
            ])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("before gain/resampling", output)
        self.assertIn("not speech VAD", output)
        self.assertIn("Energy-active", output)
        self.assertIn("Source samples at full scale: 0", output)
        self.assertIn("16000 Hz mono", output)
        self.assertIn("gain +12 dB", output)
        self.assertIn("VAD-retained audio", output)
        self.assertIn("2.75s", output)
        self.assertIn("7 recognition-input samples", output)
        self.assertIn("reduce --gain-db", output)

    def test_saturation_warning_is_visible_without_diagnostics(self):
        with patch("aqua.cli.OfflineRecognizer") as model_class, patch(
            "aqua.cli.record_test", return_value=self.recording(sample_rate=1000)
        ):
            model_class.return_value.transcribe.return_value = self.transcript(saturated_samples=2)
            status, output, _errors = self.invoke(["transcribe", "--gain-db", "30"])
        self.assertEqual(status, 0)
        self.assertIn("recognition-input samples reached full scale", output)
        self.assertNotIn("Source diagnostics", output)

    def test_setup_verifies_without_download_or_microphone_access(self):
        with patch("aqua.cli.setup_model", return_value=BASE_MODEL_DIR) as setup, patch(
            "aqua.cli.record_test"
        ) as record, patch("aqua.cli.OfflineRecognizer") as model_class:
            status, output, errors = self.invoke(["setup-model"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        setup.assert_called_once_with(BASE_MODEL_DIR, download=False)
        record.assert_not_called()
        model_class.assert_not_called()
        self.assertIn("Offline base model ready", output)

    def test_setup_download_requires_explicit_flag_and_passes_local_path(self):
        model_dir = Path("D:/Aqua/models/explicit-base")
        with patch("aqua.cli.setup_model", return_value=model_dir) as setup, patch(
            "aqua.cli.record_test"
        ) as record:
            status, output, errors = self.invoke([
                "setup-model", "--download", "--model-dir", str(model_dir)
            ])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        setup.assert_called_once_with(model_dir, download=True)
        record.assert_not_called()
        self.assertIn(str(model_dir), output)

    def test_invalid_gain_device_and_rate_fail_before_model_or_microphone(self):
        cases = [
            ["--gain-db", "nan"], ["--gain-db", "inf"],
            ["--gain-db", "31"], ["--gain-db", "-31"],
            ["--device", "-1"], ["--device", "headset"],
            ["--sample-rate", "0"], ["--sample-rate", "44100.5"],
        ]
        with patch("aqua.cli.OfflineRecognizer") as model_class, patch(
            "aqua.cli.record_test"
        ) as record:
            for options in cases:
                with self.subTest(options=options):
                    output, errors = StringIO(), StringIO()
                    with redirect_stdout(output), redirect_stderr(errors):
                        with self.assertRaises(SystemExit) as exited:
                            main(["transcribe", *options])
                    self.assertEqual(exited.exception.code, 2)
                    self.assertIn("error:", errors.getvalue())
            model_class.assert_not_called()
            record.assert_not_called()

    def test_model_setup_failure_prevents_microphone_capture(self):
        with patch("aqua.cli.OfflineRecognizer", side_effect=ModelSetupError("Run setup-model --download")), patch(
            "aqua.cli.record_test"
        ) as record:
            status, output, errors = self.invoke(["transcribe"])
        self.assertEqual(status, 1)
        record.assert_not_called()
        self.assertIn("setup-model --download", errors)
        self.assertNotIn("Recording 5 seconds", output)
        self.assertNotIn("Traceback", errors)

    def test_recognition_failure_is_friendly_and_nonzero(self):
        with patch("aqua.cli.OfflineRecognizer") as model_class, patch(
            "aqua.cli.record_test", return_value=self.recording(sample_rate=1000)
        ):
            model_class.return_value.transcribe.side_effect = SpeechError("generation failed")
            status, output, errors = self.invoke(["transcribe"])
        self.assertEqual(status, 1)
        self.assertIn("generation failed", errors)
        self.assertIn("recognition error", errors)
        self.assertNotIn("Traceback", output + errors)

    def test_microphone_failure_never_calls_recognition(self):
        with patch("aqua.cli.OfflineRecognizer") as model_class, patch(
            "aqua.cli.record_test", side_effect=AudioError("microphone disconnected")
        ):
            status, _output, errors = self.invoke(["transcribe"])
        self.assertEqual(status, 1)
        model_class.return_value.transcribe.assert_not_called()
        self.assertIn("microphone disconnected", errors)

    def test_interrupt_during_capture_or_recognition_returns_130(self):
        for stage in ("capture", "recognition"):
            with self.subTest(stage=stage):
                with patch("aqua.cli.OfflineRecognizer") as model_class, patch(
                    "aqua.cli.record_test", return_value=self.recording(sample_rate=1000)
                ) as record:
                    if stage == "capture":
                        record.side_effect = KeyboardInterrupt()
                    else:
                        model_class.return_value.transcribe.side_effect = KeyboardInterrupt()
                    status, output, errors = self.invoke(["transcribe"])
                self.assertEqual(status, 130)
                self.assertIn("cancelled", errors)
                self.assertIn("no recording was saved", errors)
                self.assertNotIn("Traceback", output + errors)

    def test_mic_diagnostics_show_selected_input_without_loading_speech_model(self):
        with patch("aqua.cli.OfflineRecognizer") as model_class, patch(
            "aqua.cli.record_test", return_value=self.recording(sample_rate=1000)
        ):
            status, output, errors = self.invoke(["mic-test", "--device", "11", "--diagnostics"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        model_class.assert_not_called()
        self.assertIn("Input 11", output)
        self.assertIn("Synthetic Realtek microphone", output)
        self.assertIn("Windows WASAPI", output)
        self.assertIn("Source diagnostics", output)
        self.assertIn("p20", output)
        self.assertIn("p90", output)

    def test_mic_diagnostics_accept_recordings_without_device_metadata(self):
        with patch("aqua.cli.record_test", return_value=self.recording(sample_rate=1000, selected=False)):
            status, output, errors = self.invoke(["mic-test", "--diagnostics"])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("Source diagnostics", output)
        self.assertIn("Captured 5.0s", output)


if __name__ == "__main__":
    unittest.main()
