"""Hint options are local prompt context, never transcript rewriting."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from aqua.speech.models import SpeechError
from aqua.speech.recognizer import OfflineRecognizer
from aqua.speech.vocabulary import (
    DEFAULT_HOTWORDS,
    DEFAULT_VOCABULARY,
    normalize_hotwords,
)


class VocabularyTests(unittest.TestCase):
    def test_default_vocabulary_preserves_requested_spellings(self):
        self.assertEqual(DEFAULT_VOCABULARY, (
            "Aqua", "Ollama", "Qwen", "Python", "VS Code", "GitHub", "Whisper", "ChatGPT",
        ))
        self.assertEqual(DEFAULT_HOTWORDS, ", ".join(DEFAULT_VOCABULARY))

    def test_custom_multiword_terms_are_preserved_with_normalized_spacing(self):
        self.assertEqual(normalize_hotwords("  Aqua,\n VS   Code, C++ \t"), "Aqua, VS Code, C++")

    def test_empty_nontext_and_control_char_hints_are_rejected(self):
        for value in ("", " \n\t ", None, 42, "Aqua\x00", "Aqua\x7f"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_hotwords(value)


class RecognitionHintsTests(unittest.TestCase):
    def setUp(self):
        self.recognizer = object.__new__(OfflineRecognizer)
        self.model = Mock()
        self.recognizer._model = self.model
        self.source = np.array([1024, -1024] * 100, dtype=np.int16).tobytes()
        self.info = SimpleNamespace(language="en", language_probability=0.9, duration_after_vad=1.0)
        self.result_text("The garden is quiet today.")

    def result_text(self, text):
        self.model.transcribe.return_value = (iter([SimpleNamespace(text=text)]), self.info)

    def test_disabled_hints_preserve_original_backend_options(self):
        self.recognizer.transcribe(self.source, 16000)
        options = self.model.transcribe.call_args.kwargs
        self.assertNotIn("hotwords", options)
        self.assertNotIn("initial_prompt", options)
        self.assertNotIn("prefix", options)

    def test_enabled_hints_forward_only_hotwords_as_context(self):
        self.recognizer.transcribe(self.source, 16000, hotwords=DEFAULT_HOTWORDS)
        options = self.model.transcribe.call_args.kwargs
        self.assertEqual(options["hotwords"], DEFAULT_HOTWORDS)
        self.assertNotIn("initial_prompt", options)
        self.assertNotIn("prefix", options)

    def test_custom_hint_text_is_forwarded_without_default_words_appended(self):
        self.recognizer.transcribe(self.source, 16000, hotwords=" FastAPI,\n CTranslate2 ")
        self.assertEqual(self.model.transcribe.call_args.kwargs["hotwords"], "FastAPI, CTranslate2")

    def test_unrelated_transcript_is_returned_unchanged_with_hints(self):
        sentence = "The garden is quiet today."
        self.result_text(sentence)
        result = self.recognizer.transcribe(self.source, 16000, hotwords=DEFAULT_HOTWORDS)
        self.assertEqual(result.text, sentence)

    def test_name_misspelling_is_not_replaced_by_a_hint(self):
        self.result_text("Hello, Ako. Can you hear me?")
        result = self.recognizer.transcribe(self.source, 16000, hotwords=DEFAULT_HOTWORDS)
        self.assertEqual(result.text, "Hello, Ako. Can you hear me?")

    def test_silence_cannot_become_vocabulary_only_output(self):
        result = self.recognizer.transcribe(b"\x00\x00" * 100, 16000, hotwords=DEFAULT_HOTWORDS)
        self.assertEqual(result.text, "")
        self.model.transcribe.assert_not_called()

    def test_invalid_custom_hints_fail_before_decoder_inference(self):
        with self.assertRaises(SpeechError) as error:
            self.recognizer.transcribe(self.source, 16000, hotwords=" \t")
        self.assertIn("at least one vocabulary hint", str(error.exception))
        self.model.transcribe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
