"""open_settings: open one page of Windows Settings from a fixed allowlist (tier 1).

Some changes Windows lets only the user make: since Windows 10 no app may set the default browser
or other default apps. Aqua opens the right Settings page instead and says what to choose there.
Only the ms-settings: pages below can open; the model names a page key, never a URI.
"""

import re

from aqua.agent.context import ToolContext
from aqua.agent.registry import SAFE, Tool, ToolOutcome

# page key -> (ms-settings URI, name as said in replies)
PAGES = {
    "default_apps": ("ms-settings:defaultapps", "Default apps"),
    "sound": ("ms-settings:sound", "Sound"),
    "display": ("ms-settings:display", "Display"),
    "bluetooth": ("ms-settings:bluetooth", "Bluetooth & devices"),
    "wifi": ("ms-settings:network-wifi", "Wi-Fi"),
    "network": ("ms-settings:network", "Network & internet"),
    "notifications": ("ms-settings:notifications", "Notifications"),
    "power": ("ms-settings:powersleep", "Power & battery"),
    "apps": ("ms-settings:appsfeatures", "Installed apps"),
    "about": ("ms-settings:about", "About"),
    "windows_update": ("ms-settings:windowsupdate", "Windows Update"),
}
URIS = frozenset(uri for uri, _label in PAGES.values())
# Other names small models and people use for the same pages.
_SPELLING = {"default_browser": "default_apps", "defaults": "default_apps", "audio": "sound", "screen": "display",
             "wi-fi": "wifi", "wireless": "wifi", "internet": "network", "battery": "power",
             "power_and_battery": "power", "installed_apps": "apps", "update": "windows_update",
             "updates": "windows_update", "system_info": "about"}


def page_of(args: dict) -> str:
    page = args["page"]
    return _SPELLING.get(page, page)


def _run(args: dict, context: ToolContext) -> ToolOutcome:
    uri, label = PAGES[page_of(args)]
    try:
        context.desktop.open_settings(uri)
    except (OSError, NotImplementedError):
        return ToolOutcome("failed", f"Windows did not open {label} in Settings.")
    return ToolOutcome("ok", f"Opened {label} in Settings.", done=f"opened {label} in Settings",
                       data={"page": label})


open_settings = Tool(
    name="open_settings", group="settings", tier=SAFE,
    description=("Open a page of Windows Settings so the user can change it there: default_apps (default browser "
                 "and other default apps, which only the user may change), sound, display, bluetooth, wifi, "
                 "network, notifications, power (power & battery), apps (installed apps), about, windows_update."),
    parameters={"type": "object", "properties": {
        "page": {"type": "string", "enum": [*PAGES, *_SPELLING]}}, "required": ["page"]},
    run=_run, describe=lambda args: f"open {PAGES[page_of(args)][1]} in Settings")


# "make Brave my default browser", "set Chrome as the default browser", "change my default browser to Firefox".
_DEFAULT_BROWSER = re.compile(
    r"\b(?:make|set|change|switch|use|have)\b[^.?!]*?\bdefault\s+(?:web\s+)?browser\b|"
    r"\bdefault\s+(?:web\s+)?browser\s+(?:to|as|should\s+be)\b|"
    r"\bi\s+want\b[^.?!]*\bas\s+(?:my|the)\s+default\s+(?:web\s+)?browser\b", re.I)
_NAMED = (re.compile(r"\b(?:make|set|use|have)\s+(?P<app>[\w .+-]{2,30}?)\s+(?:as\s+)?(?:my|the)\s+default\b", re.I),
          re.compile(r"\bdefault\s+(?:web\s+)?browser\s+(?:to|as|should\s+be)\s+(?P<app>[\w .+-]{2,30}?)\s*[.!?]*$", re.I),
          re.compile(r"\bwant\s+(?P<app>[\w .+-]{2,30}?)\s+as\s+(?:my|the)\s+default\b", re.I))
_NOT_A_NAME = {"it", "this", "that", "one", "a", "the", "my", "browser"}


def default_browser_request(text: str) -> tuple[bool, str | None]:
    """(asks to change the default browser, the browser named or None)."""
    text = text.replace("’", "'")
    if not _DEFAULT_BROWSER.search(text):
        return False, None
    for pattern in _NAMED:
        match = pattern.search(text)
        if match:
            name = " ".join(match.group("app").split())
            name = re.sub(r"^(?:please\s+|aqua\s+)+", "", name, flags=re.I)
            if name and name.casefold() not in _NOT_A_NAME:
                return True, name[:1].upper() + name[1:]
    return True, None


def default_browser_reply(app: str | None, opened: bool) -> str:
    choose = (f"find {app} there and click Set default" if app else "choose your browser there and click Set default")
    if opened:
        return f"Windows only lets you change the default browser yourself, so I opened Default apps in Settings: {choose}."
    return ("Windows only lets you change the default browser yourself: open Settings, then Apps, then Default apps, "
            f"and {choose}.")
