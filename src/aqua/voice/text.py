"""Turn streamed reply text into short, speakable sentences.

Pure text processing: no audio, models or threads. The splitter is fed the
same chunks that are printed, so speech can start before the reply finishes.
"""

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

    def reset(self) -> None:
        self._buffer = ""

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

    def _boundary(self, *, final: bool) -> int | None:
        """Index just past the next complete sentence, or None to wait."""
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
            if index - offset >= self.max_chars:
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
        if len(pending) > self.max_chars:
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

    Sentences after the limit are held, not spoken, until the reply ends. If
    the reply's final sentence is a question it is always spoken, so the user
    hears what Aqua is asking.
    """

    def __init__(self, limit: int = MAX_SPOKEN_SENTENCES):
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer.")
        self.limit = limit
        self.reset()

    def reset(self) -> None:
        self._spoken = 0
        self._held = 0
        self._last_held = None

    def push(self, sentence: str) -> list[str]:
        if not sentence:
            return []
        if self._spoken < self.limit:
            self._spoken += 1
            return [sentence]
        self._held += 1
        self._last_held = sentence
        return []

    def finish(self) -> list[str]:
        question = self._last_held if self._last_held is not None and is_question(self._last_held) else None
        omitted = self._held - (1 if question else 0)
        result = ([REST_NOTICE] if omitted else []) + ([question] if question else [])
        self.reset()
        return result


def spoken_sentences(sentences, limit: int = MAX_SPOKEN_SENTENCES) -> list[str]:
    """Apply the spoken-length rule to a complete list of speakable sentences."""
    budget = SpokenBudget(limit)
    result = [item for sentence in sentences for item in budget.push(sentence)]
    return result + budget.finish()
