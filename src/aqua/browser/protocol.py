"""Messages between Aqua, the native host and the Aqua browser extension.

Both links use Chrome's native-messaging framing: a 4-byte little-endian length, then UTF-8
JSON. The browser caps host-to-extension messages at 1 MB; Aqua uses the same cap both ways.
"""

import base64
import hashlib
import json
import struct

HOST_NAME = "com.aqua.browser_bridge"
# Derived from the public "key" in extension/manifest.json (see extension_id_from_key).
EXTENSION_ID = "kcmoalhlocdallmnhgoedkadigealcjj"
ORIGIN = f"chrome-extension://{EXTENSION_ID}/"
BROWSERS = ("brave", "chrome")
MAX_MESSAGE = 1024 * 1024
PIPE_PREFIX = r"\\.\pipe\aqua-browser-bridge-"


class ProtocolError(ValueError):
    pass


def extension_id_from_key(key: str) -> str:
    """Chromium's extension ID: the first 128 bits of SHA-256 of the public key (DER),
    written with the letters a-p for the hex digits 0-f."""
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def pipe_name(user_sid: str) -> str:
    """One pipe per Windows user; the name hides the SID but does not protect anything (the ACL does)."""
    return PIPE_PREFIX + hashlib.sha256(user_sid.encode("ascii")).hexdigest()[:16]


def encode(message: dict) -> bytes:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_MESSAGE:
        raise ProtocolError("Message too large.")
    return struct.pack("<I", len(body)) + body


def read_exactly(read, size: int) -> bytes | None:
    """``size`` bytes from ``read(n)``, or None at end of stream before the first byte."""
    chunks, left = [], size
    while left:
        chunk = read(left)
        if not chunk:
            if left == size:
                return None
            raise ProtocolError("Stream ended inside a message.")
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def read_message(read) -> dict | None:
    """The next message from ``read(n)``, or None when the stream has ended."""
    header = read_exactly(read, 4)
    if header is None:
        return None
    (size,) = struct.unpack("<I", header)
    if size > MAX_MESSAGE:
        raise ProtocolError("Message too large.")
    body = read_exactly(read, size) if size else b""
    if body is None:
        raise ProtocolError("Stream ended inside a message.")
    try:
        message = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError("Message is not JSON.") from exc
    if not isinstance(message, dict):
        raise ProtocolError("Message is not a JSON object.")
    return message
