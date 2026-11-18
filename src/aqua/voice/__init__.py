"""Spoken replies (text-to-speech output). Not to be confused with aqua.speech,
which is speech *recognition*. Heavy engines are imported only on demand."""

from .engines import VoiceError

__all__ = ["VoiceError"]
