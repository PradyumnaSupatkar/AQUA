"""Live typed-chat findings (--engine new): typed numbers and capability denials, dead model-written
confirmations, open_settings for things Windows only lets the user change, and replaying a video."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from aqua import chat
from aqua.browser.fake import FakeBrowserBridge
from aqua.speech.postprocess import prepare_typed
from aqua.tools.youtube import seek_from_request

from action_core_helpers import call, make_engine
from test_action_core_wiring import Offline


def youtube_engine(*rounds, **tab):
    engine, desktop, model = make_engine(*rounds)
    bridge = FakeBrowserBridge(browsers=("brave",))
    bridge.add_tab("brave", **({"position": 200, "duration": 600} | tab))
    engine.context.browser = bridge
    return engine, desktop, model, bridge


class TypedNumberTests(unittest.TestCase):
    """Typed "jump to one minute thirty" got the same treatment as nothing: the number fix ran on voice only."""

    def test_typed_input_gets_the_spoken_number_fix(self):
        self.assertEqual(prepare_typed("jump to one minute thirty")[0], "jump to 1:30")
        self.assertEqual(prepare_typed("skip ahead thirty seconds")[0], "skip ahead 30 seconds")
        self.assertEqual(prepare_typed("set the volume to forty")[0], "set the volume to 40")

    def test_typed_dictation_and_plain_text_stay_as_typed(self):
        for text in ("type ten seconds into notepad", "write meet at two thirty in notepad", "Well, hello there",
                     "right, open notepad", "what is one plus one"):
            self.assertEqual(prepare_typed(text), (text, []), text)

    def test_the_seek_reads_number_words_itself(self):
        # Benchmark and any other caller that skips the front end still get a position, not a guess.
        self.assertEqual(seek_from_request("jump to one minute thirty"), {"to": "1:30"})
        self.assertEqual(seek_from_request("go back ten seconds"), {"seconds": -10})

    def test_word_time_seeks_to_the_position_even_with_the_models_seconds(self):
        engine, _desktop, _model, bridge = youtube_engine([call("youtube", action="seek", seconds=90)])
        result = engine.respond("jump to one minute thirty")
        self.assertEqual(bridge.effects, ["youtube:brave:seek=90"])
        self.assertIn("1:30", result.text)


class TypedChatTests(Offline):
    def test_text_chat_sends_the_fixed_text_and_shows_the_fix_with_diagnostics(self):
        engine, _desktop, model, bridge = youtube_engine([call("youtube", action="seek", to="1:30")])
        self.use_engine(engine)
        output = StringIO()
        with patch("builtins.input", side_effect=["jump to one minute thirty", "/quit"]), \
                redirect_stdout(output), redirect_stderr(StringIO()):
            chat.run_text_chat(engine="new", diagnostics=True)
        self.assertTrue(model.requests[0]["messages"][-1]["content"].startswith("jump to 1:30\n"))
        self.assertIn("Input fix: numbers: 'jump to one minute thirty' -> 'jump to 1:30'", output.getvalue())
        self.assertEqual(bridge.effects, ["youtube:brave:seek=90"])


class CapabilityDenialTests(unittest.TestCase):
    LIVE = "I cannot jump to a specific time as that is not a function available to me."

    def test_the_live_denial_gets_one_nudge_and_the_tool_runs(self):
        engine, _desktop, model, bridge = youtube_engine(self.LIVE, [call("youtube", action="seek", to="1:30")])
        result = engine.respond("jump to 1:30")
        self.assertEqual(bridge.effects, ["youtube:brave:seek=90"])
        nudge = model.requests[1]["messages"][-1]["content"]
        self.assertIn("you do have a tool for this: youtube", nudge)
        self.assertNotIn("cannot", result.text)

    def test_other_denial_wordings_are_nudged_too(self):
        for denial in ("I'm unable to change the volume.", "I don't have the ability to mute the sound.",
                       "Sorry, that tool is not available.", "I can’t turn the volume down."):
            engine, desktop, model = make_engine(denial, [call("volume", action="down")])
            engine.respond("turn the volume down")
            self.assertEqual(desktop.volume, 40, denial)

    def test_a_second_denial_is_said_and_nothing_runs(self):
        engine, desktop, model = make_engine("I can't mute that.", "I can't mute that app on its own.")
        result = engine.respond("mute spotify")
        self.assertEqual(result.text, "I can't mute that app on its own.")
        self.assertEqual(len(model.requests), 2)
        self.assertFalse(desktop.muted)

    def test_no_nudge_without_a_fitting_tool_or_for_questions_and_negations(self):
        for request in ("delete the files on my desktop", "how do I mute the sound?", "don't mute the sound",
                        "make Brave the default browser"):
            engine, _desktop, model = make_engine("I can't do that.", "I can't do that.")
            engine.respond(request)
            self.assertEqual(len(model.requests), 1, request)


COPIED = "Putting the PC to sleep? Say yes or no."  # The live model reply, copied from the host's own question.
REAL = "Put the computer to sleep? Say yes or no."


class DeadConfirmationTests(unittest.TestCase):
    """Live: after a real 'Put the computer to sleep? Say yes or no.', 'put the pc to sleep' got the model's
    own copy of that question (outcome answer, nothing pending), so 'yes' got 'There's nothing waiting for a yes.'"""

    def test_the_live_sequence_real_confirmation_then_a_second_request(self):
        engine, desktop, model = make_engine([call("power", action="sleep")], COPIED, [call("power", action="sleep")],
                                       "The computer is going to sleep.")
        first = engine.respond("put the computer to sleep")
        self.assertEqual((first.text, first.outcome), (REAL, "confirm"))
        self.assertEqual(engine.respond("no").text, "Okay, I won't.")
        second = engine.respond("put the pc to sleep")
        self.assertEqual((second.text, second.outcome), (REAL, "confirm"))  # A real pending action this time.
        self.assertEqual((engine.pending.tool, engine.pending.args["action"]), ("power", "sleep"))
        self.assertIn("nothing is waiting for their answer", model.requests[2]["messages"][-1]["content"])
        self.assertEqual(desktop.power, [])
        engine.respond("yes")
        self.assertEqual(desktop.power, [("sleep", 0)])

    def test_the_same_when_the_first_question_was_left_unanswered(self):
        engine, desktop, _model = make_engine([call("power", action="sleep")], COPIED, [call("power", action="sleep")],
                                       "The computer is going to sleep.")
        engine.respond("put the computer to sleep")
        second = engine.respond("put the pc to sleep")
        self.assertEqual((second.text, second.outcome), (REAL, "confirm"))
        engine.respond("yes")
        self.assertEqual(desktop.power, [("sleep", 0)])

    def test_a_model_that_keeps_asking_never_shows_the_host_phrasing(self):
        engine, desktop, _model = make_engine(COPIED, COPIED)
        result = engine.respond("put the pc to sleep")
        self.assertNotIn("yes or no", result.text.casefold())
        self.assertEqual(result.text, "Sorry, I didn't manage to do that.")
        self.assertIsNone(engine.pending)
        self.assertEqual(engine.respond("yes").text, "There's nothing waiting for a yes.")
        self.assertEqual(desktop.power, [])

    def test_an_offer_in_host_phrasing_becomes_the_host_question(self):
        engine, desktop, _model = make_engine("Should I put the computer to sleep? Say yes or no.",
                                              [call("power", action="sleep", request="unsure")],
                                              "The computer is going to sleep.")
        result = engine.respond("i think my pc needs a sleep")
        self.assertEqual((result.text, result.outcome), (REAL, "confirm"))
        engine.respond("yes")
        self.assertEqual(desktop.power, [("sleep", 0)])

    def test_a_tier_one_action_in_host_phrasing_just_runs(self):
        engine, desktop, _model = make_engine("Muting the sound? Say yes or no.", [call("volume", action="mute")])
        result = engine.respond("mute the sound")
        self.assertTrue(desktop.muted)
        self.assertEqual(result.text, "Muted the sound.")

    def test_without_tools_the_phrase_is_dropped(self):
        engine, desktop, _model = make_engine("Okay, I won't put it to sleep. Say yes or no.")
        result = engine.respond("don't put the pc to sleep")
        self.assertEqual(result.text, "Okay, I won't put it to sleep.")
        self.assertEqual(desktop.power, [])


if __name__ == "__main__":
    unittest.main()
