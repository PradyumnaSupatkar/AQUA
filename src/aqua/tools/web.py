"""open_url and web_search, in the default browser. Only http(s) web addresses open."""

import ipaddress
import re
from urllib.parse import quote_plus, urlsplit

from aqua.agent.context import ToolContext
from aqua.agent.policy import deny
from aqua.agent.registry import SAFE, Tool, ToolOutcome

# DuckDuckGo fits a privacy-first assistant; the query goes only to the browser.
SEARCH_URL = "https://duckduckgo.com/?q={}"
# Site searches: fixed https templates; only the URL-encoded query is filled in.
SITE_SEARCH = {
    "youtube": "https://www.youtube.com/results?search_query={}",
    "google": "https://www.google.com/search?q={}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={}",
    "amazon": "https://www.amazon.com/s?k={}",
    "github": "https://github.com/search?q={}",
}
_HOST = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.IGNORECASE)


def normalize_url(value: str) -> str | None:
    """An https?:// URL to open, or None. Never file:, UNC, drive or other local paths."""
    text = value.strip()
    if not text or any(c.isspace() or ord(c) < 32 for c in text) or "\\" in text:
        return None
    if re.match(r"^[a-z]:", text, re.IGNORECASE) or text.startswith(("/", "~", ".")):
        return None  # A drive letter or a local path.
    if "://" not in text:
        if ":" in text.split("/", 1)[0] and not re.match(r"^[^:/]+:\d+(?:/|$)", text):
            return None  # Another scheme, e.g. javascript: or mailto:.
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or parts.username or parts.password or not parts.hostname:
        return None
    host = parts.hostname.rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None and host != "localhost" and not _HOST.match(host):
        return None
    if port is not None and not 0 < port < 65536:
        return None
    return parts.geturl()


BROWSER_PARAMETER = {"type": "string", "description": "Browser to use if the user named one, e.g. Chrome; "
                     "omit for the default browser", "maxLength": 60}


def _browser(args: dict, context: ToolContext):
    """(installed browser entry or None, note for the reply)."""
    name = args.get("browser")
    if not name:
        return None, ""
    match = context.apps.find(name)
    if match.entry is None:
        return None, f" {name} was not found, so the default browser was used."
    return match.entry, ""


def _check_browser(args: dict, context: ToolContext):
    entry, _note = _browser(args, context)
    if entry is not None and context.policy.blocked(name=entry.name, executable=entry.executable or entry.target):
        return deny(f"{entry.name} is blocked by Aqua's safety settings.")
    return None


def _check_url(args: dict, context: ToolContext):
    if normalize_url(args["url"]) is None:
        return deny("Only web addresses (http or https) can be opened, never files or local paths.")
    return _check_browser(args, context)


def _open_in(url: str, args: dict, context: ToolContext):
    entry, note = _browser(args, context)
    try:
        context.desktop.open_url(url, entry)
    except OSError:
        return None, note
    return (entry.name if entry is not None else "the browser"), note


def _open(args: dict, context: ToolContext) -> ToolOutcome:
    url = normalize_url(args["url"])
    where, note = _open_in(url, args, context)
    if where is None:
        return ToolOutcome("failed", "The browser could not be opened.")
    host = urlsplit(url).hostname
    return ToolOutcome("ok", f"Opened {host} in {where}.{note}", done=f"opened {host}", data={"url": url})


def search_url(query: str, site: str | None = None) -> str:
    return SITE_SEARCH.get(site or "", SEARCH_URL).format(quote_plus(" ".join(query.split())))


def _search(args: dict, context: ToolContext) -> ToolOutcome:
    query, site = " ".join(args["query"].split()), args.get("site")
    where, note = _open_in(search_url(query, site), args, context)
    if where is None:
        return ToolOutcome("failed", "The browser could not be opened.")
    on = f" on {site.capitalize() if site != 'youtube' else 'YouTube'}" if site else ""
    return ToolOutcome("ok", f"Opened a search for {query!r}{on} in {where}.{note}",
                       done=f"searched{on or ' the web'} for {query}")


open_url = Tool(
    name="open_url", group="web", tier=SAFE,
    description="Open a website, e.g. youtube.com, in the default browser or the one the user named.",
    parameters={"type": "object", "properties": {
        "url": {"type": "string", "description": "Web address", "minLength": 3, "maxLength": 2000},
        "browser": BROWSER_PARAMETER},
        "required": ["url"]},
    run=_open, check=_check_url, describe=lambda args: f"open {args['url']}")

web_search = Tool(
    name="web_search", group="web", tier=SAFE,
    description=("Search the web and show the results, in the default browser or the one the user named "
                 "(e.g. 'search for X in Chrome'). Searching never means typing into a browser window. "
                 "For spoken facts use look_up."),
    parameters={"type": "object", "properties": {
        "query": {"type": "string", "description": "What to search for", "minLength": 1, "maxLength": 300},
        "site": {"type": "string", "enum": list(SITE_SEARCH),
                 "description": "Search on this site, e.g. youtube for 'search X on YouTube'; omit for the web"},
        "browser": BROWSER_PARAMETER},
        "required": ["query"]},
    run=_search, check=_check_browser,
    describe=lambda args: f"search {args['site']} for {args['query']}" if args.get("site")
    else f"search the web for {args['query']}")
