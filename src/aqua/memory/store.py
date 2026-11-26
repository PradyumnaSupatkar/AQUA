"""Aqua's local memory: preferences applied by host code and short facts about the user.

Stored in %LOCALAPPDATA%\\Aqua\\memory\\memory.bin, encrypted at rest with Windows DPAPI for the
current user (CryptProtectData), so another Windows account cannot read it. Tests pass a fake
protector. Nothing here ever leaves the PC.
"""

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import time

MAX_FACTS = 200
MAX_FACT_CHARS = 200
FILE_NAME = "memory.bin"
_ENTROPY = b"Aqua local memory v1"


class MemoryError_(Exception):
    """The memory file could not be read or written."""


def memory_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Aqua" / "memory"


# -- encryption -----------------------------------------------------------------------------------

class DpapiProtector:
    """Windows DPAPI, current user scope (no UI, no machine scope)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        self._ctypes, self._Blob = ctypes, Blob
        self._crypt = ctypes.WinDLL("crypt32")
        self._kernel = ctypes.WinDLL("kernel32")
        for name in ("CryptProtectData", "CryptUnprotectData"):
            function = getattr(self._crypt, name)
            function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                                 ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
            function.restype = wintypes.BOOL
        self._kernel.LocalFree.argtypes = [ctypes.c_void_p]

    def _blob(self, data: bytes):
        buffer = self._ctypes.create_string_buffer(data, len(data))
        return self._Blob(len(data), self._ctypes.cast(buffer, self._ctypes.POINTER(self._ctypes.c_char))), buffer

    def _call(self, function, data: bytes) -> bytes:
        source, _keep = self._blob(data)
        entropy, _keep2 = self._blob(_ENTROPY)
        out = self._Blob()
        CRYPTPROTECT_UI_FORBIDDEN = 0x1
        if not function(self._ctypes.byref(source), None, self._ctypes.byref(entropy), None, None,
                        CRYPTPROTECT_UI_FORBIDDEN, self._ctypes.byref(out)):
            raise MemoryError_("Windows could not protect or unprotect the memory file.")
        try:
            return self._ctypes.string_at(out.pbData, out.cbData)
        finally:
            self._kernel.LocalFree(out.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._call(self._crypt.CryptProtectData, data)

    def unprotect(self, data: bytes) -> bytes:
        return self._call(self._crypt.CryptUnprotectData, data)


# -- records --------------------------------------------------------------------------------------

@dataclass(slots=True)
class Fact:
    id: str
    text: str
    created: float
    key: str = ""               # The subject ("my projects folder"): a new fact with it replaces the old one.


@dataclass(slots=True)
class Preference:
    id: str
    kind: str                   # browser
    site: str                   # "youtube.com", or "" for the default
    value: str                  # "Brave"
    created: float

    @property
    def text(self) -> str:
        if self.kind == "browser":
            return f"open {site_label(self.site)} in {self.value}" if self.site else f"use {self.value} for other websites"
        return f"{self.kind} {self.site} {self.value}".strip()


@dataclass(slots=True)
class MemoryData:
    facts: list[Fact] = field(default_factory=list)
    preferences: list[Preference] = field(default_factory=list)
    next_id: int = 1


_WORD = re.compile(r"[a-z0-9:\\/._-]+")
# "my projects are in D:\\Projects" -> key "projects"; "I live in Pune" -> "live".
_SUBJECT = re.compile(r"^(?:my|our|the\s+user'?s?)\s+(?P<s>[\w ]{2,40}?)\s+(?:is|are|was|were|lives?|sits?|"
                      r"(?:can\s+be\s+)?found|stays?|goes|go)\b|^i\s+(?P<v>live|work|study|prefer|use|am)\b", re.I)


def fact_key(text: str) -> str:
    match = _SUBJECT.search(text.strip())
    if not match:
        return ""
    return " ".join((match.group("s") or match.group("v")).casefold().split())


_SITE_LABELS = {"youtube.com": "YouTube", "github.com": "GitHub", "google.com": "Google", "gmail.com": "Gmail",
                "mail.google.com": "Gmail", "reddit.com": "Reddit", "netflix.com": "Netflix",
                "wikipedia.org": "Wikipedia", "wikipedia.com": "Wikipedia", "amazon.com": "Amazon", "x.com": "X",
                "twitter.com": "Twitter", "twitch.tv": "Twitch"}


def site_label(site: str) -> str:
    """"youtube.com" -> "YouTube" for replies; other sites as they are."""
    return _SITE_LABELS.get(site, site)


def private_parts(text: str) -> tuple[str, ...]:
    """What the look_up privacy guard must never send for this fact: the fact itself and its
    value ("My company is Zentrix Labs." -> "my company is zentrix labs", "zentrix labs")."""
    whole = " ".join(text.casefold().split()).rstrip(" .")
    match = re.search(r"\b(?:is|are|was|were|in|at|for|called|named)\s+(?P<v>.+)$", whole)
    return tuple(p for p in (whole, match.group("v").strip() if match else "") if len(p) >= 3)


def normalize_site(site: str) -> str:
    """"YouTube" / "https://www.youtube.com/watch" -> "youtube.com"; "" for the default."""
    text = (site or "").strip().casefold()
    if text in {"", "default", "*", "all", "any", "everything", "other", "others", "everything else", "web"}:
        return ""
    text = re.sub(r"^[a-z]+://", "", text).split("/")[0].removeprefix("www.")
    text = text.replace(" ", "")
    if "." not in text:
        text += ".com"
    return text


class MemoryStore:
    """Load/save the memory file. Every change is written at once (atomic replace)."""

    def __init__(self, path: Path | None = None, protector=None, *, clock=time.time) -> None:
        self.path = path if path is not None else memory_dir() / FILE_NAME
        self._protector = protector
        self.clock = clock
        self.data = self._load()

    @property
    def protector(self):
        if self._protector is None:
            self._protector = DpapiProtector()
        return self._protector

    # -- file -------------------------------------------------------------------------------------

    def _load(self) -> MemoryData:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return MemoryData()
        except OSError as exc:
            raise MemoryError_(f"Could not read {self.path}: {exc}") from exc
        try:
            body = json.loads(self.protector.unprotect(raw).decode("utf-8"))
            return MemoryData([Fact(**f) for f in body.get("facts", [])],
                              [Preference(**p) for p in body.get("preferences", [])],
                              int(body.get("next_id", 1)))
        except MemoryError_:
            raise
        except Exception as exc:  # noqa: BLE001 - a damaged file is reported, never half-used
            raise MemoryError_(f"The memory file {self.path} is damaged.") from exc

    def save(self) -> None:
        body = {"version": 1, "facts": [asdict(f) for f in self.data.facts],
                "preferences": [asdict(p) for p in self.data.preferences], "next_id": self.data.next_id}
        blob = self.protector.protect(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_bytes(blob)
        os.replace(temp, self.path)

    def _new_id(self, prefix: str) -> str:
        value = f"{prefix}{self.data.next_id}"
        self.data.next_id += 1
        return value

    # -- changes ----------------------------------------------------------------------------------

    def add_fact(self, text: str) -> tuple[Fact, Fact | None]:
        """Save a fact; returns (new, replaced or None). A duplicate or a fact about the same
        subject replaces the older one; past the cap the oldest fact goes."""
        text = " ".join(text.split()).strip()[:MAX_FACT_CHARS].rstrip(" .") + "."
        key, folded = fact_key(text), text.casefold()
        replaced = next((f for f in self.data.facts if f.text.casefold() == folded or (key and f.key == key)), None)
        if replaced is not None:
            self.data.facts.remove(replaced)
        fact = Fact(self._new_id("f"), text, self.clock(), key)
        self.data.facts.append(fact)
        del self.data.facts[:-MAX_FACTS]
        self.save()
        return fact, replaced

    def set_preference(self, kind: str, site: str, value: str) -> tuple[Preference, Preference | None]:
        site = normalize_site(site)
        replaced = next((p for p in self.data.preferences if p.kind == kind and p.site == site), None)
        if replaced is not None:
            self.data.preferences.remove(replaced)
        preference = Preference(self._new_id("p"), kind, site, value.strip(), self.clock())
        self.data.preferences.append(preference)
        self.save()
        return preference, replaced

    def entries(self) -> list:
        """Facts and preferences, oldest first."""
        return sorted([*self.data.facts, *self.data.preferences], key=lambda e: (e.created, e.id))

    def latest(self):
        entries = self.entries()
        return entries[-1] if entries else None

    def forget(self, entry_id: str):
        """Remove one entry by id; returns it or None."""
        for bucket in (self.data.facts, self.data.preferences):
            for entry in bucket:
                if entry.id == entry_id:
                    bucket.remove(entry)
                    self.save()
                    return entry
        return None

    def clear(self) -> int:
        count = len(self.data.facts) + len(self.data.preferences)
        self.data = MemoryData(next_id=self.data.next_id)
        self.save()
        return count

    # -- reading ----------------------------------------------------------------------------------

    def browser_for(self, site: str | None) -> str | None:
        """The remembered browser for this site ("youtube.com"), else the remembered default."""
        prefs = {p.site: p.value for p in self.data.preferences if p.kind == "browser"}
        if site:
            normal = normalize_site(site)
            for key, value in prefs.items():
                if key and (normal == key or normal.endswith("." + key)):
                    return value
        return prefs.get("")

    def relevant_facts(self, request: str, *, limit: int = 8, max_chars: int = 1600) -> list[Fact]:
        """Facts sharing a content word (or its start) with the request, best first."""
        words = _content_words(request)
        if not words:
            return []
        scored = []
        for fact in self.data.facts:
            shared = sum(1 for w in _content_words(fact.text) if any(_related(w, r) for r in words))
            if shared:
                scored.append((shared, fact.created, fact))
        chosen, used = [], 0
        for _shared, _created, fact in sorted(scored, key=lambda item: (-item[0], -item[1])):
            if len(chosen) >= limit or used + len(fact.text) > max_chars:
                break
            chosen.append(fact)
            used += len(fact.text)
        return chosen


_STOP = frozenset("""a an the and or but to of in on at for with from by is are was were be been am i me my mine you your
it its this that these those what which who whom how when where why do does did can could would should will shall
please aqua hey just now up out about there here have has had not no yes so if then than them they we our us
open start launch go get set make put tell show say know remember forget""".split())


def _content_words(text: str) -> set[str]:
    return {w.strip("._-") for w in _WORD.findall(text.casefold()) if len(w.strip("._-")) >= 3} - _STOP


def _related(a: str, b: str) -> bool:
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 4 and long_.startswith(short[:max(4, len(short) - 2)])
