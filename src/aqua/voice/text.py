"""Turn streamed reply text into short, speakable sentences and chunks.

Pure text processing: no audio, models or threads. The splitter is fed the
same chunks that are printed, so speech can start before the reply finishes.
Long sentences are also cut into chunks at natural breaks while they stream,
so synthesis never waits for a whole 40-word sentence.
"""

from dataclasses import dataclass
import re

MAX_SENTENCE_CHARS = 220
MAX_SPOKEN_SENTENCES = 3
CODE_NOTICE = "I've put the code on screen."
REST_NOTICE = "The rest is on screen."

_FENCE = "```"
_TERMINATORS = ".!?"
_CLOSERS = "\"')]}”’"
# Lower-case abbreviations whose trailing period never ends a sentence.
_ABBREVIATIONS = frozenset({
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "vs.", "approx.",
    "fig.", "no.", "dept.", "est.", "inc.", "ltd.", "co.",
})
_DOTTED = re.compile(r"(?:[A-Za-z]\.){2,}")          # e.g. i.e. U.S.
_LIST_NUMBER = re.compile(r"\d{1,3}\.")              # "1." at a sentence start

# Chunking inside long sentences. A chunk ends at a natural break (a word
# ending in , ; : or a dash) once it has CHUNK_MIN_WORDS, or at a space at the
# hard cap. The first chunks of a reply are kept smaller so speech starts and
# keeps flowing while the model is still generating.
CHUNK_MIN_WORDS = 6
CHUNK_EARLY_MAX_WORDS = 10
CHUNK_EARLY_COUNT = 2
CHUNK_MAX_WORDS = 16
FRAGMENT_MIN_WORDS = 3
_DASHES = frozenset({"-", "--", "\u2013", "\u2014"})
_COMPLETE_WORD = re.compile(r"\S+(?=\s)")
_WORD = re.compile(r"\S+")


@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    sentence_end: bool


def _is_break(word: str) -> bool:
    return word in _DASHES or word.endswith((",", ";", ":", "\u2013", "\u2014"))


def _is_hey(word: str) -> bool:
    # Never separate "Hey" from "Aqua": speakable() removes the phrase only
    # when both words are in the same chunk.
    return word.strip(",.;:!?\"'\u2013\u2014-").casefold() == "hey"


def _token_before(text: str, end: int) -> str:
    """The whitespace-delimited token ending at index ``end`` (inclusive)."""
    start = end
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    return text[start:end + 1].lstrip("\"'([{“‘*_")


class SentenceSplitter:
    """Incrementally emit complete sentences from streamed chunks.

    A terminator only counts once the following character is known, so a
    chunk boundary inside "3.5", "v0.8", "e.g." or a URL never splits it.
    Fenced code blocks are emitted whole, as a single unit.
    """

    def __init__(self, *, max_chars: int = MAX_SENTENCE_CHARS):
        if type(max_chars) is not int or max_chars < 20:
            raise ValueError("max_chars must be an integer of at least 20.")
        self.max_chars = max_chars
        self._buffer = ""
        self._chunks = 0

    def reset(self) -> None:
        self._buffer = ""
        self._chunks = 0

    # ----- chunk API (speech): sentences, and long sentences cut while streaming -----
    def feed_chunks(self, text: str) -> list[Chunk]:
        if not isinstance(text, str) or not text:
            return []
        self._buffer += text
        return self._drain_chunks(final=False)

    def flush_chunks(self) -> list[Chunk]:
        chunks = self._drain_chunks(final=True)
        tail = self._buffer.strip()
        self._buffer = ""
        return chunks + (self._split_sentence(tail) if tail else [])

    def _drain_chunks(self, *, final: bool) -> list[Chunk]:
        chunks = []
        while (cut := self._boundary(final=final, char_cap=False)) is not None:
            sentence, self._buffer = self._buffer[:cut].strip(), self._buffer[cut:]
            if sentence:
                chunks += self._split_sentence(sentence)
        while (cut := self._streaming_cut()) is not None:
            piece, self._buffer = self._buffer[:cut].strip(), self._buffer[cut:]
            chunks.append(self._emit(piece, False))
        return chunks

    def _emit(self, text: str, sentence_end: bool) -> Chunk:
        self._chunks += 1
        return Chunk(text, sentence_end)

    def _word_cap(self) -> int:
        return CHUNK_EARLY_MAX_WORDS if self._chunks < CHUNK_EARLY_COUNT else CHUNK_MAX_WORDS

    def _cut_after(self, words: list, total: int | None = None) -> int | None:
        """Index of the word to cut after, or None. ``total`` (complete
        sentences only) forbids leaving fewer than FRAGMENT_MIN_WORDS behind."""
        cap = self._word_cap()
        for index, match in enumerate(words):
            count = index + 1
            if count > cap or (total is not None and total - count < FRAGMENT_MIN_WORDS):
                return None
            word = match.group()
            if count >= CHUNK_MIN_WORDS and _is_break(word) and not _is_hey(word):
                return index
            if count == cap:
                if not _is_hey(word):
                    return index
                return index - 1 if count - 1 >= FRAGMENT_MIN_WORDS else None
        return None

    def _streaming_cut(self) -> int | None:
        """Cut an unfinished long sentence at a natural break or the cap."""
        if self._buffer.lstrip().startswith(_FENCE):
            return None  # Code blocks stay whole.
        words = list(_COMPLETE_WORD.finditer(self._buffer))
        # Require FRAGMENT_MIN_WORDS complete words after the cut, so no tiny
        # tail is left behind and streamed input chunks exactly like whole text.
        index = self._cut_after(words, total=len(words))
        return None if index is None else words[index].end()

    def _split_sentence(self, sentence: str) -> list[Chunk]:
        if sentence.startswith(_FENCE):
            return [self._emit(sentence, True)]
        chunks = []
        rest = sentence
        while True:
            words = list(_WORD.finditer(rest))
            index = self._cut_after(words, total=len(words))
            if index is None:
                chunks.append(self._emit(rest, True))
                return chunks
            cut = words[index].end()
            chunks.append(self._emit(rest[:cut].strip(), False))
            rest = rest[cut:].strip()

    def feed(self, chunk: str) -> list[str]:
        if not isinstance(chunk, str) or not chunk:
            return []
        self._buffer += chunk
        return self._drain(final=False)

    def flush(self) -> list[str]:
        sentences = self._drain(final=True)
        tail = self._buffer.strip()
        self._buffer = ""
        return sentences + ([tail] if tail else [])

    def _drain(self, *, final: bool) -> list[str]:
        sentences = []
        while True:
            cut = self._boundary(final=final)
            if cut is None:
                break
            sentence, self._buffer = self._buffer[:cut].strip(), self._buffer[cut:]
            if sentence:
                sentences.append(sentence)
        return sentences

    def _boundary(self, *, final: bool, char_cap: bool = True) -> int | None:
        """Index just past the next complete sentence, or None to wait.

        ``char_cap`` (sentence API) cuts over-long text at max_chars; the chunk
        API bounds length by words instead, so a cap never fakes a sentence end.
        """
        text = self._buffer
        stripped = text.lstrip()
        offset = len(text) - len(stripped)
        if stripped.startswith(_FENCE):
            close = text.find(_FENCE, offset + len(_FENCE))
            if close < 0:
                return None  # Wait for the closing fence; flush() emits the rest.
            return close + len(_FENCE)
        fence = text.find(_FENCE, offset)
        limit = len(text) if fence < 0 else fence
        index = offset
        while index < limit:
            if char_cap and index - offset >= self.max_chars:
                return offset + self._cap(text[offset:index])
            character = text[index]
            if character == "\n":
                return index + 1
            if character in _TERMINATORS:
                end = index
                while end + 1 < len(text) and text[end + 1] in _TERMINATORS:
                    end += 1
                while end + 1 < len(text) and text[end + 1] in _CLOSERS:
                    end += 1
                if end + 1 >= len(text):
                    if not final:
                        return None  # Need one more character to decide.
                    return end + 1
                if text[end + 1].isspace() and not self._protected(text, offset, index):
                    return end + 1
                index = end + 1
                continue
            index += 1
        if fence >= 0 and fence > offset:
            return fence  # Text before a code fence is its own unit.
        pending = text[offset:limit]
        if char_cap and len(pending) > self.max_chars:
            return offset + self._cap(pending)
        return None

    @staticmethod
    def _protected(text: str, start: int, index: int) -> bool:
        """True when the period at ``index`` belongs to an abbreviation."""
        if text[index] != ".":
            return False
        token = _token_before(text, index)
        lowered = token.casefold()
        if lowered in _ABBREVIATIONS or _DOTTED.fullmatch(token):
            return True
        # "1. First step" at the start of a sentence is a list marker.
        return bool(_LIST_NUMBER.fullmatch(token)) and not text[start:index - len(token) + 1].strip()

    def _cap(self, pending: str) -> int:
        window = pending[:self.max_chars]
        for separator in (", ", "; ", " "):
            cut = window.rfind(separator)
            if cut > self.max_chars // 3:
                return cut + len(separator)
        return self.max_chars


_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'()]+?(?=[.,!?;:)\]]*(?:\s|$))", re.IGNORECASE)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)\s]+)\)")
_FENCED = re.compile(r"```.*?(?:```|$)", re.DOTALL)
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF︎️‍⃣]"
)
_WAKE = re.compile(r"\bhey\W{0,3}aqua\b", re.IGNORECASE)
_LINE_PREFIX = re.compile(r"^\s*(?:#{1,6}\s*|>+\s*|(?:[-*+•]|\d{1,3}[.)])\s+)+", re.MULTILINE)


def speakable(sentence: str) -> str:
    """Return what to say aloud for one sentence, or "" when nothing is speakable."""
    if not isinstance(sentence, str):
        return ""
    text = sentence
    had_code = _FENCE in text
    text = _FENCED.sub(" ", text)
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _URL.sub("a link", text)
    text = _LINE_PREFIX.sub("", text)
    text = text.replace("`", "")
    text = re.sub(r"(\*{1,3}|_{2,3})(?=\S)(.+?)(?<=\S)\1", r"\2", text)
    text = re.sub(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)", r"\1", text)
    text = re.sub(r"[*#>]", " ", text)
    text = re.sub(r"(?<=\w)_(?=\w)", " ", text)
    text = text.replace("_", " ")
    text = _EMOJI.sub("", text)
    text = _WAKE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[\s,;:.\-]+", "", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    spoken = text if any(character.isalnum() for character in text) else ""
    if had_code:
        return f"{spoken} {CODE_NOTICE}".strip() if spoken else CODE_NOTICE
    return spoken


def is_question(sentence: str) -> bool:
    return isinstance(sentence, str) and sentence.rstrip().rstrip(_CLOSERS).endswith("?")


class SpokenBudget:
    """Speak at most the first N sentences, then a short notice.

    Pieces are pushed as they stream; ``end`` marks the last piece of a
    sentence (a sentence may arrive as several chunks, and the limit counts
    sentences, not chunks). Sentences after the limit are held, not spoken,
    until the reply ends. If the reply's final sentence is a question it is
    always spoken, so the user hears what Aqua is asking.
    """

    def __init__(self, limit: int = MAX_SPOKEN_SENTENCES):
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer.")
        self.limit = limit
        self.reset()

    def reset(self) -> None:
        self._spoken = 0
        self._held = 0
        self._last_held: list[str] | None = None
        self._current: list[str] = []
        self._current_spoken: bool | None = None

    def _end_sentence(self) -> None:
        if self._current_spoken is True:
            self._spoken += 1
        elif self._current_spoken is False:
            self._held += 1
            self._last_held = self._current
        self._current, self._current_spoken = [], None

    def push(self, sentence: str, *, end: bool = True) -> list[str]:
        result = []
        if sentence:
            if self._current_spoken is None:
                self._current_spoken = self._spoken < self.limit
            if self._current_spoken:
                result = [sentence]
            else:
                self._current = self._current + [sentence]
        if end:
            self._end_sentence()
        return result

    def finish(self) -> list[str]:
        self._end_sentence()
        question = self._last_held if self._last_held and is_question(self._last_held[-1]) else None
        omitted = self._held - (1 if question else 0)
        result = ([REST_NOTICE] if omitted else []) + (list(question) if question else [])
        self.reset()
        return result


def spoken_sentences(sentences, limit: int = MAX_SPOKEN_SENTENCES) -> list[str]:
    """Apply the spoken-length rule to a complete list of speakable sentences."""
    budget = SpokenBudget(limit)
    result = [item for sentence in sentences for item in budget.push(sentence)]
    return result + budget.finish()
