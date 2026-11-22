"""Fix 4: confirmations that end without a yes are reported as not done in the next
request, and the prompt asks the model to name the part of a request it could not do."""

import unittest

from aqua.agent.confirm import ConfirmationGate, PendingAction
from aqua.agent.prompt import SYSTEM_PROMPT, request_context
from aqua.tools.fake import FakeDesktop

from action_core_helpers import FIXED_NOW, Clock, call, make_engine


def spotify_desktop():
    desktop = FakeDesktop()
    desktop.add_window("Spotify Premium", "spotify.exe", front=False, pid=555)
    return desktop


def last_user(model):
    return model.requests[-1]["messages"][-1]["content"]


class DroppedConfirmationTests(unittest.TestCase):
    def test_replaced_confirmation_is_reported_as_not_done(self):
        engine, desktop, model = make_engine([call("force_quit_app", app="Spotify")], [call("launch_app", app="Notepad")],
                                             "Opened Notepad.", desktop=spotify_desktop())
        engine.respond("force quit Spotify")
        engine.respond("open notepad instead")
        self.assertIn("not done, because the user did not confirm it: force quit Spotify", model.requests[1]["messages"][-1]["content"])
        self.assertEqual((desktop.launched, desktop.terminated), (["Notepad"], []))

    def test_declined_confirmation_is_reported_once(self):
        engine, _, model = make_engine([call("force_quit_app", app="Spotify")], "Okay.", "Still fine.",
                                       desktop=spotify_desktop())
        engine.respond("force quit Spotify")
        engine.respond("no")
        engine.respond("what's up")
        self.assertIn("force quit Spotify", last_user(model))
        engine.respond("and now?")
        self.assertNotIn("not done", last_user(model))

    def test_expired_confirmation_is_reported(self):
        clock = Clock(0.0)
        engine, _, model = make_engine([call("power", action="restart")], "Hello.", clock=clock)
        engine.respond("restart the computer")
        clock.value = 61.0
        engine.respond("yes")  # Too late: a new request, with the note.
        self.assertIn("not done, because the user did not confirm it: restart", last_user(model))

    def test_confirmed_actions_and_clear_leave_no_note(self):
        gate = ConfirmationGate()
        gate.offer(PendingAction("lock_screen", {}, "Lock the screen?", 0.0, "lock", summary="lock the screen"))
        gate.answer("yes", 1.0)
        self.assertEqual(gate.take_dropped(), [])
        gate.offer(PendingAction("lock_screen", {}, "Lock the screen?", 0.0, "lock", summary="lock the screen"))
        gate.answer("something else", 1.0)
        gate.clear()
        self.assertEqual(gate.take_dropped(), [])

    def test_context_and_prompt_wording(self):
        context = request_context(FIXED_NOW, not_done=("force quit Spotify",))
        self.assertIn("do not propose it again unless the user asks", context)
        self.assertIn("say plainly which part you could not do", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
