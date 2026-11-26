"""Remembered browser routing, applied by host code: YouTube -> Brave, other sites -> Chrome; the
user's own words still win, and AQUA_BROWSER / preferred-browser.txt stay below memory."""

import unittest

from aqua.browser.fake import FakeBrowserBridge
from aqua.tools.fake import DEFAULT_APPS, FakeDesktop

from action_core_helpers import call, make_engine
from test_action_core_round4 import BRAVE
from test_memory_store import temp_store


def routed(*rounds, preferred=None):
    desktop = FakeDesktop(apps=(*DEFAULT_APPS, BRAVE))
    engine, _, model = make_engine(*rounds, desktop=desktop)
    store = temp_store()
    store.set_preference("browser", "youtube.com", "Brave")
    store.set_preference("browser", "", "Chrome")
    engine.context.memory = store
    engine.context.preferred_browser = preferred
    return engine, desktop


class BrowserRoutingTests(unittest.TestCase):
    def test_youtube_opens_in_brave(self):
        engine, desktop = routed([call("open_url", url="youtube.com")])
        engine.respond("open youtube")
        self.assertEqual(desktop.url_browsers, ["Brave"])

    def test_other_sites_open_in_chrome(self):
        engine, desktop = routed([call("open_url", url="github.com")])
        engine.respond("open github.com")
        self.assertEqual(desktop.url_browsers, ["Google Chrome"])

    def test_searches_follow_the_site(self):
        engine, desktop = routed([call("web_search", query="lofi beats", site="youtube")])
        engine.respond("search lofi beats on youtube")
        engine2, desktop2 = routed([call("web_search", query="weather pune")])
        engine2.respond("search the web for weather pune")
        self.assertEqual((desktop.url_browsers, desktop2.url_browsers), (["Brave"], ["Google Chrome"]))

    def test_a_browser_in_the_users_words_wins(self):
        engine, desktop = routed([call("open_url", url="youtube.com")])
        engine.respond("open youtube in chrome")
        self.assertEqual(desktop.url_browsers, ["Google Chrome"])

    def test_memory_beats_the_models_guess_and_the_setting(self):
        engine, desktop = routed([call("open_url", url="youtube.com", browser="Chrome")], preferred="Chrome")
        engine.respond("open youtube")
        self.assertEqual(desktop.url_browsers, ["Brave"])

    def test_the_old_setting_still_applies_without_memory(self):
        engine, desktop = routed([call("open_url", url="github.com")], preferred="Brave")
        engine.context.memory = temp_store()
        engine.respond("open github.com")
        self.assertEqual(desktop.url_browsers, ["Brave"])

    def test_a_youtube_search_through_the_extension_opens_in_the_remembered_browser(self):
        engine, _desktop = routed([call("youtube", action="search", query="lofi beats")])
        bridge = FakeBrowserBridge(browsers=("chrome", "brave"))
        bridge.add_tab("chrome", 100)
        engine.context.browser = bridge
        engine.respond("search youtube for lofi beats")
        self.assertEqual(bridge.effects, ["youtube:brave:search=lofi beats"])

    def test_the_extension_search_without_memory_is_unchanged(self):
        engine, _desktop = routed([call("youtube", action="search", query="lofi beats")])
        engine.context.memory = None
        bridge = FakeBrowserBridge(browsers=("chrome", "brave"))
        bridge.add_tab("chrome", 100)
        engine.context.browser = bridge
        engine.respond("search youtube for lofi beats")
        self.assertEqual(bridge.effects, ["youtube:chrome:search=lofi beats"])


if __name__ == "__main__":
    unittest.main()
