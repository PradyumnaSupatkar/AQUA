"""Each host guard can be switched off for evaluation, and is on by default."""

import json
import unittest

from aqua.agent.guards import DEFAULT_GUARDS, GUARD_NAMES, without
from aqua.tools import build_registry
from aqua.tools.knowledge import WikipediaClient

from action_core_helpers import call, make_engine


def lookup_registry():
    return build_registry(wikipedia=WikipediaClient(lambda params: {"query": {"search": []}}))


class GuardSwitchTests(unittest.TestCase):
    def test_all_guards_default_on_and_names_are_checked(self):
        self.assertTrue(all(getattr(DEFAULT_GUARDS, name) for name in GUARD_NAMES))
        self.assertEqual(set(GUARD_NAMES), {"negation_guard", "claim_retry", "cant_retry", "citation_strip",
                                            "knowledge_cutoff", "restate_request"})
        self.assertFalse(without("claim_retry").claim_retry)
        with self.assertRaises(ValueError):
            without("no_such_guard")

    def test_negation_guard(self):
        on, _, model = make_engine("Okay.")
        on.respond("Don't open Chrome")
        self.assertEqual(model.requests[0]["tools"], [])
        off, _, model = make_engine("Okay.", guards=without("negation_guard"))
        off.respond("Don't open Chrome")
        self.assertIn("launch_app", model.requests[0]["tools"])

    def test_claim_retry(self):
        on, desktop, model = make_engine("The sound has been muted.", "I did not mute it.")
        on.respond("mute")
        self.assertEqual(len(model.requests), 2)
        off, _, model = make_engine("The sound has been muted.", guards=without("claim_retry"))
        self.assertEqual(off.respond("mute").text, "The sound has been muted.")
        self.assertEqual(len(model.requests), 1)

    def test_cant_retry(self):
        # On: the model's own offer becomes a host confirmation. Off: its question is just spoken.
        on, desktop, model = make_engine("Would you like me to lock the screen?", [call("lock_screen")])
        self.assertEqual(on.respond("how do I lock my screen?").outcome, "confirm")
        self.assertFalse(desktop.locked)
        off, _, model = make_engine("Would you like me to lock the screen?", guards=without("cant_retry"))
        self.assertEqual(off.respond("how do I lock my screen?").outcome, "answer")
        self.assertEqual(len(model.requests), 1)

    def test_citation_strip(self):
        on, _, _ = make_engine("According to Wikipedia, it is Canberra.", "According to Wikipedia, Canberra.",
                               registry=lookup_registry())
        self.assertEqual(on.respond("What is the capital of Australia?").text, "Canberra.")
        off, _, model = make_engine("According to Wikipedia, it is Canberra.", registry=lookup_registry(),
                                    guards=without("citation_strip"))
        self.assertEqual(off.respond("What is the capital of Australia?").text, "According to Wikipedia, it is Canberra.")
        self.assertEqual(len(model.requests), 1)

    def test_knowledge_cutoff(self):
        # 2025 is before "this year" (2026 in the fixed clock) but after the model's training.
        on, _, model = make_engine("Norris won.", "Norris won.", registry=lookup_registry())
        on.respond("Who won the 2025 Formula One World Championship?")
        self.assertEqual(model.requests[1]["tools"], ["look_up"])
        off, _, model = make_engine("Norris won.", registry=lookup_registry(), guards=without("knowledge_cutoff"))
        off.respond("Who won the 2025 Formula One World Championship?")
        self.assertEqual(len(model.requests), 1)

    def test_restate_request(self):
        on, _, model = make_engine([call("volume", action="mute")], "Muted.")
        on.respond("mute")
        self.assertIn("next", json.loads(model.tool_results()[-1]["content"]))
        off, _, model = make_engine([call("volume", action="mute")], "Muted.", guards=without("restate_request"))
        off.respond("mute")
        self.assertNotIn("next", json.loads(model.tool_results()[-1]["content"]))


if __name__ == "__main__":
    unittest.main()
