"""Phonemization for sherpa-onnx 2.x, with espeak-ng replaced by a fake."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aqua.voice import phonemes
from aqua.voice.engines import VoiceError
from aqua.voice.phonemes import EspeakPhonemizer, KokoroPhonemizer, espeak_to_misaki, load_tokens

REAL_ESPEAK = phonemes._espeak  # captured before any test patches it


class PhonemeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.tokens = root / "tokens.txt"
        symbols = " ;:,.!?—…\"()“”AIOQWYabdefhijklmnprstuvwzæðŋɑɒɔəɛɜɡɪɹʃʊʌʒʤʧˈˌθᵊː"
        self.tokens.write_text("\n".join(f"{s} {i}" for i, s in enumerate(symbols)) + "\n", encoding="utf-8")
        self.us = root / "lexicon-us-en.txt"
        self.us.write_text("hello h ə l ˈ O\nopen ˈ O p ᵊ n\nnotepad n ˈ O t p ˌ æ d\n", encoding="utf-8")
        self.calls = []

        def espeak(text, lang):
            self.calls.append((text, lang))
            table = {"sainz": "sˈeɪnz", "3.5": "θɹˈiː pɔɪnt fˈaɪv", "judge": "dʒˈʌdʒ", "home": "hˈoʊm"}
            return [list(table.get(text, text))]

        manager = patch("aqua.voice.phonemes._espeak", return_value=espeak)
        manager.start()
        self.addCleanup(manager.stop)

    def test_espeak_to_misaki_mapping(self):
        self.assertEqual(espeak_to_misaki("hˈoʊm"), "hˈOm")
        self.assertEqual(espeak_to_misaki("θɹˈiː pɔɪnt fˈaɪv"), "θɹˈi pYnt fˈIv")
        self.assertEqual(espeak_to_misaki("dʒˈʌdʒ tʃˈɜːtʃ"), "ʤˈʌʤ ʧˈɜʧ")
        self.assertEqual(espeak_to_misaki("bˈɚd"), "bˈəɹd")
        self.assertEqual(espeak_to_misaki("ɡˈəʊ", british=True), "ɡˈQ")
        self.assertEqual(espeak_to_misaki("kˈɑːlz", british=True), "kˈɑːlz")  # British keeps length marks

    def test_kokoro_uses_lexicon_first_then_espeak_for_unknown_words(self):
        kokoro = KokoroPhonemizer(self.us, self.tokens, "en-us")
        self.assertEqual(kokoro.phonemes("Hello, Sainz! Open Notepad?"), "həlˈO, sˈAnz! ˈOpᵊn nˈOtpˌæd?")
        self.assertEqual(self.calls, [("sainz", "en-us")])
        kokoro.phonemes("Sainz again")  # cached: espeak is not asked twice for the same word
        self.assertEqual(self.calls.count(("sainz", "en-us")), 1)

    def test_numbers_symbols_and_unknown_characters(self):
        kokoro = KokoroPhonemizer(self.us, self.tokens, "en-us")
        self.assertEqual(kokoro.phonemes("3.5 - open & #"), "θɹˈi pYnt fˈIv ˈOpᵊn")
        self.assertEqual(kokoro.codepoints("--- !"), [])
        kokoro.lexicon["weird"] = "w€ɪɹd"  # a symbol outside the model's token set is dropped
        self.assertEqual(kokoro.phonemes("weird"), "wɪɹd")

    def test_espeak_phonemizer_passes_whole_text(self):
        espeak = EspeakPhonemizer("en-us")
        self.assertEqual(espeak.codepoints("home"), [[ord(c) for c in "hˈoʊm"]])
        self.assertEqual(self.calls[-1], ("home", "en-us"))
        self.assertEqual(EspeakPhonemizer("").lang, "en-us")

    def test_tokens_file_parsing(self):
        symbols = load_tokens(self.tokens)
        self.assertIn(" ", symbols)
        self.assertIn("ᵊ", symbols)

    def test_missing_piper_phonemize_is_a_clear_voice_error(self):
        with patch.dict("sys.modules", {"piper_phonemize": None}):
            with self.assertRaisesRegex(VoiceError, "piper_phonemize"):
                REAL_ESPEAK()


if __name__ == "__main__":
    unittest.main()
