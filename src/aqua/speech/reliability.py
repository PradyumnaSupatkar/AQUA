"""Conservative voice-chat gates; these heuristics do not certify accuracy."""

from math import isfinite

from aqua.audio.recording import Recording
from aqua.speech.recognizer import Transcript


def transcript_rejection_reason(recording: Recording, transcript: Transcript) -> str | None:
    """Return a retry hint instead of sending empty or suspect speech to chat.

    Confidence fields come from Whisper segments, not language detection.
    Old Transcript callers can omit them; VAD, text, and capture checks still
    apply. Quiet speech is not rejected solely for its input volume.
    """
    text = transcript.text.strip()
    if not text or len(text) < 2 or not any(character.isalnum() for character in text):
        return "no usable speech was recognized"
    if recording.overflow_count:
        return "the microphone lost audio; retry after reducing system load"
    if recording.sample_rate <= 0 or recording.frames <= 0 or recording.levels.peak == 0:
        return "the microphone captured no signal"
    if not isfinite(transcript.speech_seconds) or transcript.speech_seconds < 0.3:
        return "too little speech was detected"
    if transcript.saturated_samples / (recording.duration * 16000) > 0.01:
        return "recognition input clipped excessively; reduce gain and retry"
    if transcript.avg_logprob is not None and (
        not isfinite(transcript.avg_logprob) or transcript.avg_logprob < -1.0
    ):
        return "speech recognition confidence was low"
    if transcript.no_speech_probability is not None and (
        not isfinite(transcript.no_speech_probability) or transcript.no_speech_probability > 0.6
    ):
        return "the recognizer was uncertain whether speech was present"
    if transcript.compression_ratio is not None and (
        not isfinite(transcript.compression_ratio) or transcript.compression_ratio > 2.4
    ):
        return "the recognized text was unusually repetitive"
    return None
