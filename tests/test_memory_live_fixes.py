"""Live memory test findings (chat, --engine new): refused secrets leave the session, recall is the
host's own list, "open <site>" goes to open_url with the browser preference, the recency guard never
overrides a local reading, and CLI lines typed into chat get a host reply."""

import unittest

from aqua import chat
from aqua.browser.fake import FakeBrowserBridge
from aqua.tools.adapter import AppEntry
from aqua.tools.fake import DEFAULT_APPS, FakeDesktop

from action_core_helpers import call, make_engine
from test_action_core_round4 import BRAVE
from test_memory_store import temp_store

EDGE = AppEntry("Microsoft Edge", r"C:\ProgramData\Start Menu\Microsoft Edge.lnk", "shortcut",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
YOUTUBE_APP = AppEntry("YouTube", "youtube-pwa-id", "appsfolder")


def engine_with(*rounds, apps=(BRAVE, EDGE), store=None, routes=True):
    desktop = FakeDesktop(apps=(*DEFAULT_APPS, *apps))
    engine, _, model = make_engine(*rounds, desktop=desktop)
    engine.context.memory = store if store is not None else temp_store()
    if routes:
        engine.context.memory.set_preference("browser", "youtube.com", "Brave")
        engine.context.memory.set_preference("browser", "", "Chrome")
    return engine, desktop, model


def remember(**args):
    return call("remember", request=None, **args)


class SecretsLeaveTheSessionTests(unittest.TestCase):
    def history_text(self, engine):
        return " ".join(str(m.get("content", "")) + str(m.get("tool_calls", "")) for m in engine.conversation.history)

    def test_the_live_wifi_echo_sequence(self):
        engine, _desktop, model = engine_with(
            [remember(kind="fact", text="My wifi password is test123")],
            "I can't remember passwords, so your wifi password test123 was not saved.",
            "I remember that your WiFi password is test123.", routes=False)
        engine.respond("remember my wifi password is test123")
        self.assertEqual(engine.context.memory.entries(), [])
        self.assertNotIn("test123", self.history_text(engine))
        self.assertIn("[sensitive detail not kept]", self.history_text(engine))
        listed = engine.respond("aqua memory list")
        self.assertEqual(listed.text, "I don't remember anything about you yet.")
        self.assertEqual(len(model.requests), 2)  # The list needed no model call.
        echo = engine.respond("what's my wifi password?")
        self.assertEqual(echo.text, "I don't remember anything about you yet.")  # The claim is not in the store.
        self.assertNotIn("test123", model.requests[-1]["messages"].__repr__())

    def test_a_refusal_without_a_remember_call_is_redacted_too(self):
        engine, _desktop, _model = engine_with("I can't keep passwords like hunter22.", routes=False)
        engine.respond("please remember my email password is hunter22")
        self.assertNotIn("hunter22", self.history_text(engine))

    def test_ordinary_turns_are_kept(self):
        engine, _desktop, _model = engine_with([remember(kind="fact", text="I live in Pune")], routes=False)
        engine.respond("remember that I live in Pune")
        self.assertIn("Pune", self.history_text(engine))

    def test_a_true_memory_claim_stays(self):
        store = temp_store()
        store.add_fact("My projects are in D:\\Projects")
        engine, _desktop, _model = engine_with("I remember that your projects are in D:\\Projects.", store=store,
                                               routes=False)
        self.assertEqual(engine.respond("where do I keep my projects?").text,
                         "I remember that your projects are in D:\\Projects.")


class DeterministicRecallTests(unittest.TestCase):
    def test_every_phrase_lists_facts_and_preferences_with_ids_and_no_model(self):
        store = temp_store()
        store.add_fact("My projects are in D:\\Projects")
        engine, _desktop, model = engine_with(store=store)
        wanted = ("I remember: your projects are in D:\\Projects (f1); open YouTube in Brave (p2); "
                  "use Chrome for other websites (p3).")
        for text in ("what do you remember", "What do you remember about me?", "what do you know about me",
                     "list memories", "show my memories", "memory list", "/memory", "Hey Aqua, what do you remember?"):
            self.assertEqual(engine.respond(text).text, wanted, text)
        self.assertEqual(model.requests, [])

    def test_empty(self):
        engine, _desktop, _model = engine_with(routes=False)
        self.assertEqual(engine.respond("what do you remember about me?").text,
                         "I don't remember anything about you yet.")

    def test_memory_off(self):
        from aqua.tools import build_registry
        engine, _, model = make_engine("I don't keep memories.", registry=build_registry(memory=False))
        engine.respond("what do you remember about me?")
        self.assertEqual(len(model.requests), 1)  # No memory tools: the model answers as before.


class OpenSiteTests(unittest.TestCase):
    def test_open_youtube_on_the_first_turn_goes_to_brave_without_the_model(self):
        for _ in range(10):
            engine, desktop, model = engine_with()
            result = engine.respond("open youtube")
            self.assertEqual((desktop.opened_urls, desktop.url_browsers), (["https://youtube.com"], ["Brave"]))
            self.assertEqual((result.text, model.requests, desktop.launched), ("Opened YouTube in Brave.", [], []))

    def test_other_wordings(self):
        cases = {"go to youtube": ("https://youtube.com", "Brave"), "open youtube.com": ("https://youtube.com", "Brave"),
                 "open github": ("https://github.com", "Google Chrome"),
                 "open youtube in edge": ("https://youtube.com", "Microsoft Edge"),
                 "please open gmail": ("https://mail.google.com", "Google Chrome")}
        for text, (url, browser) in cases.items():
            engine, desktop, _model = engine_with()
            engine.respond(text)
            self.assertEqual((desktop.opened_urls, desktop.url_browsers), ([url], [browser]), text)

    def test_an_installed_app_with_that_name_is_left_to_the_model(self):
        engine, desktop, _model = engine_with([call("launch_app", app="YouTube")], apps=(BRAVE, YOUTUBE_APP))
        engine.respond("open youtube")
        self.assertEqual((desktop.launched, desktop.opened_urls), (["YouTube"], []))
        engine, desktop, _model = engine_with([call("launch_app", app="Spotify")])
        engine.respond("open spotify")
        self.assertEqual(desktop.launched, ["Spotify"])

    def test_launch_app_with_a_website_name_falls_back_to_open_url(self):
        engine, desktop, _model = engine_with([call("launch_app", app="YouTube")])
        result = engine.respond("could you open youtube real quick")
        self.assertEqual((desktop.opened_urls, desktop.url_browsers), (["https://youtube.com"], ["Brave"]))
        self.assertEqual(result.text, "Opened YouTube in Brave.")

    def test_the_youtube_tool_with_no_tab_or_connection_falls_back_to_open_url(self):
        for bridge in (FakeBrowserBridge(connected=False), FakeBrowserBridge(browsers=("brave",))):
            engine, desktop, _model = engine_with([call("youtube", action="play")])
            engine.context.browser = bridge
            engine.respond("could you open youtube real quick")
            self.assertEqual(desktop.url_browsers, ["Brave"])

    def test_playback_failures_are_still_reported(self):
        engine, desktop, _model = engine_with([call("youtube", action="pause")], "No YouTube tab is open.")
        engine.context.browser = FakeBrowserBridge(browsers=("brave",))
        result = engine.respond("pause the video")
        self.assertEqual((desktop.opened_urls, result.text), ([], "No YouTube tab is open."))


class RecencyGuardTests(unittest.TestCase):
    def test_a_youtube_status_right_now_is_answered(self):
        engine, _desktop, model = engine_with([call("youtube", action="now_playing", request=None)],
                                              "Lofi beats to study to is playing.")
        bridge = FakeBrowserBridge()
        bridge.add_tab()
        engine.context.browser = bridge
        result = engine.respond("what's playing on youtube right now?")
        self.assertEqual(result.text, "Lofi beats to study to is playing.")
        self.assertEqual(len(model.requests), 2)

    def test_a_device_reading_right_now_is_answered(self):
        engine, _desktop, _model = engine_with([call("device_status", request=None)], "The volume is at 50%.")
        self.assertEqual(engine.respond("what is the volume right now?").text, "The volume is at 50%.")

    def test_world_news_still_gets_the_guard(self):
        engine, _desktop, model = engine_with("The latest champion is Max.", "I think it was Max.", routes=False)
        result = engine.respond("who is the current F1 champion right now?")
        self.assertIn("too recent", result.text)


class CliLineTests(unittest.TestCase):
    def test_startup_flags(self):
        engine, _desktop, model = engine_with()
        self.assertEqual(engine.respond("--no-memory").text,
                         "That's a startup option. Restart with: python -m aqua chat --engine new --no-memory")
        self.assertEqual(engine.respond("--no-lookup").text,
                         "That's a startup option. Restart with: python -m aqua chat --engine new --no-lookup")
        self.assertEqual(model.requests, [])

    def test_memory_command_lines(self):
        store = temp_store()
        store.add_fact("I live in Pune")
        engine, _desktop, model = engine_with(store=store, routes=False)
        self.assertEqual(engine.respond("aqua memory forget f1").text, "Forgot that you live in Pune.")
        store.add_fact("My dog is called Max")
        self.assertEqual(engine.respond("python -m aqua memory clear").outcome, "confirm")
        engine.respond("yes")
        self.assertEqual(store.entries(), [])
        self.assertIn("aqua memory forget f1", engine.respond("aqua memory forget").text)

    def test_slash_commands(self):
        self.assertEqual(chat.memory_slash("/memory"), "aqua memory list")
        self.assertEqual(chat.memory_slash("/memory forget f2"), "aqua memory forget f2")
        self.assertEqual(chat.memory_slash("/memory clear"), "aqua memory clear")
        self.assertIsNone(chat.memory_slash("/clear"))


if __name__ == "__main__":
    unittest.main()
