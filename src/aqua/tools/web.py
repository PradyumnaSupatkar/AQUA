"""open_url and web_search, in the default browser. Only http(s) web addresses open."""

import ipaddress
import os
import re
from pathlib import Path
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


def preferred_browser_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Aqua" / "preferred-browser.txt"


def read_preferred_browser(path: Path | None = None) -> str | None:
    """The optional preferred-browser setting: AQUA_BROWSER, else the first line of
    %LOCALAPPDATA%\\Aqua\\preferred-browser.txt (e.g. "Brave"). None means the Windows default."""
    value = os.environ.get("AQUA_BROWSER", "").strip()
    if value:
        return value[:60]
    try:
        lines = (preferred_browser_path() if path is None else path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    return next((line.strip()[:60] for line in lines if line.strip() and not line.lstrip().startswith("#")), None)


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
# Spoken browser names, longest first so "google chrome" wins over "chrome".
BROWSERS = ("google chrome", "microsoft edge", "mozilla firefox", "brave browser", "opera gx", "tor browser",
            "chrome", "brave", "firefox", "edge", "opera", "vivaldi")
SITE_NAMES = {"youtube": "YouTube", "google": "Google", "wikipedia": "Wikipedia", "amazon": "Amazon",
              "github": "GitHub", "duckduckgo": "DuckDuckGo"}
_SITE = r"(you\s?tube|google(?!\s+chrome)|wikipedia|amazon|git\s?hub)"

# Websites people open by name ("open youtube"): name -> address.
KNOWN_SITES = {
    "youtube": "youtube.com", "you tube": "youtube.com", "github": "github.com", "git hub": "github.com",
    "gmail": "mail.google.com", "google": "google.com", "google drive": "drive.google.com",
    "google maps": "maps.google.com", "reddit": "reddit.com", "netflix": "netflix.com", "wikipedia": "wikipedia.org",
    "amazon": "amazon.com", "twitter": "x.com", "x": "x.com", "facebook": "facebook.com", "instagram": "instagram.com",
    "linkedin": "linkedin.com", "twitch": "twitch.tv", "stack overflow": "stackoverflow.com",
    "stackoverflow": "stackoverflow.com", "chatgpt": "chatgpt.com", "outlook": "outlook.live.com",
    "duckduckgo": "duckduckgo.com", "prime video": "primevideo.com", "hotstar": "hotstar.com",
    "spotify": "open.spotify.com", "whatsapp": "web.whatsapp.com", "discord": "discord.com",
}
_DOMAIN_NAME = re.compile(r"^(?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,24}(?:/\S*)?$", re.I)
# A whole request that only opens a site: "open youtube", "go to github.com", "open youtube in edge".
_OPEN_SITE = re.compile(
    r"^\W*(?:(?:hey\s+)?aqua\W+)?(?:please\s+|can\s+you\s+|could\s+you\s+)*(?:open(?:\s+up)?|go\s+to|visit|load|"
    r"bring\s+up|take\s+me\s+to|navigate\s+to|launch|pull\s+up)\s+(?:the\s+)?(?P<site>[\w.:/-]+(?:\s+[\w.-]+)?)"
    r"(?:\s+(?:website|site|web\s?site|page|homepage|home\s+page))?(?:\s+(?:in|on|with|using)\s+(?:the\s+)?"
    rf"(?P<browser>(?:{'|'.join(map(re.escape, ('google chrome', 'microsoft edge', 'mozilla firefox', 'brave browser', 'brave', 'chrome', 'firefox', 'edge', 'opera', 'vivaldi')))}))"
    r"(?:\s+browser)?)?(?:\s+(?:please|for\s+me|now))?\W*$", re.I)


def site_url(name: str) -> str | None:
    """The address a spoken site name or a domain means ("youtube" -> youtube.com), or None."""
    text = " ".join(str(name).casefold().strip(" .!?").split())
    text = re.sub(r"\s+dot\s+com$", ".com", text)
    if text in KNOWN_SITES:
        return KNOWN_SITES[text]
    return text if _DOMAIN_NAME.match(text) and not text.endswith((".exe", ".lnk", ".txt")) else None


def open_site_request(text: str) -> tuple[str, str] | None:
    """(site name as said, address) when the whole request just opens a website, else None."""
    match = _OPEN_SITE.match(text.replace("’", "'").strip())
    if not match:
        return None
    site = re.sub(r"\s+(?:website|site|web\s?site|page|homepage|please|now)$", "", match.group("site").strip(),
                  flags=re.I)
    url = site_url(site)
    return (site, url) if url else None
# A site the request searches on: "on YouTube", "search YouTube for", "open YouTube (and search)".
_SITE_PLACE = (re.compile(rf"\b(?:on|in|at|using|via|with)\s+(?:the\s+)?{_SITE}\b", re.I),
               re.compile(rf"\bsearch\s+{_SITE}\s+for\b", re.I),
               re.compile(rf"\b(?:open|go\s+to|launch|start|bring\s+up)\s+(?:the\s+)?{_SITE}(?:\.com)?\b", re.I))
_SEARCH_VERB = r"(?:search(?:\s+for)?|look\s+up|look\s+for|find)"


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s.]", " ", text.casefold()).split())


def named_browser(text: str) -> str | None:
    """The browser a request names ("open brave browser and ..."), if any."""
    words = f" {_plain(text)} "
    return next((name for name in BROWSERS if f" {name} " in words), None)


def named_site(text: str) -> str | None:
    """The SITE_SEARCH site a request searches or opens, e.g. "youtube"; None for the web."""
    for pattern in _SITE_PLACE:
        match = pattern.search(text)
        if match:
            return re.sub(r"\s", "", match.group(1).casefold())
    return None


def site_query(text: str, site: str) -> str | None:
    """What to search for on ``site``: "open youtube and search sidemen sunday" and
    "search sidemen sunday on youtube" both give "sidemen sunday"."""
    name = r"(?:the\s+)?" + _SITE
    for pattern in (rf"\b{_SEARCH_VERB}\s+(?P<q>.+?)\s+(?:on|in|at|using|via)\s+{name}\b",
                    rf"\bsearch\s+{name}\s+for\s+(?P<q>.+)",
                    rf"\b{name}\b.*?\b{_SEARCH_VERB}\s+(?P<q>.+)"):
        match = re.search(pattern, text, re.I)
        if match and named_site(match.group(0)) in (site, None):
            query = re.sub(rf"\s+(?:on|in|at|using|via|with)\s+(?:the\s+)?(?:{_SITE}|[\w ]*browser|"
                           rf"{'|'.join(BROWSERS)})\b.*$", "", match.group("q"), flags=re.I)
            query = " ".join(query.strip(" .!?,\"'").split())
            if query:
                return query
    return None


def _launched_browser(context: ToolContext) -> str | None:
    for launch in reversed(context.launches):
        if named_browser(launch.app) is not None:
            return launch.app
    return None


def remembered_browser(context: ToolContext, site: str | None) -> str | None:
    """The browser the user's memory routes this site to ("youtube.com" -> Brave), else its default."""
    store = getattr(context, "memory", None)
    if store is None:
        return None
    try:
        return store.browser_for(site)
    except Exception:  # noqa: BLE001 - memory trouble never stops a web action
        return None


def browser_choice(args: dict, context: ToolContext, site: str | None = None) -> str | None:
    """The browser to use, first match wins: the one the user's words name (this request, or the
    one it answers); the remembered browser for ``site`` (aqua.memory: e.g. youtube.com -> Brave,
    default -> Chrome), unless a browser was launched earlier in this request; the model's argument;
    the launched browser; then the preferred-browser setting (AQUA_BROWSER / preferred-browser.txt,
    below memory). None: the Windows default."""
    said = named_browser(context.request) or next(
        (name for name in map(named_browser, reversed(getattr(context, "earlier", ()))) if name), None)
    if said:
        return said
    launched, remembered = _launched_browser(context), remembered_browser(context, site)
    if remembered:
        return launched or remembered
    return args.get("browser") or launched or context.preferred_browser or None


def _site_host(args: dict, context: ToolContext) -> str | None:
    """The site a web call goes to, for memory routing: the URL's host, or the search site."""
    if args.get("url"):
        url = normalize_url(args["url"])
        return (urlsplit(url).hostname or None) if url else None
    site = args.get("site") or named_site(context.request)
    return f"{site}.com" if site else None


def _browser(args: dict, context: ToolContext):
    """(installed browser entry or None, note for the reply)."""
    name = browser_choice(args, context, _site_host(args, context))
    if not name:
        return None, ""
    match = context.apps.find(name)
    if match.entry is None and name.casefold().endswith(" browser"):
        match = context.apps.find(name[:-len(" browser")])  # "brave browser" is installed as "Brave".
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


def _site_of(host: str) -> str | None:
    """"www.youtube.com" -> "youtube" for the sites Aqua can search."""
    parts = host.casefold().split(".")
    return next((site for site in SITE_SEARCH if site in parts[-3:-1]), None)


def _where(where: str, args: dict, context: ToolContext) -> str:
    """" in Brave" for the done phrase, unless Brave was opened in this same request."""
    if where == "the browser" or _launched_browser(context) == where:
        return ""
    return f" in {where}"


def _open(args: dict, context: ToolContext) -> ToolOutcome:
    url = normalize_url(args["url"])
    host = urlsplit(url).hostname or ""
    site = _site_of(host)
    query = site_query(context.request, site) if site and urlsplit(url).path in ("", "/") else None
    if query:
        # "open youtube and search X": one search on that site, not the home page and then a guess.
        return _search({**args, "query": query, "site": site}, context, steps=2)
    where, note = _open_in(url, args, context)
    if where is None:
        return ToolOutcome("failed", "The browser could not be opened.")
    shown = SITE_NAMES.get(site, host.removeprefix("www.")) if site else host.removeprefix("www.")
    return ToolOutcome("ok", f"Opened {host} in {where}.{note}", done=f"opened {shown}{_where(where, args, context)}",
                       data={"url": url, "browser": where})


def search_url(query: str, site: str | None = None) -> str:
    return SITE_SEARCH.get(site or "", SEARCH_URL).format(quote_plus(" ".join(query.split())))


def _search(args: dict, context: ToolContext, steps: int = 1) -> ToolOutcome:
    query = " ".join(args["query"].split())
    # The model often drops the site ("open youtube and search X"): the request says it.
    site = args.get("site") or named_site(context.request)
    args = {**args, "site": site} if site else args
    key = (site, query.casefold())
    if key in context.searches:
        return ToolOutcome("duplicate", "Already searched for that; not repeated.")
    where, note = _open_in(search_url(query, site), args, context)
    if where is None:
        return ToolOutcome("failed", "The browser could not be opened.")
    context.searches.append(key)
    name = SITE_NAMES.get(site, "") if site else ""
    on = f" on {name}" if name else " on DuckDuckGo"
    if site and _SITE_PLACE[2].search(context.request):
        steps = 2  # "open youtube and search X" asked for both, and this one search does both.
    return ToolOutcome("ok", f"Opened a search for {query!r}{on} in {where}.{note}",
                       done=f"searched {name or 'the web'} for {query}{_where(where, args, context)}",
                       data={"site": site or "duckduckgo", "browser": where, "query": query, "steps": steps})


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
