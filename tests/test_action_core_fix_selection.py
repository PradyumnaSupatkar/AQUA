"""Fix 3: a fallback tool set when no keyword matches, and the speech-recognition hint."""

import unittest

from aqua.agent.prompt import SYSTEM_PROMPT
from aqua.agent.selection import CORE_TOOLS, FALLBACK_TOOLS, MAX_VISIBLE_TOOLS, matched_groups, select_tools
from aqua.tools import build_registry

from action_core_helpers import call, make_engine


class FallbackSelectionTests(unittest.TestCase):
    registry = build_registry()

    def test_unmatched_requests_get_the_likely_actions(self):
        for text in ("sign me out", "right hello I am aqua into the notepad",
                     "I'd like somewhere to jot down a few thoughts", "hello there"):
            self.assertEqual(matched_groups(text), (), text)
            names = select_tools(text, self.registry)
            self.assertEqual(names, (*CORE_TOOLS, *FALLBACK_TOOLS), text)
            self.assertLessEqual(len(names), MAX_VISIBLE_TOOLS)

    def test_matched_requests_keep_their_groups_only(self):
        names = select_tools("turn the volume down", self.registry)
        self.assertIn("volume", names)
        self.assertNotIn("power", names)

    def test_fallback_skips_missing_tools_and_negations_still_hide_everything(self):
        no_lookup = build_registry(lookup=False)
        self.assertNotIn("look_up", select_tools("hello there", no_lookup))
        self.assertEqual(select_tools("Don't sign me out", self.registry), ())

    def test_prompt_mentions_misheard_words(self):
        self.assertIn("mishear", SYSTEM_PROMPT)
        self.assertIn('"paws" for "pause"', SYSTEM_PROMPT)


class FallbackFlowTests(unittest.TestCase):
    def test_sign_me_out_reaches_the_power_confirmation(self):
        engine, desktop, model = make_engine([call("power", action="sign_out")], "Signing you out.")
        asked = engine.respond("sign me out")
        self.assertIn("power", model.requests[0]["tools"])
        self.assertEqual(asked.text, "Sign out of Windows? Unsaved work may be lost. Say yes or no.")
        engine.respond("yes")
        self.assertEqual(desktop.power, [("sign_out", 0)])

    def test_plain_chat_still_streams_live(self):
        engine, _, _ = make_engine("Hi there. Nice to hear from you.")
        chunks = []
        engine.respond("hello there", on_text=chunks.append)
        self.assertEqual(chunks, ["Hi there. ", "Nice to hear from you."])  # Streamed sentence by sentence.


if __name__ == "__main__":
    unittest.main()
