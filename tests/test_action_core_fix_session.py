"""Group A: explanation questions never act (also deep in a session), device state comes
from a live reading every time, follow-ups keep their tools, tool-call text is never spoken."""

import json
from pathlib import Path
import unittest

from aqua.agent.selection import select_tools, selected_groups, wants_explanation
from aqua.brain.conversation import Conversation
from aqua.tools import build_registry
from aqua.tools.fake import FakeDesktop
from aqua.tools.system import device_status

from action_core_helpers import call, make_engine
from tools import benchmark_agent as bench

LONG = json.loads((Path(__file__).resolve().parents[1] / "benchmarks" / "agent-v1" / "cases-long.json").read_bytes())
HISTORY = LONG["cases"][0]["setup"]["tool_history"]
ACTION_TOOLS = {"launch_app", "type_text", "undo_last", "lock_screen", "power", "volume", "media", "window_action",
                "close_app", "force_quit_app", "brightness", "open_url", "web_search"}


def long_engine(*rounds, **options):
    engine, desktop, model = make_engine(*rounds, **options)
    bench.replay_tool_history(engine.conversation, HISTORY)
    return engine, desktop, model


class ExplanationQuestionTests(unittest.TestCase):
    registry = build_registry()

    def test_how_what_why_questions_want_explanations(self):
        for text in ("How do I lock my screen?", "how do I lock my screen", "how can I restart my PC",
                     "What does force quit do?", "Why is my screen locking?", "Do you know how to restart a PC?",
                     "can you explain how to mute the sound", "is there a way to lock the screen faster"):
            self.assertTrue(wants_explanation(text), text)
        for text in ("lock my screen", "How about opening Spotify", "why don't you open Chrome", "open Chrome",
                     "would you be able to open Chrome?"):
            self.assertFalse(wants_explanation(text), text)

    def test_they_see_only_read_only_tools(self):
        names = select_tools("How do I lock my screen?", self.registry)
        self.assertFalse(set(names) & ACTION_TOOLS)
        self.assertIn("get_time", select_tools("what time is it", self.registry))
        self.assertIn("get_battery", select_tools("how much battery do I have", self.registry))
        self.assertIn("device_status", select_tools("what's the volume at?", self.registry))
        self.assertIn("lock_screen", select_tools("How do I lock my screen?", self.registry, question_guard=False))

    def test_lock_question_deep_in_a_session_does_not_lock(self):
        engine, desktop, model = long_engine([call("lock_screen")], "Press Windows key and L to lock it.")
        result = engine.respond("how do I lock my screen?")
        self.assertFalse(desktop.locked)
        self.assertNotIn("lock_screen", model.requests[0]["tools"])
        self.assertGreater(len(model.requests[0]["messages"]), 10)  # The long history is in the prompt.
        self.assertEqual(result.task.operations[0].status, "invalid")

    def test_an_offer_in_the_explanation_becomes_a_host_question(self):
        engine, desktop, _ = long_engine("Press Windows+L. Would you like me to lock it now?", [call("lock_screen")],
                                         "Locked the screen.")
        asked = engine.respond("How do I lock my screen?")
        self.assertEqual(asked.text, "Press Windows+L. Lock the screen? Say yes or no.")
        self.assertFalse(desktop.locked)
        engine.respond("yes")
        self.assertTrue(desktop.locked)


class DeviceStateTests(unittest.TestCase):
    def test_status_tool_reads_the_desktop(self):
        engine, desktop, _ = make_engine()
        outcome = device_status.run({}, engine.context)
        self.assertEqual(outcome.data, {"volume": 50, "muted": False, "brightness": 70, "playback": "playing"})
        self.assertIn("volume 50%", outcome.message)

    def test_battery_claim_without_a_reading_is_retried(self):
        engine, _, model = long_engine("Your battery is at 85% and charging.", [call("get_battery")],
                                       "Your battery is at 76%.")
        result = engine.respond("how much battery do I have")
        self.assertEqual(result.text, "Your battery is at 76%.")
        self.assertIn("no tool was called", model.requests[1]["messages"][-1]["content"])

    def test_check_again_reads_again_and_never_repeats_an_old_value(self):
        engine, _, model = long_engine([call("get_battery")], "Your battery is at 76%.",
                                       "It is still 76% and charging.", "Battery is at 76%.")
        engine.respond("how much battery do I have")
        result = engine.respond("check again")
        self.assertIn("get_battery", model.requests[2]["tools"])  # Inherited from the previous request.
        self.assertEqual(result.text, "Sorry, I couldn't do that.")  # Stated a value twice with no reading.

    def test_volume_brightness_playback_claims_need_a_reading(self):
        from aqua.agent.loop import unsupported_claim
        for reply, tools in (("The volume is at 40%.", {"device_status", "volume"}),
                             ("Brightness is at 60%.", {"device_status", "brightness"}),
                             ("The music is currently paused.", {"device_status", "media"}),
                             ("Your battery is at 85%.", {"get_battery"})):
            self.assertTrue(unsupported_claim(reply, set()), reply)
            self.assertFalse(unsupported_claim(reply, tools), reply)
        self.assertFalse(unsupported_claim("It is 2:05 PM.", set()))  # The time is in the host context.


class FollowUpTests(unittest.TestCase):
    def test_follow_ups_inherit_the_previous_groups(self):
        self.assertEqual(selected_groups("check again", ("system",)), ("system",))
        self.assertEqual(selected_groups("turn the volume down", ("system",)), ("audio",))
        self.assertEqual(selected_groups("hello", ()), ())

    def test_stale_yes_after_sign_out_question_never_acts(self):
        engine, desktop, model = make_engine([call("power", action="sign_out")], "It is 3 PM.")
        engine.respond("sign me out")
        engine.respond("what time is it")
        result = engine.respond("yes")  # Stale: no pending action any more.
        self.assertEqual(desktop.power, [])
        self.assertEqual(result.text, "There's nothing waiting for a yes.")


class BareAnswerTests(unittest.TestCase):
    def test_bare_no_with_nothing_pending_gets_a_host_reply(self):
        engine, desktop, model = make_engine("It does sound quiet.")
        engine.respond("it's very quiet in here")
        result = engine.respond("no")
        self.assertEqual((result.text, len(model.requests)), ("Okay, there's nothing to cancel.", 1))
        self.assertEqual(desktop.volume, 50)

    def test_bare_yes_with_nothing_pending_gets_a_host_reply(self):
        engine, _, model = make_engine("The volume is at 50%.", "The volume is at 50%.")
        engine.respond("turn it up?")
        before = len(model.requests)
        result = engine.respond("yes")
        self.assertEqual(result.text, "There's nothing waiting for a yes.")
        self.assertEqual(len(model.requests), before)  # The "yes" never reached the model.

    def test_yes_to_aquas_own_question_reaches_the_model_without_action_tools(self):
        engine, _, model = make_engine("Would you like a joke?", "Why did the PC sneeze? It had a virus.")
        engine.respond("tell me something fun")
        engine.respond("yes")
        self.assertFalse(set(model.requests[-1]["tools"]) & ACTION_TOOLS)
        self.assertEqual(len(model.requests), 2)

    def test_status_reading_is_offered_for_questions_not_commands(self):
        registry = build_registry()
        self.assertIn("device_status", select_tools("what's the volume at?", registry))
        self.assertIn("device_status", select_tools("is the music playing", registry))
        for command in ("it's too loud", "turn it up and play the music", "it's way too bright in here"):
            self.assertNotIn("device_status", select_tools(command, registry), command)


class ToolTextTests(unittest.TestCase):
    def test_written_out_tool_call_is_never_spoken(self):
        engine, desktop, _ = make_engine('<tool_call>\n{"name": "web_search", "arguments": {"query": "pizza"}}\n</tool_call>')
        spoken = []
        result = engine.respond("Don't open Chrome", on_text=spoken.append)
        self.assertEqual(result.text, "Okay.")
        self.assertNotIn("<tool_call>", "".join(spoken))
        self.assertEqual(desktop.opened_urls, [])

    def test_written_out_call_to_a_visible_tool_still_runs_through_the_policy(self):
        engine, desktop, _ = make_engine('{"name": "volume", "arguments": {"action": "mute"}}', "Muted.")
        self.assertEqual(engine.respond("hello there, mute please").outcome, "confirm")  # No request: unsure.

    def test_ordinary_replies_still_stream(self):
        engine, _, _ = make_engine("Hello! Nice to meet you.")
        spoken = []
        engine.respond("hello there", on_text=spoken.append)
        self.assertEqual("".join(spoken), "Hello! Nice to meet you.")


class HarnessHistoryTests(unittest.TestCase):
    def test_tool_history_is_replayed_as_tool_turns_within_limits(self):
        conversation = Conversation()
        bench.replay_tool_history(conversation, HISTORY)
        roles = [message["role"] for message in conversation.history]
        self.assertIn("tool", roles)
        self.assertLessEqual(conversation.turn_count, 8)  # Normal session limits still apply.
        self.assertEqual(len(HISTORY), 15)


if __name__ == "__main__":
    unittest.main()
