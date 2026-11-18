"""Retired regex command parser, retained only for historical compatibility.

V0.7 input modes use Qwen's conversational tool calls and never route here.
"""
import re

from .launcher import ALLOWED_APPS, normalize_app_name


COMMAND_PREFIX = (
    r"(?:(?:hey\s+)?aqua\s*,?\s+)?"
    r"(?:(?:can|could)\s+you\s+(?:please\s+)?|please\s+)?"
)

LAUNCH_VERBS = r"(?:open|launch|start)"


def parse_launch_request(text: str) -> str | None:
    cleaned = text.strip().rstrip(".!?").strip()

    pattern = (
        COMMAND_PREFIX
        + LAUNCH_VERBS
        + r"\s+([a-z][a-z0-9 ]*)"
    )

    match = re.fullmatch(
        pattern,
        cleaned,
        flags=re.IGNORECASE,
    )

    if match is None:
        return None

    app_name = normalize_app_name(match.group(1))

    if app_name not in ALLOWED_APPS:
        return None

    return app_name


def is_launch_request(text: str) -> bool:
    pattern = (
        r"^"
        + COMMAND_PREFIX
        + LAUNCH_VERBS
        + r"\b"
    )

    return re.match(
        pattern,
        text.strip(),
        flags=re.IGNORECASE,
    ) is not None
