"""remember, forget and recall: Aqua's local memory (aqua.memory.store).

remember is tier 1 and automatic: the model calls it when the user states a lasting preference or
fact, and the reply always says what was saved. Host rules (aqua.memory.safety) decide whether it
may be saved: only the user's own words in this request, never secrets, sensitive categories or
dictated text. Browser preferences are read from the user's words by the host ("always open
YouTube in Brave and Chrome for anything else" -> youtube.com: Brave, default: Chrome).
forget is tier 1 for one memory and tier 2 (the host asks) for everything. recall only reads.
"""

import re

from aqua.agent.context import ToolContext
from aqua.agent.registry import DISRUPTIVE, READ, SAFE, Tool, ToolOutcome
from aqua.memory.safety import refusal

OFF = "Memory is turned off for this session (--no-memory), so nothing was saved."
_BROWSERS = ("google chrome", "microsoft edge", "mozilla firefox", "brave", "chrome", "edge", "firefox", "opera",
             "vivaldi")
_BROWSER_NAMES = {"google chrome": "Chrome", "microsoft edge": "Edge", "mozilla firefox": "Firefox"}
_BROWSER = "|".join(re.escape(b) for b in _BROWSERS)
_SITE = r"(?:you\s?tube|google|gmail|github|wikipedia|amazon|netflix|reddit|twitter|x|facebook|instagram|linkedin|" \
        r"spotify|twitch|[a-z0-9-]+\.(?:com|org|net|io|dev|in|co\.uk|tv|ai))"
# "open YouTube in Brave", "YouTube in Brave", "use Brave for YouTube", "Brave for YouTube".
_SITE_IN = re.compile(rf"\b(?P<site>{_SITE})(?:\s+(?:videos?|links?|stuff))?\s+(?:in|with|on|using)\s+"
                      rf"(?:the\s+)?(?P<browser>{_BROWSER})\b", re.I)
_BROWSER_FOR = re.compile(rf"\b(?P<browser>{_BROWSER})\s+(?:browser\s+)?(?:for|when\s+(?:i|you)\s+open)\s+"
                          rf"(?P<site>{_SITE})\b", re.I)
# "Chrome for anything else", "Chrome for everything else", "otherwise Chrome", "Chrome as the default".
_DEFAULT = re.compile(
    rf"\b(?P<browser>{_BROWSER})\s+(?:browser\s+)?(?:for\s+(?:anything|everything|all)(?:\s+else)?|for\s+(?:the\s+)?"
    rf"(?:rest|others?|other\s+(?:sites|websites|things|links))|for\s+(?:websites|web\s+pages|links|browsing)|"
    rf"as\s+(?:the|my)\s+(?:default|usual)|by\s+default|otherwise)\b|"
    rf"\b(?:otherwise|else|everything\s+else|anything\s+else|other\s+(?:sites|websites))\s+(?:in|with|use)\s+"
    rf"(?P<browser2>{_BROWSER})\b", re.I)


def _browser_name(text: str) -> str:
    folded = " ".join(text.casefold().split())
    return _BROWSER_NAMES.get(folded, folded.title())


def browser_preferences(request: str) -> list[tuple[str, str]]:
    """(site or "" for the default, browser) pairs the user's own words state."""
    found: dict[str, str] = {}
    for pattern in (_SITE_IN, _BROWSER_FOR):
        for match in pattern.finditer(request):
            found.setdefault(re.sub(r"\s", "", match.group("site").casefold()), _browser_name(match.group("browser")))
    for match in _DEFAULT.finditer(request):
        found.setdefault("", _browser_name(match.group("browser") or match.group("browser2")))
    return list(found.items())


_PRONOUNS = ((r"\bthe user's\b", "your"), (r"\bthe user is\b", "you are"), (r"\bthe user\b", "you"),
             (r"\bi am\b", "you are"), (r"\bi'm\b", "you're"), (r"\bi've\b", "you've"), (r"\bmy\b", "your"),
             (r"\bmine\b", "yours"), (r"\bme\b", "you"), (r"\bi\b", "you"), (r"\bmyself\b", "yourself"))


def spoken(fact: str) -> str:
    """"My projects are in D:\\Projects." -> "your projects are in D:\\Projects" (for the reply)."""
    text = fact.strip().rstrip(".")
    for pattern, replacement in _PRONOUNS:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return text[:1].lower() + text[1:] if not text[:2].isupper() else text


def _site_label(site: str) -> str:
    names = {"youtube.com": "YouTube", "github.com": "GitHub", "google.com": "Google", "gmail.com": "Gmail"}
    return names.get(site, site)


def _store(context):
    return getattr(context, "memory", None)


def _remember(args: dict, context: ToolContext) -> ToolOutcome:
    store = _store(context)
    if store is None:
        return ToolOutcome("failed", OFF)
    request = getattr(context, "request", "") or ""
    dictated = tuple(getattr(context, "typed", ()))  # Text type_text was asked to type this session.
    if args.get("kind") == "browser":
        pairs = browser_preferences(request)
        if not pairs:
            return ToolOutcome("failed", "The user's words name no site-and-browser preference, so nothing was saved.")
        phrases = []
        for site, browser in pairs:
            why = refusal(f"{site.removesuffix('.com')} {browser}", request, dictated)
            if why:
                return ToolOutcome("failed", why)
            preference, _old = store.set_preference("browser", site, browser)
            phrases.append(f"open {_site_label(preference.site)} in {browser}" if preference.site
                           else f"use {browser} for other websites")
        done = "will " + " and ".join(phrases) + " from now on"
        return ToolOutcome("ok", "Saved: " + "; ".join(phrases) + ".", done=done,
                           data={"memory_saved": done, "steps": len(pairs)})
    text = " ".join(str(args.get("text", "")).split())
    if not text:
        return ToolOutcome("failed", "remember needs text (a short sentence about the user).")
    why = refusal(text, request, dictated)
    if why:
        return ToolOutcome("failed", "Not saved: " + why)
    fact, replaced = store.add_fact(text)
    context.observe(fact.text)  # Personal: look_up never sends it to Wikipedia.
    done = f"remembered that {spoken(fact.text)}"
    message = f"Saved fact {fact.id}: {fact.text}" + (f" (replaces: {replaced.text})" if replaced else "")
    return ToolOutcome("ok", message, done=done, data={"memory_saved": done})


def _forget_tier(args: dict, _context) -> int:
    return DISRUPTIVE if str(args.get("target", "")).strip().casefold() in {"all", "everything"} else SAFE


def _match(store, target: str):
    target = target.strip().casefold()
    if target in {"", "last", "that", "it", "latest", "most recent"}:
        return store.latest()
    for entry in store.entries():
        if entry.id == target:
            return entry
    words = [w for w in re.findall(r"[a-z0-9]+", target) if len(w) >= 3]
    for entry in reversed(store.entries()):
        if words and all(w in entry.text.casefold() for w in words):
            return entry
    return None


def _forget(args: dict, context: ToolContext) -> ToolOutcome:
    store = _store(context)
    if store is None:
        return ToolOutcome("failed", OFF)
    target = str(args.get("target", "last"))
    if _forget_tier(args, context) == DISRUPTIVE:
        count = store.clear()
        return ToolOutcome("ok", f"Forgot all {count} memories.", done="forgot everything I remembered")
    entry = _match(store, target)
    if entry is None:
        return ToolOutcome("failed", "No remembered item matches that.")
    store.forget(entry.id)
    what = spoken(entry.text) if entry.id.startswith("f") else entry.text
    return ToolOutcome("ok", f"Forgot {entry.id}: {entry.text}", done=f"forgot that {what}".replace(
        "forgot that open", "forgot to open").replace("forgot that use", "forgot to use"))


def listing(store, query: str = "") -> str:
    entries = store.entries()
    if query:
        words = [w for w in re.findall(r"[a-z0-9]+", query.casefold()) if len(w) >= 3]
        picked = [e for e in entries if any(w in e.text.casefold() for w in words)]
        entries = picked or entries
    if not entries:
        return "I don't remember anything about you yet."
    parts = [spoken(e.text) if e.id.startswith("f") else e.text for e in entries[:12]]
    more = f" and {len(entries) - 12} more" if len(entries) > 12 else ""
    return "I remember that " + "; ".join(parts) + more + "."


def _recall(args: dict, context: ToolContext) -> ToolOutcome:
    store = _store(context)
    if store is None:
        return ToolOutcome("ok", "Memory is turned off for this session.", data={"memories": []})
    text = listing(store, str(args.get("query", "")))
    return ToolOutcome("ok", text, data={"memories": [{"id": e.id, "text": e.text} for e in store.entries()],
                                         "answer": text})


def _recall_review(reply: str, outcome: ToolOutcome) -> str | None:
    """The reply must list what is remembered; a model summary that drops items gets the host's list."""
    answer = outcome.data.get("answer") if isinstance(outcome.data, dict) else None
    if not answer:
        return None
    key_words = [w for w in re.findall(r"[a-z0-9:\\]+", answer.casefold()) if len(w) >= 5]
    folded = reply.casefold()
    return None if key_words and all(w in folded for w in key_words) else answer


remember = Tool(
    name="remember", group="memory", tier=lambda args, context: SAFE,
    description=("Save something lasting the user just said about themselves or how they want things done, e.g. "
                 "'my projects are in D:\\Projects' (kind fact, text: a short sentence) or 'always open YouTube in "
                 "Brave and Chrome for anything else' (kind browser). Call it whenever the user states a lasting "
                 "preference or fact ('from now on', 'always', 'my X is Y'). Never for passwords or private details."),
    parameters={"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["fact", "browser"]},
        "text": {"type": "string", "maxLength": 300, "description": "fact: one short sentence, in the user's words"},
        "site": {"type": "string", "maxLength": 60, "description": "browser: the site, e.g. youtube.com"},
        "browser": {"type": "string", "maxLength": 40, "description": "browser: e.g. Brave"}},
        "required": ["kind"]},
    run=_remember, describe=lambda args: "remember that")

forget = Tool(
    name="forget", group="memory", tier=_forget_tier,
    description=("Forget something Aqua remembered: target 'last' (the most recent, for 'forget that'), an id, a few "
                 "words of it, or 'all' (Aqua asks first)."),
    parameters={"type": "object", "properties": {
        "target": {"type": "string", "maxLength": 100, "description": "last, all, an id, or words of the memory"}},
        "required": ["target"]},
    run=_forget,
    confirm=lambda args: "Forget everything I remember about you? Say yes or no.",
    describe=lambda args: "forget everything I remember" if _forget_tier(args, None) == DISRUPTIVE else "forget that")

recall = Tool(
    name="recall", group="memory", tier=READ,
    description="List what Aqua remembers about the user (optionally only what matches query).",
    parameters={"type": "object", "properties": {
        "query": {"type": "string", "maxLength": 100, "description": "optional words to match"}}},
    run=_recall, review=_recall_review)
