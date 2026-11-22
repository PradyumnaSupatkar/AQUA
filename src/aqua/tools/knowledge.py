"""look_up: answer factual questions from English Wikipedia, not from model memory.

Only the model's short query leaves the PC, and only after the privacy guard
passes: no conversation, desktop data, typed text or user details are sent.
"""

from datetime import datetime
from http.client import HTTPException, HTTPSConnection
import json
import os
import re
import sys
from urllib.parse import quote, urlencode

from aqua import __version__
from aqua.agent.context import ToolContext
from aqua.agent.policy import deny
from aqua.agent.recency import claims_not_happened, mentions_recent
from aqua.agent.registry import READ, Tool, ToolOutcome

WIKI_HOST = "en.wikipedia.org"
API_PATH = "/w/api.php"
TIMEOUT_SECONDS = 8.0
MAX_RESPONSE_BYTES = 512 * 1024
MAX_QUERY_WORDS = 12
MAX_EXTRACT_CHARS = 1500
# Wikimedia asks clients to identify themselves with contact details. Aqua sends
# a generic client name; a user who wants to add contact details can set this.
CONTACT_ENV = "AQUA_WIKI_CONTACT"

_URL = re.compile(r"[a-z][a-z0-9+.-]*://|\bwww\.|\b[\w-]+\.(?:com|org|net|io|gov|edu|co|uk|in|de|dev|app|info|ai|me)"
                  r"\b(?:/|$)", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DRIVE = re.compile(r"(?:^|[\s\"'(])[a-z]:(?:[\\/]|$|\s)", re.IGNORECASE)
_PATH = re.compile(r"\\|(?:^|\s)[~.]?/[\w.-]+|\b[\w-]+\.(?:exe|dll|txt|docx?|pdf|py|json|ini|lnk|bat|cmd|ps1|xlsx?|csv|"
                   r"log|png|jpe?g|zip|msi)\b", re.IGNORECASE)
_UPCOMING = re.compile(r"\b(?:is\s+scheduled|was\s+scheduled|will\s+be\s+held|is\s+set\s+to|will\s+take\s+place|"
                       r"upcoming|is\s+expected\s+to|will\s+be\s+the)\b", re.IGNORECASE)
_SEGMENTS = re.compile(r"\s+[-–—|]\s+")
_FILLER = re.compile(r"\b(?:winners?|won|results?|standings|scores?|facts|info(?:rmation)?|details|wiki(?:pedia)?)\b",
                     re.IGNORECASE)


def user_agent() -> str:
    contact = " ".join(os.environ.get(CONTACT_ENV, "").split())[:120]
    detail = contact or "local voice assistant; Wikipedia look-ups only"
    return f"AquaVoiceAssistant/{__version__} ({detail}) Python/{sys.version_info.major}.{sys.version_info.minor}"


def privacy_problem(query: str, context: ToolContext) -> str | None:
    """Why a query must not leave the PC, or None. Checked before any request."""
    text = " ".join(query.split())
    folded = text.casefold()
    if len(text.split()) > MAX_QUERY_WORDS:
        return f"the query is longer than {MAX_QUERY_WORDS} words; use a short topic like 'Lewis Hamilton'"
    if _EMAIL.search(text):
        return "the query contains an e-mail address"
    if _URL.search(text):
        return "the query contains a web address"
    if _DRIVE.search(text) or _PATH.search(text):
        return "the query contains a file path or file name"
    for seen in context.observed:
        parts = {seen, *(_SEGMENTS.split(seen))}
        if any(len(part) >= 3 and part in folded for part in parts):
            return "the query contains a window title or app name seen on this PC"
    for typed in context.typed:
        typed = " ".join(typed.casefold().split())
        if len(typed) >= 3 and (typed in folded or (len(folded) >= 3 and folded in typed)):
            return "the query contains text the user asked to type"
    return None


def _check(args: dict, context: ToolContext):
    problem = privacy_problem(args["query"], context)
    if problem:
        return deny(f"Not sent to Wikipedia because {problem}. Answer without it, or ask the user.")
    return None


class WikipediaClient:
    """One HTTPS GET to en.wikipedia.org's API per look-up; no cookies, redirects or proxies."""

    def __init__(self, fetch=None) -> None:
        self._fetch = fetch if fetch is not None else self._get

    def _get(self, params: dict) -> dict:
        connection = HTTPSConnection(WIKI_HOST, timeout=TIMEOUT_SECONDS)
        try:
            connection.request("GET", API_PATH + "?" + urlencode(params), headers={
                "User-Agent": user_agent(), "Accept": "application/json", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if response.status != 200 or len(body) > MAX_RESPONSE_BYTES:
                raise OSError(f"Wikipedia answered with HTTP {response.status}.")
            return json.loads(body.decode("utf-8"))
        except (HTTPException, ValueError) as exc:
            raise OSError("Wikipedia returned an unreadable answer.") from exc
        finally:
            connection.close()

    def lookup(self, query: str) -> dict | None:
        """The best article for the query, as its first ~12 sentences (a short intro
        alone often lacks facts such as a race winner).

        Of the top three search hits, a title whose words all appear in the query
        wins ("2025 Las Vegas Grand Prix" for "2025 Las Vegas Grand Prix winner"),
        then search rank. Disambiguation pages are skipped.
        """
        # An article named like the query, minus words such as "winner", comes first:
        # search ranks "Las Vegas Grand Prix" above "2025 Las Vegas Grand Prix" for
        # "2025 Las Vegas Grand Prix winner".
        topic = " ".join(_FILLER.sub(" ", query).split())
        if len(topic.split()) >= 2 and topic.casefold() != query.casefold():
            page = self._article(topic)
            if page is not None:
                return page
        found = self._fetch({"action": "query", "format": "json", "formatversion": "2", "list": "search",
                             "srsearch": query, "srlimit": "3", "srnamespace": "0", "srprop": ""})
        hits = found.get("query", {}).get("search", []) if isinstance(found, dict) else []
        titles = [hit["title"] for hit in hits if isinstance(hit, dict) and isinstance(hit.get("title"), str)]
        for title in sorted(titles, key=lambda t: (-title_match(t, query), titles.index(t))):
            page = self._article(title)
            if page is not None:
                return page
        return None

    def _article(self, title: str) -> dict | None:
        return self._best(self._fetch({
            "action": "query", "format": "json", "formatversion": "2", "redirects": "1", "titles": title,
            "prop": "extracts|info|revisions", "explaintext": "1", "exsentences": "12", "inprop": "url",
            "rvprop": "timestamp"}))

    @staticmethod
    def _best(data) -> dict | None:
        pages = data.get("query", {}).get("pages", []) if isinstance(data, dict) else []
        pages = sorted((p for p in pages if isinstance(p, dict)), key=lambda p: p.get("index", 99))
        for page in pages:
            extract = page.get("extract")
            if not isinstance(extract, str) or not extract.strip() or "may refer to:" in extract[:300]:
                continue  # Skip empty pages and disambiguation lists.
            title = str(page.get("title", ""))
            revisions = page.get("revisions") or [{}]
            return {"title": title, "extract": extract,
                    "url": page.get("fullurl") or f"https://{WIKI_HOST}/wiki/{quote(title.replace(' ', '_'))}",
                    "last_edited": str(revisions[0].get("timestamp", ""))[:10]}
        return None


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def title_match(title: str, query: str) -> int:
    """Words in the title when every one of them is in the query, else 0."""
    words = _words(title)
    return len(words) if words and words <= _words(query) else 0


def shorten(text: str, limit: int = MAX_EXTRACT_CHARS) -> str:
    text = re.sub(r"={2,}[^=\n]{1,80}={2,}", " ", text)  # Drop "== Background ==" section headings.
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "..."


def may_be_out_of_date(question: str, extract: str, now: datetime) -> bool:
    """Very recent events: the request names this year (or later), uses words like
    "latest", or the article still calls the event upcoming."""
    return mentions_recent(question, now) or bool(_UPCOMING.search(extract))


def make_look_up(client: WikipediaClient | None = None, *, now=None) -> Tool:
    client = client if client is not None else WikipediaClient()
    clock = now if now is not None else (lambda: datetime.now().astimezone())

    def run(args: dict, context: ToolContext) -> ToolOutcome:
        query = " ".join(args["query"].split())
        try:
            page = client.lookup(query)
        except OSError:
            return ToolOutcome("failed", "Wikipedia could not be reached. Say so; do not answer from memory.")
        if page is None:
            return ToolOutcome("empty", "Wikipedia has no article for that. Say you could not find it; do not guess.")
        extract = shorten(page["extract"])
        message = "Answer only from this Wikipedia extract, starting with 'According to Wikipedia'."
        upcoming = bool(_UPCOMING.search(extract))
        if upcoming:
            message += (" The article describes this as upcoming or unfinished, so it has no final result: "
                        "say Wikipedia does not have the result yet.")
        elif may_be_out_of_date(f"{context.request} {query}", extract, clock()):
            message += " This is recent, so add that the article may be out of date."
        return ToolOutcome("ok", message, data={"source": "Wikipedia", "title": page["title"], "url": page["url"],
                                                "last_edited": page["last_edited"], "extract": extract,
                                                **({"upcoming": True} if upcoming else {}),
                                                **({"asks_result": True} if _RESULT_WORDS.search(
                                                    f"{context.request} {query}") else {})})

    return Tool(
        name="look_up", group="knowledge", tier=READ,
        description=("Look up a fact on Wikipedia (people, places, events, sports, records, numbers). "
                     "Use a short topic query, e.g. 'Lewis Hamilton'. Answer from the result, not from memory."),
        parameters={"type": "object", "properties": {
            "query": {"type": "string", "description": "Short topic, 2-6 words", "minLength": 2, "maxLength": 120}},
            "required": ["query"]},
        run=run, check=_check, review=review_reply)


_RESULT_WORDS = re.compile(r"\b(?:won|wins|winner|winning|victory|victorious|champions?|beat|defeated|took\s+the\s+title)\b",
                           re.IGNORECASE)


def review_reply(reply: str, outcome: ToolOutcome) -> str | None:
    """Host check of the spoken answer against the article: a result the article
    does not contain (e.g. a winner of a race it calls upcoming) is not spoken."""
    data = outcome.data
    if outcome.status != "ok" or not isinstance(data.get("extract"), str):
        return None
    if not data.get("upcoming"):
        return None
    unsupported = _RESULT_WORDS.search(reply) and not _RESULT_WORDS.search(data["extract"])
    if data.get("asks_result") or unsupported or claims_not_happened(reply):
        # The article still calls it upcoming: it may simply not be updated yet.
        return (f"According to Wikipedia, the {data['title']} has no result yet; the article may be out of date, "
                "so I don't want to guess.")
    return None
