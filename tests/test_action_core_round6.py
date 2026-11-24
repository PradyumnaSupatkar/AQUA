"""Live hands-free findings (Parakeet): replies describe only this turn's actions, and youtube seek
mode follows the user's words (absolute "jump to 1:30" vs relative "skip ahead 30 seconds")."""

import unittest

from aqua.browser.fake import FakeBrowserBridge
from aqua.tools.fake import FakeDesktop

from action_core_helpers import call, make_engine

RECAP = ("I have typed 'this is a test' into Notepad as requested. "
         "Would you like me to type 'meeting at noon' as well?")


class EarlierActionRecapTests(unittest.TestCase):
    """The exact live case: 'this is a test' was typed in an EARLIER turn; this turn ("Well Right meeting
    at noon", 0 tool operations) re-reported that typing as new and asked instead of typing."""

    def _after_earlier_typing(self, *rounds):
        desktop = FakeDesktop()
        notepad = desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
        engine, _, model = make_engine([call("type_text", text="this is a test", app="Notepad")], *rounds,
                                       desktop=desktop)
        engine.respond("write this is a test into notepad")
        self.assertEqual(desktop.text[notepad.handle], "this is a test")
        return engine, desktop, model

    def test_a_recap_inside_an_offer_is_never_said(self):
        engine, _desktop, _model = self._after_earlier_typing(RECAP, "Shall I type it?")
        result = engine.respond("Well Right meeting at noon")
        self.assertNotIn("I have typed", result.text)
        self.assertNotIn("this is a test", result.text)
        self.assertEqual(result.task.operations, ())

    def test_a_recap_inside_an_offer_that_names_the_tool_is_dropped_from_the_question(self):
        engine, desktop, _model = self._after_earlier_typing(
            RECAP, [call("type_text", text="meeting at noon", app="Notepad", request="unsure")])
        asked = engine.respond("Well Right meeting at noon")
        self.assertEqual(asked.outcome, "confirm")
        self.assertNotIn("I have typed", asked.text)
        engine.respond("yes")
        self.assertTrue(desktop.text[next(iter(desktop.text))].endswith("meeting at noon"))

    def test_a_plain_recap_claim_with_no_tool_this_turn_is_still_caught(self):
        engine, _desktop, model = self._after_earlier_typing(
            "I have typed 'this is a test' into Notepad as requested.", "I didn't type anything this time.")
        result = engine.respond("Well Right meeting at noon")
        self.assertIn("no tool was called", model.requests[-1]["messages"][-1]["content"])  # The claim nudge.
        self.assertEqual(result.text, "I didn't type anything this time.")

    def test_prompt_limits_claims_to_this_request(self):
        from aqua.agent.prompt import SYSTEM_PROMPT
        self.assertIn("tool result of THIS request", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
