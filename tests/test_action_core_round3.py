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


class InsertionCheckTests(unittest.TestCase):
    def test_text_inserted_at_the_caret_in_an_existing_document_passes(self):
        from aqua.tools.input import inserted
        old = "Old restored tab text. Second sentence."
        self.assertTrue(inserted(old, "Old restored tab text. hello I am Aqua Second sentence.", "hello I am Aqua "))
        self.assertTrue(inserted(old, old + "hello", "hello"))
        self.assertTrue(inserted("", "hello", "hello"))
        self.assertTrue(inserted("a\r\nb", "a\nXb", "X"))  # Line endings are normalised.
        self.assertTrue(inserted(None, "anything hello anything", "hello"))

    def test_garbled_or_extra_changes_fail(self):
        from aqua.tools.input import inserted
        old = "Old text."
        self.assertFalse(inserted(old, old + "hello i am aqua", "hello I am Aqua"))   # Shift lost
        self.assertFalse(inserted(old, old + "Hello aaaaaaaaa", "Hello I am Aqua"))   # Repeated packets
        self.assertFalse(inserted(old, "Old text changed.hello", "hello"))            # Something else changed

    def test_typing_into_a_notepad_that_already_has_text(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("*Restored notes - Notepad", "notepad.exe", front=False)
        desktop.text[notepad.handle] = "Notes Notepad restored from last time. "
        engine, _, _ = make_engine([call("type_text", text="hello I am your assistant", app="Notepad")],
                                   "Typed it.", desktop=desktop)
        result = engine.respond("Write hello I am your assistant into the notepad window")
        self.assertEqual(result.task.operations[0].status, "ok")


class DictationAcrossQuestionTests(unittest.TestCase):
    def _asked(self, *rounds):
        engine, desktop, model = make_engine([call("type_text", text="meeting at noon")], *rounds)
        asked = engine.respond("write meeting at noon")
        self.assertEqual(asked.text, "Which app should I type that into?")
        return engine, desktop, model

    def test_named_app_is_opened_and_the_kept_text_typed(self):
        engine, desktop, model = self._asked()
        result = engine.respond("notepad")
        self.assertEqual(desktop.launched, ["Notepad"])
        self.assertEqual(list(desktop.text.values()), ["meeting at noon"])
        self.assertEqual(result.text, "Opened Notepad and typed your text.")
        self.assertEqual(len(model.requests), 1)  # The host did it; the model could not drop the text.

    def test_spoken_variants_name_the_app(self):
        engine, desktop, _ = self._asked()
        engine.respond("in the note pad please")
        self.assertEqual(list(desktop.text.values()), ["meeting at noon"])

    def test_an_open_window_is_reused(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
        desktop.focus(1)
        engine, _, _ = make_engine([call("type_text", text="meeting at noon")], desktop=desktop)
        engine.respond("write meeting at noon")
        engine.respond("Notepad")
        self.assertEqual((desktop.launched, desktop.text), ([], {notepad.handle: "meeting at noon"}))

    def test_anything_else_goes_to_the_model_and_the_text_is_dropped(self):
        engine, desktop, model = self._asked("Okay, never mind.")
        engine.respond("actually forget it, what time is it")
        self.assertEqual(desktop.typed, [])
        self.assertEqual(len(model.requests), 2)
        self.assertIsNone(engine._awaiting_text)


LOCK_TEXT = '<tool_call>{"name": "lock_screen", "arguments": {"request": "asked"}}</tool_call>'


class HiddenToolTextTests(unittest.TestCase):
    def test_written_call_to_a_hidden_action_becomes_a_host_question(self):
        engine, desktop, _ = make_engine(LOCK_TEXT, "Locked.")
        spoken = []
        asked = engine.respond("hmm, tell me something nice", on_text=spoken.append)
        self.assertEqual(asked.text, "Lock the screen? Say yes or no.")
        self.assertNotIn("tool_call", "".join(spoken))
        self.assertFalse(desktop.locked)
        engine.respond("yes")
        self.assertTrue(desktop.locked)

    def test_buffered_reply_with_text_before_the_call_is_never_shown(self):
        engine, desktop, _ = make_engine("Sure. " + LOCK_TEXT)
        result = engine.respond("open Notepad")  # Command-like: buffered, and lock_screen was not shown.
        self.assertNotIn("tool_call", result.text)
        self.assertEqual(result.text, "Lock the screen? Say yes or no.")
        self.assertFalse(desktop.locked)

    def test_after_dont_requests_the_model_is_asked_for_plain_words(self):
        engine, desktop, model = make_engine(LOCK_TEXT, "Okay, I won't lock it.")
        result = engine.respond("don't lock the screen")
        self.assertEqual(result.text, "Okay, I won't lock it.")
        self.assertFalse(model.requests[1]["tools"])
        self.assertFalse(desktop.locked)
        self.assertIsNone(engine.pending)

    def test_a_second_written_call_ends_in_okay(self):
        engine, _, _ = make_engine('{"name": "nope_tool", "arguments": {}}', LOCK_TEXT)
        result = engine.respond("hello there")
        self.assertEqual(result.text, "Okay.")

    def test_a_hidden_read_only_tool_may_run(self):
        engine, _, _ = make_engine('<tool_call>{"name": "get_time", "arguments": {}}</tool_call>', "It's noon.")
        result = engine.respond("hello, nice weather")
        self.assertEqual(result.text, "It's noon.")
        self.assertEqual(result.task.operations[0].tool, "get_time")

    def test_tool_text_is_not_kept_in_history(self):
        engine, _, _ = make_engine(LOCK_TEXT, '{"name": "x"}')
        engine.respond("don't do anything, just chat")
        self.assertNotIn("tool_call", str(engine.conversation.history))
        self.assertNotIn('"name"', str(engine.conversation.history))


class DirectCommandTests(unittest.TestCase):
    def test_direct_commands_are_recognised(self):
        from aqua.agent.loop import direct_command
        for text in ("lock the screen", "Lock the screen.", "please lock my computer", "could you lock the screen?",
                     "would you be able to mute the sound", "don't forget to open Notepad", "Hey Aqua, pause it"):
            self.assertTrue(direct_command(text), text)
        for text in ("should I lock the screen?", "what does locking do?", "the volume, lock things, whatever",
                     "I might lock it later", "is the screen locked?"):
            self.assertFalse(direct_command(text), text)

    def test_lock_the_screen_runs_even_when_the_model_says_unsure(self):
        for request in ("unsure", None):
            engine, desktop, _ = make_engine([call("lock_screen", request=request)], "Locked the screen.")
            result = engine.respond("lock the screen")
            self.assertTrue(desktop.locked, request)
            self.assertEqual(result.outcome, "action")

    def test_unclear_requests_still_ask(self):
        engine, desktop, _ = make_engine([call("lock_screen", request="unsure")], "Fine.")
        result = engine.respond("I guess lock the screen, maybe?")
        self.assertEqual(result.outcome, "confirm")
        self.assertFalse(desktop.locked)

    def test_disruptive_tools_still_confirm(self):
        engine, desktop, _ = make_engine([call("power", action="shutdown", request="unsure")], "Fine.")
        self.assertEqual(engine.respond("shut down the computer").outcome, "confirm")
        self.assertEqual(desktop.power, [])


POEM = ("Rain taps softly on the glass,\nPuddles gather on the grass,\nClouds drift slowly, grey and wide,\n"
        "Rivers swell with every tide.")


class ReadBackWordingTests(unittest.TestCase):
    def test_what_is_typed_comes_from_the_request(self):
        from aqua.tools.input import what_to_type
        self.assertEqual(what_to_type("write a short poem about rain into Notepad"), "a short poem about rain")
        self.assertEqual(what_to_type("Could you type me an email to my boss in the Notepad window?"),
                         "an email to my boss")
        self.assertEqual(what_to_type("put a greeting into notepad"), "a greeting")
        self.assertEqual(what_to_type("hello there"), "")

    def test_long_text_is_summarised_aloud_and_shown_in_full(self):
        desktop = FakeDesktop()
        desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
        engine, _, _ = make_engine([call("type_text", text=POEM, app="Notepad")], "Typed it.", desktop=desktop)
        spoken = []
        asked = engine.respond("write a short poem about rain into Notepad", on_text=spoken.append)
        self.assertEqual(asked.text, "I'll type a short poem about rain into Notepad, 22 words, on 4 lines. Go ahead?")
        self.assertNotIn("Puddles", "".join(spoken))
        self.assertEqual(asked.detail, POEM)

    def test_short_text_is_read_in_full_with_nothing_extra_shown(self):
        desktop = FakeDesktop()
        desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
        engine, _, _ = make_engine([call("type_text", text="Hello from Aqua", app="Notepad")], "Typed it.",
                                   desktop=desktop)
        asked = engine.respond("put a greeting into notepad")
        self.assertEqual(asked.text, "I'll type into Notepad: 'Hello from Aqua'. Go ahead?")
        self.assertIsNone(asked.detail)

    def test_unknown_description_falls_back_to_the_text(self):
        from aqua.tools.input import read_back
        question, shown = read_back(POEM, "go on then", "", keep_lines=False)
        self.assertEqual(question, "I'll type the text, 22 words, as one line. Go ahead?")
        self.assertNotIn("\n", shown)

    def test_vs_code_is_a_plain_editor(self):
        desktop = FakeDesktop()
        code = desktop.add_window("Welcome - Visual Studio Code", "code.exe", front=False)
        engine, _, _ = make_engine([call("type_text", text=POEM, app="VS Code")], "Typed it.", desktop=desktop)
        engine.respond("write a poem about rain into VS Code")
        engine.respond("yes")
        self.assertEqual(desktop.text[code.handle], POEM)


if __name__ == "__main__":
    unittest.main()
