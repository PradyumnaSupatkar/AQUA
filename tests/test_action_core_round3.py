"""Round 3 fixes: polite requests, partial requests, claim check on facts, bare yes/no,
streaming holds, brightness undo, site search, and the live re-test failures (items 9-13)."""

import unittest

from aqua.agent.selection import select_tools, wants_explanation
from aqua.tools import build_registry
from aqua.tools.fake import FakeDesktop

from action_core_helpers import call, make_engine


class PoliteRequestTests(unittest.TestCase):
    def test_polite_forms_are_not_explanation_questions(self):
        registry = build_registry()
        for text in ("could you bring up my coding environment?", "would you be able to open Chrome?",
                     "can you open Notepad", "don't forget to open Notepad", "please open Spotify"):
            self.assertFalse(wants_explanation(text), text)
            self.assertIn("launch_app", select_tools(text, registry), text)

    def test_prompt_says_polite_forms_are_requests(self):
        from aqua.agent.prompt import SYSTEM_PROMPT
        self.assertIn('"would you be able to ..."', SYSTEM_PROMPT)
        self.assertIn("mean do it now", SYSTEM_PROMPT)

    def test_a_promise_without_a_tool_call_gets_one_nudge(self):
        engine, desktop, model = make_engine("I will open Notepad for you.", [call("launch_app", app="Notepad")],
                                             "Opened Notepad.")
        result = engine.respond("don't forget to open Notepad")
        self.assertEqual(desktop.launched, ["Notepad"])
        self.assertEqual(result.text, "Opened Notepad.")
        self.assertIn("you said you would do it", model.requests[1]["messages"][-1]["content"])

    def test_a_repeated_promise_becomes_an_honest_reply(self):
        engine, desktop, _ = make_engine("Let me open Chrome for you.", "I'll open Chrome now.")
        result = engine.respond("would you be able to open Chrome?")
        self.assertEqual((desktop.launched, result.text), ([], "Sorry, I didn't manage to do that."))

    def test_promises_after_a_real_action_are_fine(self):
        engine, desktop, _ = make_engine([call("launch_app", app="Notepad")], "Opened Notepad; I'll keep it open.")
        self.assertEqual(engine.respond("open Notepad").text, "Opened Notepad; I'll keep it open.")


class PartialRequestTests(unittest.TestCase):
    def test_the_part_that_cannot_be_done_is_kept_in_the_reply(self):
        engine, desktop, _ = make_engine([call("launch_app", app="Chrome")],
                                         "I have opened Chrome. I can't delete files, because I have no tool for that. "
                                         "Anything else?")
        result = engine.respond("Open Chrome and delete my files.")
        self.assertEqual(desktop.launched, ["Google Chrome"])
        self.assertEqual(result.text, "I have opened Chrome. I can't delete files, because I have no tool for that.")

    def test_the_reminder_asks_for_the_part_that_cannot_be_done(self):
        engine, _, model = make_engine([call("launch_app", app="Chrome")], "Opened Chrome.")
        engine.respond("Open Chrome and delete my files.")
        self.assertIn("say which part and why", model.tool_results(1)[-1]["content"])

    def test_plain_one_sentence_replies_stay_one_sentence(self):
        engine, _, _ = make_engine([call("volume", action="mute")], "Muted the sound. Enjoy the quiet!")
        self.assertEqual(engine.respond("mute").text, "Muted the sound.")


if __name__ == "__main__":
    unittest.main()
