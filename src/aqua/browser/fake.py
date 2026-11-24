"""An in-memory browser bridge for tests and the YouTube benchmark. Nothing here touches a browser.

It answers like the extension's pageCommand(): the same result fields and error codes.
"""

from dataclasses import dataclass, field

from .bridge import BridgeError, BridgeUnavailable

QUALITIES = ("1080p", "720p", "480p", "360p", "240p", "144p")
RATES = (0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2)


@dataclass
class FakeTab:
    browser: str
    last_accessed: float
    page: str = "watch"                 # watch | results | home
    title: str = "Lofi beats to study to"
    channel: str = "Lofi Girl"
    position: float = 65
    duration: float = 3600
    playing: bool = True
    rate: float = 1
    quality: str = "720p"
    qualities: tuple = QUALITIES
    captions: bool | None = False       # None: the video has no captions
    volume: int = 80
    muted: bool = False
    fullscreen: bool = False
    skip_button: bool = False
    results: list = field(default_factory=list)  # (title, channel) on a results page
    query: str = ""


class FakeBrowserBridge:
    def __init__(self, tabs=(), *, connected: bool = True) -> None:
        self.tabs = list(tabs)
        self.is_connected = connected
        self.calls: list[tuple[str, str, dict]] = []   # (browser, action, args)
        self.effects: list[str] = []                    # "youtube:pause", ... (actions that changed something)
        self.error: str | None = None

    @property
    def connected(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(tab.browser for tab in self.tabs)) if self.is_connected else ()

    def add_tab(self, browser: str = "chrome", last_accessed: float = 100, **fields) -> FakeTab:
        tab = FakeTab(browser, last_accessed, **fields)
        self.tabs.append(tab)
        return tab

    def _newest(self) -> FakeTab | None:
        return max(self.tabs, key=lambda tab: tab.last_accessed, default=None)

    def youtube(self, action: str, args: dict) -> dict:
        if not self.is_connected:
            raise BridgeUnavailable("not connected")
        tab = self._newest()
        browser = tab.browser if tab is not None else "chrome"
        self.calls.append((browser, action, dict(args)))
        result = self._run(tab, action, args)
        if "done" in result and action != "now_playing":
            self.effects.append(f"youtube:{action}")
        return {**result, "browser": tab.browser if tab is not None else browser if action == "search" else None}

    def _now(self, tab: FakeTab) -> dict:
        return {"title": tab.title, "channel": tab.channel, "position": round(tab.position),
                "duration": round(tab.duration), "playing": tab.playing}

    def _run(self, tab: FakeTab | None, action: str, args: dict) -> dict:
        if action == "search":
            if tab is None:
                tab = self.add_tab(self.connected[0] if self.connected else "chrome", 1000)
            tab.page, tab.query = "results", str(args.get("query", ""))
            if not tab.results:
                tab.results = [(f"{tab.query} - result {i}", f"Channel {i}") for i in range(1, 6)]
            return {"done": "search", "query": tab.query}
        if tab is None:
            return {"error": "no_tab"}
        if action == "now_playing":
            return {"done": "now_playing", **self._now(tab)} if tab.page == "watch" else {"error": "no_video"}
        if action == "play_result":
            if tab.page != "results":
                return {"error": "not_results"}
            n = int(args.get("n", 1))
            if n > len(tab.results):
                return {"error": "no_result", "count": len(tab.results)}
            tab.title, tab.channel = tab.results[n - 1]
            tab.page, tab.position, tab.playing = "watch", 0, True
            return {"done": "play_result", "n": n, "title": tab.title}
        if action == "skip_ad":
            if not tab.skip_button:
                return {"error": "no_skip_button"}
            tab.skip_button = False
            return {"done": "skip_ad"}
        if tab.page != "watch":
            return {"error": "no_video"}
        if action in ("play", "pause"):
            tab.playing = action == "play"
            return {"done": action, **self._now(tab)}
        if action == "seek":
            if "by" in args:
                target = tab.position + args["by"]
            elif "to" in args:
                target = args["to"]
            else:
                target = tab.duration * args.get("percent", 0) / 100
            tab.position = max(0, min(tab.duration - 1, target))
            return {"done": "seek", **self._now(tab)}
        if action in ("next", "previous"):
            tab.title, tab.position = f"{action} video", 0
            return {"done": action}
        if action == "fullscreen":
            tab.fullscreen = bool(args.get("on", True))
            return {"done": "fullscreen", "on": tab.fullscreen}
        if action == "captions":
            if tab.captions is None:
                return {"error": "no_captions"}
            tab.captions = bool(args.get("on", True))
            return {"done": "captions", "on": tab.captions}
        if action == "speed":
            if args.get("rate") not in RATES:
                return {"error": "unsupported_speed", "available": list(RATES)}
            tab.rate = args["rate"]
            return {"done": "speed", "rate": tab.rate}
        if action == "quality":
            wanted = str(args.get("quality", "")).lower()
            if wanted == "auto":
                return {"done": "quality", "quality": "auto", "available": list(tab.qualities)}
            if wanted not in tab.qualities:
                return {"error": "quality_unavailable", "available": list(tab.qualities)}
            tab.quality = wanted
            return {"done": "quality", "quality": wanted, "requested": wanted, "available": list(tab.qualities)}
        if action == "volume":
            tab.volume, tab.muted = int(args["level"]), int(args["level"]) == 0
            return {"done": "volume", "level": tab.volume}
        if action == "mute":
            tab.muted = bool(args.get("on", True))
            return {"done": "mute", "on": tab.muted}
        raise BridgeError("unknown action")
