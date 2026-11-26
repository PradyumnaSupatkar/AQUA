"""Two narrow fixes to a voice transcript before it reaches the engine.

1. "write" homophones. No recogniser can tell "write" from "right", "ride", "bright", "rite" or
   "wright" (benchmarks/asr-v1: every model heard "Right, hello I am Aqua"). The first word becomes
   "write" only when the rest of the utterance names a typing destination ("... into notepad",
   "... in the notepad window", "... to my notes") or Aqua is waiting for the answer to "Which app
   should I type that into?". Nothing else in the utterance is changed.
2. Spoken numbers. Parakeet writes numbers as words and can garble them ("ten ADP" for 1080p), so
   numbers that are tool arguments are turned into digits: units ("ten seconds" -> "10 seconds"),
   levels ("set the volume to thirty" -> "to 30"), times ("jump to two thirty" -> "2:30",
   "two minutes thirty" -> "2:30"), resolutions ("ten eighty p" -> "1080p") and speeds
   ("one point five x" -> "1.5x"). Ordinals and other words ("the second one") are left alone.

Each fix returns a note, shown with --diagnostics. Typed chat gets fix 2 only (``prepare_typed``).
"""

import re

# -- 1. write -------------------------------------------------------------------------------------

_HOMOPHONE = re.compile(r"^(?P<lead>\W*)(?P<word>right|ride|bright|rite|wright|writes?)\b[,.:;!]?\s+(?P<rest>\S.*)$",
                        re.I | re.S)
# Apps people dictate into. Others count when the session's app index knows them (``is_app``).
TYPING_TARGETS = frozenset({
    "notepad", "note pad", "notepad++", "wordpad", "word", "microsoft word", "vs code", "visual studio code", "code",
    "onenote", "one note", "sticky notes", "notes", "my notes", "google docs", "docs", "discord", "slack", "teams",
    "microsoft teams", "whatsapp", "telegram", "outlook", "email", "gmail", "the document", "document",
})
_DESTINATION = re.compile(
    r"\b(?:into|in|to|onto|inside)\s+(?:the\s+|my\s+|a\s+|our\s+)?(?:new\s+|open\s+)?"
    r"(?P<app>[\w+.'\- ]{1,40}?)(?:\s+(?:window|app|application|file|document|tab|editor))?\s*[.!?]*$", re.I)


def names_destination(rest: str, is_app=None) -> str | None:
    """The app the utterance types into ("... into the notepad window" -> "notepad"), if any."""
    rest = re.sub(r"(?:[\s,]+(?:please|thanks|thank\s+you|now|for\s+me))+\W*$", "", rest, flags=re.I)
    for match in _DESTINATION.finditer(rest):
        app = " ".join(match.group("app").casefold().split())
        if app in TYPING_TARGETS or (is_app is not None and app and is_app(app)):
            return app
    return None


def fix_write(text: str, *, awaiting_app: bool = False, is_app=None) -> tuple[str, str | None]:
    match = _HOMOPHONE.match(text)
    if match is None or match.group("word").casefold().startswith("write"):
        return text, None
    destination = names_destination(match.group("rest"), is_app)
    if not destination and not awaiting_app:
        return text, None
    word = match.group("word")
    write = "Write" if word[:1].isupper() else "write"
    reason = f"typing destination: {destination}" if destination else "answering 'Which app?'"
    return f"{match.group('lead')}{write} {match.group('rest')}", f"'{word}' -> 'write' ({reason})"


# -- 2. numbers -----------------------------------------------------------------------------------

_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if i > 1}
_UNIT = "|".join(sorted(_UNITS, key=len, reverse=True))
_TEN = "|".join(_TENS)
_SMALL = rf"(?:(?:{_TEN})(?:[\s-](?:{_UNIT}))?|{_UNIT})"          # 0-99 in words
NUMBER = rf"(?:{_SMALL}(?:\s+hundred(?:\s+(?:and\s+)?{_SMALL})?)?)"  # 0-999 in words


def words_to_int(words: str) -> int | None:
    total, current = 0, 0
    for word in re.split(r"[\s-]+", words.casefold().strip()):
        if word == "and":
            continue
        if word in _UNITS:
            current += _UNITS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        else:
            return None
    return total + current


_MINUTES = rf"(?:{_SMALL}|oh\s+(?:{_UNIT}))"
_RULES = [
    # Resolutions: "ten eighty p" -> 1080p; Parakeet's "ten ADP" / "ten Arep" when quality is mentioned.
    (re.compile(r"\b(?:ten\s+eighty|ten-eighty)\s*p\b", re.I), lambda m: "1080p"),
    (re.compile(r"\bseven\s+twenty\s*p\b", re.I), lambda m: "720p"),
    (re.compile(r"\bfour\s+eighty\s*p\b", re.I), lambda m: "480p"),
    (re.compile(r"\bthree\s+sixty\s*p\b", re.I), lambda m: "360p"),
    (re.compile(r"\bfourteen\s+forty\s*p\b", re.I), lambda m: "1440p"),
    (re.compile(r"\btwenty\s+one\s+sixty\s*p\b", re.I), lambda m: "2160p"),
    (re.compile(r"\bfour\s*k\b", re.I), lambda m: "4K"),
    (re.compile(r"(?<=\bquality to )ten\s+(?:a\w*p|are\s*p|a\s*d\s*p|e\w*p)\b", re.I), lambda m: "1080p"),
    # Speeds: "one point five x" -> 1.5x, "one and a half times" -> 1.5x, "two x" -> 2x.
    (re.compile(rf"\b({_UNIT})\s+point\s+({_UNIT}|twenty[\s-]five|seventy[\s-]five)\s*(?:x|times)\b", re.I),
     lambda m: f"{words_to_int(m.group(1))}.{str(words_to_int(m.group(2))).rstrip('0') or '0'}x"),
    (re.compile(rf"\b({_UNIT})\s+and\s+a\s+half\s+(?:x|times)\b", re.I), lambda m: f"{words_to_int(m.group(1))}.5x"),
    (re.compile(rf"\b({_UNIT})\s*(?:x)\s+(?=speed\b)|\b({_UNIT})x\b", re.I),
     lambda m: f"{words_to_int(m.group(1) or m.group(2))}x "),
    # Times: "two minutes thirty (seconds)" -> 2:30.
    (re.compile(rf"\b({_SMALL})\s+minutes?\s+(?:and\s+)?({_SMALL})(?:\s+seconds?)?\b", re.I),
     lambda m: f"{words_to_int(m.group(1))}:{words_to_int(m.group(2)):02d}"),
    # "at / to / until two thirty" -> 2:30 (hours 1-12, minutes oh-five .. fifty-nine).
    (re.compile(rf"\b(at|to|until|till|by)\s+(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
                rf"({_MINUTES})(?=\s*(?:[.,!?]|$|\b(?:in|on|of|o'?clock|am|pm|a\.m\.|p\.m\.|tonight|today|"
                rf"tomorrow)\b))", re.I),
     lambda m: f"{m.group(1)} {words_to_int(m.group(2))}:"
               f"{words_to_int(re.sub(r'^oh\s+', '', m.group(3), flags=re.I)):02d}"),
    # Units: "ten seconds" -> 10 seconds, "fifty percent" -> 50 percent.
    (re.compile(rf"\b({NUMBER})(?=\s+(?:seconds?|secs?|minutes?|mins?|hours?|percent|per\s+cent|%|times|db|"
                rf"decibels?)\b)", re.I), lambda m: str(words_to_int(m.group(1)))),
    # Levels: "set the volume to thirty" -> "to 30" (volume, brightness, speed or level named first).
    (re.compile(rf"(\b(?:volume|brightness|level|sound)\b[^.?!]{{0,30}}?\b(?:to|at|by)\s+)({NUMBER})\b", re.I),
     lambda m: m.group(1) + str(words_to_int(m.group(2)))),
]


def spoken_numbers(text: str) -> tuple[str, str | None]:
    changed = text
    for pattern, replace in _RULES:
        changed = pattern.sub(replace, changed)
    changed = re.sub(r"\s{2,}", " ", changed).replace(" .", ".").strip()
    return (changed, f"numbers: '{text}' -> '{changed}'") if changed != text.strip() else (text, None)


# -- 0. leading fillers ---------------------------------------------------------------------------

# Parakeet often starts with "Well ..." (heard in the end of the wake phrase or a breath). These words
# are dropped from the start when more words follow; a bare "okay" stays (it can be a yes).
_FILLER = re.compile(r"^\W*(?:well|so|um+|uh+|erm?|oh|okay|ok|hey)\b[\s,.!?;:-]*(?=\S)", re.I)


def strip_fillers(text: str) -> tuple[str, str | None]:
    stripped = text
    while (match := _FILLER.match(stripped)) and re.search(r"\w", stripped[match.end():]):
        stripped = stripped[match.end():]
    if stripped == text:
        return text, None
    removed = text[:len(text) - len(stripped)].strip(" ,.")
    return stripped, f"dropped leading filler '{removed}'"


def prepare(text: str, *, awaiting_app: bool = False, is_app=None) -> tuple[str, list[str]]:
    """The transcript the engine should see, and notes for --diagnostics. Fillers go first, so
    "Well Right meeting at noon into notepad" still gets the write fix."""
    notes = []
    text, note = strip_fillers(text)
    if note:
        notes.append(note)
    text, note = fix_write(text, awaiting_app=awaiting_app, is_app=is_app)
    if note:
        notes.append(note)
    text, note = spoken_numbers(text)
    if note:
        notes.append(note)
    return text, notes


# Typed text that is dictation ("type ...", "write ..."): the user's own words, kept as typed.
_DICTATION = re.compile(r"^\W*(?:please\s+)?(?:type|write|dictate|enter)\b", re.I)


def prepare_typed(text: str) -> tuple[str, list[str]]:
    """Typed chat input as the engine should see it: the same spoken-number fix as voice ("jump to
    one minute thirty" -> "jump to 1:30"), so typed and spoken requests reach the tools alike. No
    filler or homophone fixes (a keyboard does not mishear), and typed dictation is left as typed."""
    if _DICTATION.match(text):
        return text, []
    text, note = spoken_numbers(text)
    return text, [note] if note else []
