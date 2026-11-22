"""Questions about events too recent for the model's memory (and maybe for Wikipedia)."""

from datetime import datetime
import re

# Qwen3 4B's training data ends around 2024; later years are news to it.
MODEL_KNOWLEDGE_YEAR = 2024

_RECENT_WORDS = re.compile(r"\b(?:latest|recent(?:ly)?|current(?:ly)?|today|tonight|yesterday|this\s+(?:week|month|"
                           r"year|season|weekend)|last\s+(?:night|week|weekend)|right\s+now|just)\b", re.IGNORECASE)
# A reply that declares an event to be in the future is a guess from stale memory.
_NOT_YET = re.compile(r"\b(?:has|have|had)\s*(?:not|n't)\s+(?:yet\s+)?(?:happened|occurred|taken\s+place|been\s+held)|"
                      r"\bnot\s+(?:yet\s+)?(?:happened|occurred|taken\s+place)|\bhasn't\s+happened|\bfuture\s+event|"
                      r"\bis\s+(?:scheduled|set)\s+to\s+(?:take\s+place|be\s+held)|\bin\s+the\s+future\b", re.IGNORECASE)


def _years(text: str) -> list[int]:
    return [int(year) for year in re.findall(r"\b(20\d\d)\b", text)]


def mentions_recent(text: str, now: datetime) -> bool:
    """Recent enough that Wikipedia may lag: this year or later, or words like "latest"."""
    return any(year >= now.year for year in _years(text)) or bool(_RECENT_WORDS.search(text))


def claims_not_happened(reply: str) -> bool:
    return bool(_NOT_YET.search(reply))


def beyond_model_memory(question: str, reply: str, now: datetime) -> bool:
    """The model should look this up instead of answering from training data."""
    return (mentions_recent(question, now) or any(year > MODEL_KNOWLEDGE_YEAR for year in _years(question))
            or bool(_NOT_YET.search(reply)))
