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


class FactualAnswerTests(unittest.TestCase):
    def test_facts_are_not_action_claims(self):
        from aqua.agent.loop import unsupported_claim
        for fact in ("Jane Austen wrote Pride and Prejudice.", "The Eiffel Tower was opened in 1889.",
                     "Hamilton started racing at eight.", "The shop closed in 2010."):
            self.assertFalse(unsupported_claim(fact, set()), fact)

    def test_claims_about_the_pc_still_count(self):
        from aqua.agent.loop import unsupported_claim
        for claim in ("I have written the poem.", "The text has been written into Notepad.", "Notepad is now open.",
                      "Your music has been paused.", "Battery is at 85%.", "Nothing is playing right now."):
            self.assertTrue(unsupported_claim(claim, set()), claim)

    def test_factual_answer_is_spoken_unchanged(self):
        engine, _, model = make_engine("Jane Austen wrote Pride and Prejudice.")
        self.assertEqual(engine.respond("Who wrote Pride and Prejudice?").text, "Jane Austen wrote Pride and Prejudice.")
        self.assertEqual(len(model.requests), 1)


class StreamingHoldTests(unittest.TestCase):
    def test_a_streamed_claim_is_held_until_checked(self):
        engine, desktop, model = make_engine("Sure thing. I have opened Notepad for you.",
                                             [call("launch_app", app="Notepad")], "Opened Notepad.")
        spoken = []
        result = engine.respond("hello there, Notepad please", on_text=spoken.append)
        self.assertEqual(spoken[0], "Sure thing. ")  # Safe sentence streamed at once.
        self.assertNotIn("I have opened Notepad for you.", "".join(spoken))
        self.assertEqual(desktop.launched, ["Notepad"])
        self.assertEqual(result.text, "Sure thing. Opened Notepad.")

    def test_a_held_promise_without_any_action_becomes_honest(self):
        engine, desktop, _ = make_engine("I will open Notepad for you.", "I will open Notepad for you.")
        spoken = []
        result = engine.respond("hello there, my notes thing", on_text=spoken.append)
        self.assertEqual(desktop.launched, [])
        self.assertEqual(result.text, "Sorry, I didn't manage to do that.")
        self.assertNotIn("I will open", "".join(spoken))

    def test_ordinary_chat_streams_every_sentence(self):
        engine, _, _ = make_engine("Paris is the capital of France. It is lovely in spring.")
        spoken = []
        engine.respond("chat with me about spring in Paris", on_text=spoken.append)
        self.assertEqual(spoken, ["Paris is the capital of France. ", "It is lovely in spring."])


class BrightnessUndoTests(unittest.TestCase):
    def test_brightness_change_is_undoable_and_undo_is_offered(self):
        engine, desktop, model = make_engine([call("brightness", action="down")], "Brightness is now 60%.",
                                             "No action was requested.", [call("undo_last")], "")
        engine.respond("turn the brightness down")
        self.assertEqual(desktop.brightness, 60)
        self.assertEqual(engine.context.undo.peek().description, "the brightness change")
        result = engine.respond("undo that")
        self.assertIn("undo_last", model.requests[2]["tools"])
        self.assertIn("undo_last would undo: the brightness change", model.requests[2]["messages"][-1]["content"])
        self.assertIn("call undo_last now", model.requests[3]["messages"][-1]["content"])  # The nudge.
        self.assertEqual(desktop.brightness, 70)
        self.assertEqual(result.text, "Put the brightness back to 70%.")

    def test_no_nudge_when_there_is_nothing_to_undo(self):
        engine, _, model = make_engine("There is nothing to undo.")
        engine.respond("undo that")
        self.assertEqual(len(model.requests), 1)


class SiteSearchTests(unittest.TestCase):
    def test_site_urls_are_fixed_templates_with_an_encoded_query(self):
        from aqua.tools.web import search_url
        self.assertEqual(search_url("Sidemen Sunday", "youtube"),
                         "https://www.youtube.com/results?search_query=Sidemen+Sunday")
        self.assertEqual(search_url("a&b=c/../x", "google"), "https://www.google.com/search?q=a%26b%3Dc%2F..%2Fx")
        self.assertEqual(search_url("rust", "github"), "https://github.com/search?q=rust")
        self.assertTrue(search_url("pizza").startswith("https://duckduckgo.com/?q="))

    def test_search_on_youtube_through_the_engine(self):
        engine, desktop, _ = make_engine([call("web_search", query="Sidemen Sunday", site="youtube")],
                                         "Searched YouTube for Sidemen Sunday.")
        engine.respond("search Sidemen Sunday on YouTube")
        self.assertEqual(desktop.opened_urls, ["https://www.youtube.com/results?search_query=Sidemen+Sunday"])

    def test_unknown_site_is_a_schema_error_not_a_url(self):
        from aqua.agent.registry import SchemaError, validate_arguments
        schema = build_registry().get("web_search").parameters
        with self.assertRaises(SchemaError):
            validate_arguments(schema, {"query": "x", "site": "evil.example", "request": "asked"})


if __name__ == "__main__":
    unittest.main()
