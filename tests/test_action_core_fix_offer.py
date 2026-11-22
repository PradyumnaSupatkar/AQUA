"""Fix 1: "request: asked|unsure" on action tools, and the model's own offers
("Would you like me to ...?") become host confirmations that only a yes runs."""

import json
import unittest

from aqua.agent.policy import CONFIRM, RUN, Policy
from aqua.agent.registry import REQUEST_PARAMETER
from aqua.tools import build_registry
from aqua.tools.fake import FakeDesktop

from action_core_helpers import call, make_engine

READ_ONLY = {"get_time", "get_battery", "list_windows", "inspect_window", "look_up"}


class RequestArgumentTests(unittest.TestCase):
    def test_every_action_tool_requires_request_and_read_only_tools_do_not(self):
        registry = build_registry()
        for name in registry.names:
            parameters = registry.get(name).parameters
            if name in READ_ONLY:
                self.assertNotIn("request", parameters["properties"], name)
            else:
                self.assertEqual(parameters["properties"]["request"], REQUEST_PARAMETER, name)
                self.assertIn("request", parameters["required"], name)

    def test_unsure_confirms_with_a_host_question_and_asked_runs(self):
        registry = build_registry()
        policy = Policy()
        lock = registry.get("lock_screen")
        self.assertEqual(policy.decide(lock, {"request": "asked"}, None).action, RUN)
        decision = policy.decide(lock, {"request": "unsure"}, None)
        self.assertEqual((decision.action, decision.question), (CONFIRM, "Lock the screen? Say yes or no."))
        volume = registry.get("volume")
        self.assertEqual(policy.decide(volume, {"action": "down", "request": "unsure"}, None).question,
                         "Turn the volume down? Say yes or no.")

    def test_unsure_call_waits_for_yes(self):
        engine, desktop, _ = make_engine([call("lock_screen", request="unsure")], "Locked the screen.")
        asked = engine.respond("people can see my screen, maybe lock it")
        self.assertEqual((asked.outcome, asked.text), ("confirm", "Lock the screen? Say yes or no."))
        self.assertFalse(desktop.locked)
        self.assertEqual(engine.respond("yes").text, "Locked the screen.")
        self.assertTrue(desktop.locked)

    def test_a_call_without_request_is_treated_as_unsure(self):
        engine, desktop, _ = make_engine([call("volume", action="mute", request=None)])
        self.assertEqual(engine.respond("mute").outcome, "confirm")
        self.assertFalse(desktop.muted)

    def test_direct_requests_still_act_immediately(self):
        engine, desktop, _ = make_engine([call("volume", action="mute")], "Muted.")
        self.assertEqual(engine.respond("mute").text, "Muted.")
        self.assertTrue(desktop.muted)


class OfferTests(unittest.TestCase):
    def test_offer_becomes_a_pending_confirmation_for_the_exact_tool(self):
        engine, desktop, model = make_engine(
            "Spotify is a popular music app. Would you like me to open it?",
            [call("launch_app", app="Spotify", request="asked")],  # Forced to "unsure" by the host.
            "Opened Spotify.")
        asked = engine.respond("Is Spotify a good app?")
        self.assertEqual(asked.outcome, "confirm")
        self.assertEqual(asked.text, "Spotify is a popular music app. Open Spotify? Say yes or no.")
        self.assertEqual(desktop.launched, [])
        self.assertIn('request \\"unsure\\"', json.dumps(model.requests[1]["messages"][-1]))
        self.assertEqual((engine.pending.tool, engine.pending.args),
                         ("launch_app", {"app": "Spotify", "request": "unsure"}))
        self.assertEqual(engine.respond("yes").text, "Opened Spotify.")
        self.assertEqual(desktop.launched, ["Spotify"])

    def test_no_declines_an_offer(self):
        engine, desktop, _ = make_engine("Would you like me to lower the volume?",
                                         [call("volume", action="down")])
        engine.respond("If the volume were lower, would I sleep better?")
        self.assertEqual(engine.respond("no").text, "Okay, I won't.")
        self.assertEqual(desktop.volume, 50)

    def test_offer_with_no_specific_action_keeps_the_question(self):
        engine, desktop, model = make_engine("Would you like me to help with anything else?",
                                             "Would you like me to help with anything else?")
        result = engine.respond("thanks, that's all for the music")
        self.assertEqual((result.outcome, result.text), ("answer", "Would you like me to help with anything else?"))
        self.assertIsNone(engine.pending)
        self.assertEqual(len(model.requests), 2)

    def test_offer_never_runs_without_a_yes_even_for_safe_tools(self):
        desktop = FakeDesktop()
        desktop.add_window("Untitled - Notepad", "notepad.exe")
        engine, _, _ = make_engine("Do you want me to minimise Notepad?",
                                   [call("window_action", action="minimise", window="Notepad")], desktop=desktop)
        result = engine.respond("Notepad is in the way of the windows")
        self.assertEqual(result.text, "Minimise Notepad? Say yes or no.")
        self.assertFalse(any(w.minimized for w in desktop.windows()))

    def test_plain_questions_are_not_offers(self):
        engine, _, model = make_engine("Would you like a joke?")
        self.assertEqual(engine.respond("tell me something fun").text, "Would you like a joke?")
        self.assertEqual(len(model.requests), 1)


if __name__ == "__main__":
    unittest.main()
