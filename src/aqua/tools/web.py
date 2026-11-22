"""open_url and web_search, in the default browser. Only http(s) web addresses open."""

import ipaddress
import re
from urllib.parse import quote_plus, urlsplit

from aqua.agent.context import ToolContext
from aqua.agent.policy import deny
from aqua.agent.registry import SAFE, Tool, ToolOutcome

# DuckDuckGo fits a privacy-first assistant; the query goes only to the browser.
SEARCH_URL = "https://duckduckgo.com/?q={}"
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


def _check_url(args: dict, context: ToolContext):
    if normalize_url(args["url"]) is None:
        return deny("Only web addresses (http or https) can be opened, never files or local paths.")
    return None


def _open(args: dict, context: ToolContext) -> ToolOutcome:
    url = normalize_url(args["url"])
    try:
        context.desktop.open_url(url)
    except OSError:
        return ToolOutcome("failed", "The browser could not be opened.")
    host = urlsplit(url).hostname
    return ToolOutcome("ok", f"Opened {host} in the browser.", done=f"opened {host}", data={"url": url})


def _search(args: dict, context: ToolContext) -> ToolOutcome:
    query = " ".join(args["query"].split())
    try:
        context.desktop.open_url(SEARCH_URL.format(quote_plus(query)))
    except OSError:
        return ToolOutcome("failed", "The browser could not be opened.")
    return ToolOutcome("ok", f"Opened a web search for {query!r} in the browser.",
                       done=f"searched the web for {query}")


open_url = Tool(
    name="open_url", group="web", tier=SAFE,
    description="Open a website in the default browser, e.g. youtube.com.",
    parameters={"type": "object", "properties": {
        "url": {"type": "string", "description": "Web address", "minLength": 3, "maxLength": 2000}},
        "required": ["url"]},
    run=_open, check=_check_url)

web_search = Tool(
    name="web_search", group="web", tier=SAFE,
    description="Search the web in the default browser (shows results on screen).",
    parameters={"type": "object", "properties": {
        "query": {"type": "string", "description": "What to search for", "minLength": 1, "maxLength": 300}},
        "required": ["query"]},
    run=_search)
