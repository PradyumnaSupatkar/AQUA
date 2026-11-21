"""Text -> phonemes for sherpa-onnx 2.x, which no longer phonemizes internally.

sherpa-onnx >= 2.0 ignores the voices' espeak-ng-data and expects phonemes in
GenerationConfig.phoneme_codepoints. Phonemes come from espeak-ng through
piper_phonemize (imported only when a real voice loads):

- Piper and Kitten: the whole sentence through espeak-ng, as upstream does.
- Kokoro: words in Kokoro's own lexicon use it (its "misaki" phoneme set);
  unknown words and numbers go through espeak-ng and are mapped onto misaki
  symbols, mirroring Kokoro's espeak fallback. Symbols outside the model's
  token set are dropped, never sent.
"""

from functools import lru_cache
from pathlib import Path
import re

from .engines import VoiceError

PUNCTUATION = frozenset(';:,.!?—…"()“”')
# File names such as index.js or chat.py are read "index dot j s": every part
# starts with a letter and the last has 2+ characters, so e.g., U.S. and v0.8
# are not affected.
_DOTTED = r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)*\.[A-Za-z][A-Za-z0-9]+"
_TOKEN = re.compile(_DOTTED + r"|[A-Za-z]+(?:['’][A-Za-z]+)*|\d+(?:[.,]\d+)*|\S")
# espeak-ng IPA -> misaki (longest first). US and GB differ only in the "oʊ"/"əʊ" vowel.
_E2M = (("aɪ", "I"), ("aʊ", "W"), ("eɪ", "A"), ("ɔɪ", "Y"), ("oʊ", "O"), ("əʊ", "Q"),
        ("dʒ", "ʤ"), ("tʃ", "ʧ"), ("ɚ", "əɹ"), ("ɝ", "ɜɹ"), ("ɐ", "ə"), ("r", "ɹ"), ("g", "ɡ"),
        ("ɬ", "l"), ("x", "k"), ("e", "A"), ("o", "O"), ("ʲ", ""))
# American misaki has no length marks; the British lexicon keeps them.
_E2M_US = _E2M + (("ː", ""),)


def _espeak():
    try:
        from piper_phonemize import phonemize_espeak
    except ImportError as exc:
        raise VoiceError("Speech output needs piper_phonemize, a core Aqua dependency; "
                         "reinstall Aqua with pip install -e .") from exc
    return phonemize_espeak


def espeak_phonemes(text: str, lang: str) -> list[str]:
    """One phoneme string per espeak sentence."""
    return ["".join(sentence) for sentence in _espeak()(text, lang)]


def to_codepoints(sentences: list[str]) -> list[list[int]]:
    return [[ord(character) for character in sentence] for sentence in sentences if sentence.strip()]


class EspeakPhonemizer:
    """Piper and Kitten: espeak-ng IPA for the whole text."""

    def __init__(self, lang: str = "en-us"):
        self.lang = lang or "en-us"
        _espeak()  # Fail at load time, not on the first reply.

    def codepoints(self, text: str) -> list[list[int]]:
        return to_codepoints(espeak_phonemes(text, self.lang))


def load_tokens(path: Path) -> frozenset[str]:
    symbols = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].strip().isdigit():
            symbols.add(parts[0] if parts[0] else " ")
    return frozenset(symbols)


def load_lexicon(path: Path) -> dict[str, str]:
    lexicon = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            word, _, phonemes = line.rstrip("\n").partition(" ")
            if word and phonemes:
                lexicon.setdefault(word, phonemes.replace(" ", ""))
    return lexicon


def espeak_to_misaki(phonemes: str, *, british: bool = False) -> str:
    for source, target in (_E2M if british else _E2M_US):
        phonemes = phonemes.replace(source, target)
    return phonemes


class KokoroPhonemizer:
    def __init__(self, lexicon_path: Path, tokens_path: Path, lang: str):
        self.lang = lang
        self.lexicon = load_lexicon(lexicon_path)
        self.tokens = load_tokens(tokens_path)
        _espeak()
        self.fallback = lru_cache(maxsize=4096)(self._fallback)

    def _fallback(self, word: str) -> str:
        return espeak_to_misaki(" ".join(espeak_phonemes(word, self.lang)).strip(), british=self.lang != "en-us")

    def phonemes(self, text: str) -> str:
        pieces = []
        for token in _TOKEN.findall(text):
            if token in PUNCTUATION:
                if pieces:
                    pieces[-1] += token
                continue
            if not token[0].isalnum():
                continue  # Symbols such as - / & are not spoken.
            words = token.split(".") if "." in token and token[0].isalpha() else [token]
            for index, part in enumerate(words):
                if index:
                    pieces.append(self.lexicon.get("dot") or self.fallback("dot"))
                word = part.replace("’", "'").lower()
                pieces.append(self.lexicon.get(word) or self.fallback(word))
        spoken = " ".join(piece for piece in pieces if piece)
        return "".join(character for character in spoken if character in self.tokens)

    def codepoints(self, text: str) -> list[list[int]]:
        return to_codepoints([self.phonemes(text)])
