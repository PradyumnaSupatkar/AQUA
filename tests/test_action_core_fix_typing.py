"""Fix 2: dictated text is typed at once; text the model wrote is read back and confirmed;
launching an unnamed app only to type into it asks first."""

import unittest

from aqua.agent.context import ToolContext
from aqua.agent.policy import CONFIRM, RUN, Policy
from aqua.tools import build_registry
from aqua.tools.app_index import AppIndex
from aqua.tools.fake import FakeDesktop
from aqua.tools.input import dictated, readback

from action_core_helpers import call, make_engine

POEM = "Soft rain on the window, grey skies hum along, the garden drinks slowly"


def notepad_desktop():
    desktop = FakeDesktop()
    notepad = desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
    return desktop, notepad


class DictationTests(unittest.TestCase):
    def test_users_own_words_count_as_dictation(self):
        for text, request in (("Hello I am Aqua", "Open Notepad and write Hello I am Aqua"),
                              ("Hello, I am Aqua.", "right hello I am aqua into the notepad"),
                              ("Don't open Chrome.", 'Type "Don\'t open Chrome." into Notepad'),
                              ("shopping list", "Open Notepad, write shopping list, then minimise it")):
            self.assertTrue(dictated(text, request), text)
        for text, request in ((POEM, "write a short poem about rain into Notepad"),
                              ("Hello, world!", "Write something into my open Notepad window."),
                              ("My name is Sam.", "Put that in there."),
                              ("Hello", "help")):
            self.assertFalse(dictated(text, request), text)

    def test_readback_is_shortened_for_long_text(self):
        long_text = "word " * 100
        self.assertTrue(readback(long_text).endswith("(100 words)"))
        self.assertEqual(readback("short"), "short")


class TypingFlowTests(unittest.TestCase):
    def test_dictated_text_is_typed_immediately(self):
        desktop, notepad = notepad_desktop()
        engine, _, _ = make_engine([call("type_text", text="meeting at noon", app="Notepad")], "Typed it.",
                                   desktop=desktop)
        result = engine.respond("write meeting at noon into Notepad")
        self.assertEqual(result.outcome, "action")
        self.assertEqual(desktop.text, {notepad.handle: "meeting at noon"})

    def test_poem_is_read_back_then_typed_on_yes(self):
        desktop, notepad = notepad_desktop()
        engine, _, _ = make_engine([call("type_text", text=POEM, app="Notepad")], "Typed the poem into Notepad.",
                                   desktop=desktop)
        asked = engine.respond("write a short poem about rain into Notepad")
        self.assertEqual(asked.outcome, "confirm")
        # Over 12 words: summarised aloud, the full text shown.
        self.assertEqual(asked.text, "I'll type a short poem about rain into Notepad, 13 words. Go ahead?")
        self.assertEqual(asked.detail, POEM)
        self.assertEqual(desktop.typed, [])
        self.assertTrue(engine.awaiting_answer)  # Hands-free opens its answer window.
        self.assertEqual(engine.respond("yes").text, "Typed your text into Notepad.")  # Host-written after the yes.
        self.assertEqual(desktop.text, {notepad.handle: POEM})

    def test_poem_declined_types_nothing(self):
        desktop, _ = notepad_desktop()
        engine, _, _ = make_engine([call("type_text", text=POEM, app="Notepad")], desktop=desktop)
        engine.respond("write a short poem about rain into Notepad")
        self.assertEqual(engine.respond("no").text, "Okay, I won't.")
        self.assertEqual(desktop.typed, [])

    def test_invented_text_for_write_something_asks_first(self):
        desktop, _ = notepad_desktop()
        engine, _, _ = make_engine([call("type_text", text="Hello, world!", app="Notepad")], desktop=desktop)
        result = engine.respond("Write something into my open Notepad window.")
        self.assertEqual(result.outcome, "confirm")
        self.assertIn("'Hello, world!'", result.text)
        self.assertEqual(desktop.typed, [])


class LaunchToTypeTests(unittest.TestCase):
    def context(self, desktop, request):
        ctx = ToolContext(desktop=desktop, apps=AppIndex(desktop.app_entries, clock=desktop.monotonic), policy=Policy())
        ctx.begin_request(request)
        return ctx

    def test_unnamed_app_for_typing_is_confirmed(self):
        launch = build_registry().get("launch_app")
        ctx = self.context(FakeDesktop(), "Please type Hello from Aqua.")
        decision = ctx.policy.decide(launch, {"app": "Notepad", "request": "asked"}, ctx)
        self.assertEqual((decision.action, decision.question), (CONFIRM, "Open Notepad to type into it? Say yes or no."))

    def test_named_app_or_non_typing_request_launches(self):
        launch = build_registry().get("launch_app")
        for request, app in (("Open Notepad and write hello", "Notepad"), ("write hello in note pad", "Notepad"),
                             ("type hello in vs code", "VS Code"), ("I feel like programming", "VS Code")):
            ctx = self.context(FakeDesktop(), request)
            self.assertEqual(ctx.policy.decide(launch, {"app": app, "request": "asked"}, ctx).action, RUN, request)

    def test_already_open_app_is_not_a_guess(self):
        desktop, _ = notepad_desktop()
        launch = build_registry().get("launch_app")
        ctx = self.context(desktop, "Please type Hello from Aqua.")
        self.assertEqual(ctx.policy.decide(launch, {"app": "Notepad", "request": "asked"}, ctx).action, RUN)

    def test_launch_then_type_after_yes(self):
        engine, desktop, _ = make_engine([call("launch_app", app="Notepad")], [call("type_text", text="Hello from Aqua")],
                                         "Opened Notepad and typed your text.")
        asked = engine.respond("Please type Hello from Aqua.")
        # Round 4: nowhere to type, so the host asks where (the model's guess at Notepad is never needed).
        self.assertEqual(asked.text, "Which app should I type that into?")
        self.assertEqual(desktop.launched, [])
        engine.respond("Notepad")
        self.assertEqual(desktop.launched, ["Notepad"])
        self.assertEqual(list(desktop.text.values()), ["Hello from Aqua"])


if __name__ == "__main__":
    unittest.main()
