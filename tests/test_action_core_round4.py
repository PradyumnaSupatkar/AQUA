"""Round 4 live re-test fixes: browser choice, site search, honest web replies, dictation with
no destination, named-app phrasings, host-written replies, new lines in editors, how-to answers."""

import unittest

from aqua.tools.adapter import AppEntry
from aqua.tools.fake import DEFAULT_APPS, FakeDesktop

from action_core_helpers import call, make_engine

BRAVE = AppEntry("Brave", r"C:\ProgramData\Start Menu\Brave.lnk", "shortcut",
                 r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe")


def browser_desktop():
    return FakeDesktop(apps=(*DEFAULT_APPS, BRAVE))


class BrowserChoiceTests(unittest.TestCase):
    def test_browser_named_in_the_request_is_used_when_the_model_omits_it(self):
        desktop = browser_desktop()
        engine, _, _ = make_engine([call("launch_app", app="Brave"), call("open_url", url="youtube.com")],
                                   "Opened Brave and YouTube.", desktop=desktop)
        engine.respond("open brave browser and open youtube.com")
        self.assertEqual(desktop.opened_urls, ["https://youtube.com"])
        self.assertEqual(desktop.url_browsers, ["Brave"])

    def test_a_browser_launched_earlier_in_the_request_is_used(self):
        from aqua.agent.context import Launch, ToolContext
        from aqua.tools.app_index import AppIndex
        from aqua.tools.web import browser_choice
        context = ToolContext(desktop=browser_desktop(), apps=AppIndex((BRAVE,)))
        context.begin_request("start my usual browser then go to github.com")
        context.launches.append(Launch("Brave", 7))
        self.assertEqual(browser_choice({}, context), "Brave")
        self.assertEqual(browser_choice({"browser": "Chrome"}, context), "Chrome")  # The model's choice wins.

    def test_preferred_browser_setting_applies_when_nothing_is_named(self):
        desktop = browser_desktop()
        engine, _, _ = make_engine([call("open_url", url="github.com")], "Opened GitHub.", desktop=desktop)
        engine.context.preferred_browser = "Brave"
        engine.respond("open github.com")
        self.assertEqual(desktop.url_browsers, ["Brave"])
        engine, _, _ = make_engine([call("open_url", url="github.com")], "Opened GitHub.", desktop=desktop)
        engine.respond("open github.com")
        self.assertEqual(desktop.url_browsers[-1], None)  # No setting: the Windows default.

    def test_preferred_browser_setting_file(self):
        import os
        import tempfile
        from pathlib import Path
        from aqua.tools.web import read_preferred_browser
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "preferred-browser.txt"
            self.assertIsNone(read_preferred_browser(path))
            path.write_text("# my browser\nBrave\n", encoding="utf-8")
            saved = os.environ.pop("AQUA_BROWSER", None)
            try:
                self.assertEqual(read_preferred_browser(path), "Brave")
            finally:
                if saved is not None:
                    os.environ["AQUA_BROWSER"] = saved

    def test_named_browser(self):
        from aqua.tools.web import named_browser
        self.assertEqual(named_browser("open brave browser and open youtube.com"), "brave browser")
        self.assertEqual(named_browser("search cats in Google Chrome"), "google chrome")
        self.assertIsNone(named_browser("open youtube.com"))


class SiteSearchFillTests(unittest.TestCase):
    def test_named_site_and_query(self):
        from aqua.tools.web import named_site, site_query
        self.assertEqual(named_site("open youtube and search sidemen sunday"), "youtube")
        self.assertEqual(named_site("search sidemen sunday in you tube"), "youtube")
        self.assertIsNone(named_site("search for youtube alternatives"))
        self.assertIsNone(named_site("search cats in google chrome"))
        self.assertEqual(site_query("open youtube and search sidemen sunday", "youtube"), "sidemen sunday")
        self.assertEqual(site_query("search sidemen sunday in youtube", "youtube"), "sidemen sunday")
        self.assertEqual(site_query("open YouTube and search for lofi beats in Brave", "youtube"), "lofi beats")

    def test_site_is_filled_in_when_the_model_omits_it(self):
        engine, desktop, _ = make_engine([call("web_search", query="sidemen sunday")], "Searched.")
        engine.respond("open youtube and search sidemen sunday")
        self.assertEqual(desktop.opened_urls, ["https://www.youtube.com/results?search_query=sidemen+sunday"])

    def test_open_site_then_search_is_one_site_search(self):
        engine, desktop, _ = make_engine([call("open_url", url="youtube.com"),
                                          call("web_search", query="sidemen sunday")], "Searched.")
        result = engine.respond("search sidemen sunday in youtube")
        self.assertEqual(desktop.opened_urls, ["https://www.youtube.com/results?search_query=sidemen+sunday"])
        self.assertEqual([op.status for op in result.task.operations], ["ok", "duplicate"])

    def test_plain_open_of_a_site_still_opens_it(self):
        engine, desktop, _ = make_engine([call("open_url", url="youtube.com")], "Opened YouTube.")
        engine.respond("open youtube")
        self.assertEqual(desktop.opened_urls, ["https://youtube.com"])


if __name__ == "__main__":
    unittest.main()
