"""Optional spelling hints for Whisper; never transcript substitutions.

The installed faster-whisper 1.2.1 inserts ``hotwords`` as prior-context
tokens. A nonempty ``prefix`` suppresses those hints, while ``initial_prompt``
adds separate previous-context tokens. Aqua uses hotwords alone to avoid
duplicating that context or supplying a required transcript prefix. The
backend keeps at most the first 223 hotword tokens, so lists should be short.
Hints can bias recognition and do not guarantee any word will be recognized.
"""

DEFAULT_VOCABULARY = (
    "Aqua",
    "Ollama",
    "Qwen",
    "Python",
    "VS Code",
    "GitHub",
    "Whisper",
    "ChatGPT",
)
DEFAULT_HOTWORDS = ", ".join(DEFAULT_VOCABULARY)


def normalize_hotwords(value: str) -> str:
    """Accept a short human-written hint list without changing its words."""
    if not isinstance(value, str):
        raise ValueError("Hotwords must be text.")
    text = " ".join(value.split())
    if not text:
        raise ValueError("Hotwords must contain at least one vocabulary hint.")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValueError("Hotwords must not contain control characters.")
    return text
