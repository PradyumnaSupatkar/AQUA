"""Live typed-chat findings (--engine new): typed numbers and capability denials, dead model-written
confirmations, open_settings for things Windows only lets the user change, and replaying a video."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from aqua import chat
from aqua.browser.fake import FakeBrowserBridge
from aqua.agent.selection import replay_groups, replay_request, select_tools
from aqua.speech.postprocess import prepare_typed
from aqua.tools import build_registry
from aqua.tools.settings import PAGES, default_browser_request
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
                        "search my emails for the invoice"):
            engine, _desktop, model = make_engine("I can't do that.", "I can't do that.")
            engine.respond(request)
            self.assertEqual(len(model.requests), 1, request)


COPIED = "Putting the PC to sleep? Say yes or no."  # The live model reply, copied from the host's own question.
REAL = "Put the computer to sleep? Say yes or no."


class DeadConfirmationTests(unittest.TestCase):
    """Live: after a real 'Put the computer to sleep? Say yes or no.', 'put the pc to sleep' got the model's
    own copy of that question (outcome answer, nothing pending), so 'yes' got 'There's nothing waiting for a yes.'"""

    def test_the_live_sequence_real_confirmation_then_a_second_request(self):
        # The benchmark run showed the live model repeating its own question even after a nudge, so the
        # host maps it to the exact call itself: no extra model round, the real question, then a working yes.
        engine, desktop, model = make_engine([call("power", action="sleep")], COPIED, "The computer is going to sleep.")
        first = engine.respond("put the computer to sleep")
        self.assertEqual((first.text, first.outcome), (REAL, "confirm"))
        self.assertEqual(engine.respond("no").text, "Okay, I won't.")
        second = engine.respond("put the pc to sleep")
        self.assertEqual((second.text, second.outcome), (REAL, "confirm"))  # A real pending action this time.
        self.assertEqual((engine.pending.tool, engine.pending.args), ("power", {"action": "sleep", "request": "asked"}))
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(desktop.power, [])
        engine.respond("yes")
        self.assertEqual(desktop.power, [("sleep", 0)])

    def test_the_same_when_the_first_question_was_left_unanswered(self):
        engine, desktop, _model = make_engine([call("power", action="sleep")], COPIED, "The computer is going to sleep.")
        engine.respond("put the computer to sleep")
        second = engine.respond("put the pc to sleep")
        self.assertEqual((second.text, second.outcome), (REAL, "confirm"))
        engine.respond("yes")
        self.assertEqual(desktop.power, [("sleep", 0)])

    def test_a_question_no_tool_phrase_matches_gets_one_nudge(self):
        engine, desktop, model = make_engine("Doing that now? Say yes or no.", [call("power", action="sleep")],
                                             "The computer is going to sleep.")
        result = engine.respond("put the pc to sleep")
        self.assertEqual((result.text, result.outcome), (REAL, "confirm"))
        self.assertIn("nothing is waiting for their answer", model.requests[1]["messages"][-1]["content"])

    def test_a_model_that_keeps_asking_never_shows_the_host_phrasing(self):
        vague = "Doing that now? Say yes or no."
        engine, desktop, _model = make_engine(vague, vague)
        result = engine.respond("put the pc to sleep")
        self.assertNotIn("yes or no", result.text.casefold())
        self.assertEqual(result.text, "Sorry, I didn't manage to do that.")
        self.assertIsNone(engine.pending)
        self.assertEqual(engine.respond("yes").text, "There's nothing waiting for a yes.")
        self.assertEqual(desktop.power, [])

    def test_an_ambiguous_question_is_not_guessed(self):
        engine, _desktop, _model = make_engine([call("power", action="sleep")], "Okay.")
        turn_tools = ("launch_app", "type_text", "undo_last", "lock_screen", "power")
        from aqua.agent.loop import _Turn
        turn = _Turn("do it", turn_tools, [])
        self.assertIsNone(engine._host_ask_call(turn, "Lock the screen or restart the computer? Say yes or no."))
        mapped = engine._host_ask_call(turn, "Lock the screen? Say yes or no.")
        self.assertEqual(mapped["function"]["name"], "lock_screen")
        self.assertEqual(mapped["function"]["arguments"]["request"], "unsure")  # Not a command: the host asks.

    def test_an_open_question_names_the_app(self):
        engine, desktop, _model = make_engine("Open Notepad? Say yes or no.", "Notepad is open.")
        result = engine.respond("I need to write something down")
        self.assertEqual((result.outcome, engine.pending.tool, engine.pending.args["app"]),
                         ("confirm", "launch_app", "Notepad"))
        self.assertTrue(result.text.startswith("Open Notepad"), result.text)
        engine.respond("yes")
        self.assertEqual(desktop.launched, ["Notepad"])

    def test_an_offer_question_with_a_past_tense_word_is_kept(self):
        # Benchmark: "...open a code editor ... to get started?" lost its question ("started" read as a claim).
        offer = "That's great! Would you like me to open a code editor like Visual Studio Code to get started?"
        engine, _desktop, _model = make_engine(offer, "Would you like me to open Visual Studio Code?")
        result = engine.respond("I feel like programming")
        self.assertEqual(result.text, offer)

    def test_streamed_text_is_never_repeated_before_the_offer(self):
        # Benchmark: "That's great! That's great!" (the streamed start said again, the question lost).
        engine, _desktop, _model = make_engine(
            "That's great! Would you like me to open Visual Studio Code? Say yes or no.",
            "That's great! Would you like me to open Visual Studio Code?")
        result = engine.respond("I feel like coding")
        self.assertEqual(result.text.count("That's great!"), 1, result.text)

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


class OpenSettingsTests(unittest.TestCase):
    def test_only_the_allowlisted_pages_exist_and_it_is_tier_one(self):
        tool = build_registry().get("open_settings")
        self.assertEqual(tool.tier, 1)
        self.assertEqual(set(PAGES), {"default_apps", "sound", "display", "bluetooth", "wifi", "network",
                                      "notifications", "power", "apps", "about", "windows_update"})
        self.assertTrue(all(uri.startswith("ms-settings:") for uri, _ in PAGES.values()))

    def test_a_page_opens_without_asking(self):
        engine, desktop, _model = make_engine([call("open_settings", page="bluetooth")])
        result = engine.respond("open bluetooth settings")
        self.assertEqual(desktop.settings_opened, ["ms-settings:bluetooth"])
        self.assertEqual(result.text, "Opened Bluetooth & devices in Settings.")

    def test_spoken_names_map_to_pages_and_anything_else_is_refused(self):
        engine, desktop, _model = make_engine([call("open_settings", page="wi-fi")])
        engine.respond("open the wifi settings")
        self.assertEqual(desktop.settings_opened, ["ms-settings:network-wifi"])
        engine, desktop, _model = make_engine([call("open_settings", page="ms-settings:privacy")],
                                              "I can't open that page.")
        engine.respond("open privacy settings")
        self.assertEqual(desktop.settings_opened, [])
        with self.assertRaises(OSError):
            desktop.open_settings("ms-settings:privacy")

    def test_settings_words_show_the_tool(self):
        registry = build_registry()
        for text in ("open bluetooth settings", "show me my notifications settings", "check for windows update"):
            self.assertIn("open_settings", select_tools(text, registry), text)


class DefaultBrowserTests(unittest.TestCase):
    """Live: 'Make Brave the default browser' was refused (correct, Windows blocks it) but unhelpfully."""

    def test_the_live_request_explains_and_opens_default_apps_without_the_model(self):
        engine, desktop, model = make_engine()
        result = engine.respond("Make Brave the default browser")
        self.assertEqual(desktop.settings_opened, ["ms-settings:defaultapps"])
        self.assertEqual(result.text, "Windows only lets you change the default browser yourself, so I opened "
                                      "Default apps in Settings: find Brave there and click Set default.")
        self.assertEqual((result.outcome, model.requests), ("action", []))
        self.assertEqual(desktop.opened_urls, [])

    def test_other_wordings(self):
        cases = {"make brave my default browser": "Brave", "set Chrome as the default browser": "Chrome",
                 "change my default browser to Firefox": "Firefox", "I want Brave as my default browser": "Brave",
                 "can you make it my default browser": None, "please set my default browser": None}
        for text, browser in cases.items():
            self.assertEqual(default_browser_request(text), (True, browser), text)
        for text in ("what is my default browser?", "open brave", "search the web in my browser"):
            self.assertFalse(default_browser_request(text)[0], text)

    def test_questions_and_negations_go_to_the_model(self):
        for text in ("how do I make brave my default browser?", "don't make brave my default browser"):
            engine, desktop, model = make_engine("Open Settings, then Apps, then Default apps.")
            engine.respond(text)
            self.assertEqual(desktop.settings_opened, [], text)
            self.assertEqual(len(model.requests), 1, text)

    def test_when_settings_cannot_open_the_steps_are_said(self):
        engine, desktop, _model = make_engine()
        desktop.open_settings = lambda uri: (_ for _ in ()).throw(OSError("no"))
        result = engine.respond("make brave my default browser")
        self.assertIn("open Settings, then Apps, then Default apps, and find Brave there", result.text)


class ReplayTests(unittest.TestCase):
    """Live: 'replay the video' got 'Replaying the video? Say yes or no.', then 'yes replay' pressed the
    media keys (no media session) while YouTube played in Brave."""

    def test_replay_the_video_is_one_youtube_step_with_the_host_reply(self):
        engine, desktop, model, bridge = youtube_engine([call("youtube", action="seek", to="0:00")],
                                                        position=420, playing=False)
        result = engine.respond("replay the video")
        self.assertEqual(bridge.effects, ["youtube:brave:seek=0", "youtube:brave:play"])
        self.assertEqual((result.text, result.outcome), ("Restarted the video.", "action"))
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(desktop.media, [])
        self.assertNotIn("media", model.requests[0]["tools"])
        self.assertNotIn("power", model.requests[0]["tools"])

    def test_the_models_play_or_restart_become_the_restart(self):
        for args in ({"action": "play"}, {"action": "restart"}, {"action": "seek", "seconds": -30}):
            engine, _desktop, _model, bridge = youtube_engine([call("youtube", **args)], position=300)
            self.assertEqual(engine.respond("play the video again from the beginning").text, "Restarted the video.")
            self.assertEqual(bridge.effects, ["youtube:brave:seek=0", "youtube:brave:play"], args)

    def test_without_video_words_it_needs_a_connected_youtube_tab(self):
        for text in ("start it over", "replay", "play it again", "rewatch it", "restart it"):
            engine, _desktop, model, bridge = youtube_engine([call("youtube", action="restart")])
            engine.respond(text)
            self.assertEqual(bridge.effects, ["youtube:brave:seek=0", "youtube:brave:play"], text)
        self.assertEqual(replay_groups("start it over", ("apps",), video_active=False), ("apps",))

    def test_the_models_dead_question_turns_into_the_restart(self):
        engine, _desktop, model, bridge = youtube_engine("Replaying the video? Say yes or no.",
                                                         [call("youtube", action="restart")])
        result = engine.respond("replay the video")
        self.assertEqual(result.text, "Restarted the video.")  # Tier 1: no confirmation.
        self.assertEqual(bridge.effects, ["youtube:brave:seek=0", "youtube:brave:play"])

    def test_restarting_the_computer_is_never_a_replay(self):
        for text in ("restart the computer", "restart", "restart spotify", "play the song again"):
            self.assertFalse(replay_request(text, video_active=True), text)
        self.assertIn("power", select_tools("restart the computer", build_registry(), video_active=True))

    def test_a_failed_seek_is_reported_and_play_is_not_sent(self):
        engine, _desktop, _model, bridge = youtube_engine([call("youtube", action="restart")],
                                                          "The YouTube tab is not showing a video.", page="home")
        result = engine.respond("replay the video")
        self.assertEqual(bridge.effects, [])
        self.assertEqual([c[1] for c in bridge.calls], ["seek"])
        self.assertIn("not showing a video", result.text)


if __name__ == "__main__":
    unittest.main()
