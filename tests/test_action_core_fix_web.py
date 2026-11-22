"""Fix 5: web_search and open_url can name a browser; the harness sees windows created
during a turn being minimised or maximised."""

import json
from pathlib import Path
import unittest

from aqua.agent.context import ToolContext
from aqua.agent.policy import DENY, Policy
from aqua.tools.app_index import AppIndex
from aqua.tools.fake import FakeDesktop
from aqua.tools.web import SEARCH_URL, open_url, web_search

from action_core_helpers import call, make_engine
from tools import benchmark_agent as bench


def context():
    desktop = FakeDesktop()
    return ToolContext(desktop=desktop, apps=AppIndex(desktop.app_entries, clock=desktop.monotonic), policy=Policy())


class BrowserArgumentTests(unittest.TestCase):
    def test_search_in_a_named_browser(self):
        ctx = context()
        outcome = web_search.run({"query": "weather in London", "browser": "Chrome"}, ctx)
        self.assertEqual(ctx.desktop.opened_urls, [SEARCH_URL.format("weather+in+London")])
        self.assertEqual(ctx.desktop.url_browsers, ["Google Chrome"])
        self.assertIn("in Google Chrome", outcome.message)
        self.assertEqual(ctx.desktop.launched, [])  # The search opens the browser itself; nothing is typed.

    def test_default_browser_and_unknown_browser(self):
        ctx = context()
        open_url.run({"url": "youtube.com"}, ctx)
        outcome = web_search.run({"query": "pizza", "browser": "Netscape"}, ctx)
        self.assertEqual(ctx.desktop.url_browsers, [None, None])
        self.assertIn("Netscape was not found", outcome.message)

    def test_blocked_browser_is_denied(self):
        ctx = context()
        self.assertEqual(ctx.policy.decide(web_search, {"query": "x", "browser": "PowerShell"}, ctx).action, DENY)
        self.assertEqual(ctx.policy.decide(open_url, {"url": "example.com", "browser": "cmd"}, ctx).action, DENY)

    def test_description_says_searching_is_not_typing(self):
        self.assertIn("never means typing into a browser", web_search.description)

    def test_open_chrome_and_search_through_the_engine(self):
        engine, desktop, _ = make_engine([call("web_search", query="weather in London", browser="Chrome")],
                                         "Searched for the weather in London in Chrome.")
        engine.respond("open Chrome and search for weather in London")
        self.assertEqual((desktop.url_browsers, desktop.typed), (["Google Chrome"], []))


class HarnessWindowDiffTests(unittest.TestCase):
    def test_new_window_minimised_in_the_same_turn_is_an_effect(self):
        suite = json.loads((Path(__file__).resolve().parents[1] / "benchmarks" / "agent-v1" / "cases.json").read_bytes())
        desktop = bench.make_desktop(suite, {})
        before = bench.snapshot(desktop)
        entry = next(e for e in desktop.app_entries() if e.name == "Notepad")
        desktop.launch(entry)
        new = next(w for w in desktop.windows() if w.process == "notepad.exe")
        desktop.show(new.handle, "minimise")
        self.assertEqual(bench.effects(before, desktop), ["launch:notepad", "minimise:untitled - notepad"])
        before = bench.snapshot(desktop)
        desktop.launch(next(e for e in desktop.app_entries() if e.name == "Calculator"))
        self.assertEqual(bench.effects(before, desktop), ["launch:calculator"])  # A normal new window: no extra.


if __name__ == "__main__":
    unittest.main()
