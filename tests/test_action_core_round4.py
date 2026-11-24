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


class HostReplyTests(unittest.TestCase):
    def test_finished_actions_are_reported_by_the_host_without_another_model_call(self):
        desktop = browser_desktop()
        engine, _, model = make_engine([call("launch_app", app="Brave"), call("open_url", url="youtube.com")],
                                       desktop=desktop)
        result = engine.respond("open brave browser and open youtube.com")
        self.assertEqual(result.text, "Opened Brave and YouTube.")
        self.assertEqual((len(model.requests), result.stage), (1, "host-reply"))

    def test_site_search_reply_names_the_site_and_query(self):
        engine, _, model = make_engine([call("web_search", query="sidemen sunday", site="youtube")])
        self.assertEqual(engine.respond("search sidemen sunday on youtube").text, "Searched YouTube for sidemen sunday.")
        self.assertEqual(len(model.requests), 1)

    def test_open_site_and_search_is_one_step_covering_both(self):
        engine, desktop, model = make_engine([call("web_search", query="sidemen sunday")])
        result = engine.respond("open youtube and search sidemen sunday")
        self.assertEqual(result.text, "Searched YouTube for sidemen sunday.")
        self.assertEqual(len(model.requests), 1)

    def test_steps_left_still_go_to_the_model(self):
        engine, _, model = make_engine([call("launch_app", app="Chrome")],
                                       "I have opened Chrome. I can't delete files.")
        result = engine.respond("Open Chrome and delete my files.")
        self.assertEqual(len(model.requests), 2)
        self.assertIn("can't delete files", result.text)

    def test_readings_failures_and_look_ups_are_answered_by_the_model(self):
        engine, _, model = make_engine([call("get_battery")], "The battery is at 76%.")
        self.assertEqual(engine.respond("how much battery is left").text, "The battery is at 76%.")
        self.assertEqual(len(model.requests), 2)
        engine, _, model = make_engine([call("launch_app", app="Nonexistent App")], "I couldn't find that app.")
        engine.respond("open nonexistent app")
        self.assertEqual(len(model.requests), 2)

    def test_opening_an_app_only_to_type_is_not_the_whole_request(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("notes.txt - Notepad", "notepad.exe", front=False)
        engine, _, model = make_engine([call("launch_app", app="notepad")], [call("type_text", text="hi")],
                                       desktop=desktop)
        result = engine.respond("write hi in notepad")
        self.assertEqual(desktop.text, {notepad.handle: "hi"})
        self.assertEqual(result.text, "Brought Notepad to the front and typed your text into Notepad.")

    def test_helpers(self):
        from aqua.agent.loop import host_summary, steps_asked
        self.assertEqual(steps_asked("mute"), 1)
        self.assertEqual(steps_asked("open brave browser and open youtube.com"), 2)
        self.assertEqual(steps_asked("open notepad type hello"), 2)  # No "and": the verbs count.
        self.assertEqual(host_summary(["opened Notepad", "typed your text into Notepad"]),
                         "Opened Notepad and typed your text.")
        self.assertEqual(host_summary(["opened Brave", "opened YouTube"]), "Opened Brave and YouTube.")


class HonestWebReplyTests(unittest.TestCase):
    def test_a_reply_naming_the_wrong_site_is_replaced_by_the_facts(self):
        engine, _, _ = make_engine([call("web_search", query="sidemen sunday")],
                                   "I searched on YouTube for sidemen sunday.")
        result = engine.respond("search sidemen sunday and tell me what you find")
        self.assertEqual(result.text, "Searched the web for sidemen sunday.")

    def test_a_reply_naming_the_wrong_browser_is_replaced(self):
        from aqua.agent.loop import Operation, web_mismatch
        ops = [Operation("open_url", {}, "ok", "Opened youtube.com in the browser.", "opened YouTube")]
        self.assertTrue(web_mismatch("Opened YouTube in Brave.", ops))
        self.assertFalse(web_mismatch("Opened YouTube.", ops))
        self.assertFalse(web_mismatch("Opened Brave.", [Operation("launch_app", {}, "ok", "Opened Brave.")]))


class PromiseOfferTests(unittest.TestCase):
    """Round 3 benchmark regression: 'I'd like somewhere to jot down a few thoughts' opened Notepad."""

    def test_a_promise_to_a_wish_becomes_a_host_question(self):
        engine, desktop, model = make_engine("I can open Notepad for you so you can jot them down.",
                                             [call("launch_app", app="Notepad", request="asked")])
        asked = engine.respond("I'd like somewhere to jot down a few thoughts")
        self.assertEqual(asked.outcome, "confirm")
        self.assertEqual(asked.text, "Open Notepad? Say yes or no.")
        self.assertNotIn("call the tool now", model.requests[1]["messages"][-1]["content"])
        engine.respond("no")
        self.assertEqual(desktop.launched, [])

    def test_an_offer_back_to_a_polite_command_is_nudged_to_act(self):
        engine, desktop, model = make_engine("I would be able to open Chrome for you. Would you like me to do that now?",
                                             [call("launch_app", app="Chrome")])
        result = engine.respond("would you be able to open Chrome?")
        self.assertEqual(desktop.launched, ["Google Chrome"])
        self.assertEqual(result.outcome, "action")
        self.assertIn("call the tool now", model.requests[1]["messages"][-1]["content"])

    def test_negated_requests_are_never_offers(self):
        engine, desktop, model = make_engine("I will not open Chrome.")
        engine.respond("don't open Chrome")
        self.assertEqual((desktop.launched, len(model.requests)), ([], 1))


class HowToTests(unittest.TestCase):
    def test_a_refusal_to_a_how_to_question_becomes_the_steps(self):
        engine, desktop, _ = make_engine("I don't know how to lock the screen.")
        result = engine.respond("how do I lock my screen?")
        self.assertEqual(result.text, "Press Windows + L. I can also lock it for you if you want.")
        self.assertFalse(desktop.locked)

    def test_a_real_explanation_is_kept(self):
        engine, _, _ = make_engine("Press Windows and L together.")
        self.assertEqual(engine.respond("how do I lock my screen?").text, "Press Windows and L together.")

    def test_an_explanation_with_a_promise_keeps_the_steps_and_asks(self):
        engine, desktop, _ = make_engine("Press Windows + L. I can also lock it for you.",
                                         [call("lock_screen", request="unsure")])
        asked = engine.respond("how do I lock my screen?")
        self.assertEqual(asked.text, "Press Windows + L. Lock the screen? Say yes or no.")
        self.assertFalse(desktop.locked)

    def test_refusal_detection(self):
        from aqua.agent.howto import is_refusal
        self.assertTrue(is_refusal("I don't know how to lock the screen."))
        self.assertTrue(is_refusal("I'm not sure how to do that."))
        self.assertFalse(is_refusal("Press Windows + L."))

    def test_prompt_asks_for_steps(self):
        from aqua.agent.prompt import SYSTEM_PROMPT
        self.assertIn("answer them with the steps", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
