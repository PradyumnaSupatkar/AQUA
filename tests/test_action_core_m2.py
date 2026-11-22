"""Milestone 2 tools on the fake desktop: close/force-quit, power with countdown,
brightness, media state, open_url/web_search and inspect_window."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import Mock, patch

from aqua import handsfree
from aqua.actions.tools import ActionState
from aqua.agent.context import ToolContext
from aqua.agent.countdown import Countdown, is_cancel
from aqua.agent.policy import CLARIFY, CONFIRM, DENY, Policy
from aqua.desktop import backend
from aqua.tools.app_index import AppIndex
from aqua.tools.apps import close_app, force_quit_app
from aqua.tools.fake import FakeDesktop
from aqua.tools.power import power
from aqua.tools.system import brightness, media
from aqua.tools.web import SEARCH_URL, normalize_url, open_url, web_search
from aqua.tools.windows import inspect_window

from action_core_helpers import call, make_engine


def context(desktop=None):
    desktop = desktop if desktop is not None else FakeDesktop()
    return ToolContext(desktop=desktop, apps=AppIndex(desktop.app_entries, clock=desktop.monotonic), policy=Policy())


def decide(tool, args, ctx):
    return ctx.policy.decide(tool, args, ctx)


def results(model, index=-1):
    return [json.loads(m["content"]) for m in model.tool_results(index)]


class CloseAndForceQuitTests(unittest.TestCase):
    def test_close_app_closes_every_window_of_the_app(self):
        desktop = FakeDesktop()
        first = desktop.add_window("a.txt - Notepad", "notepad.exe")
        second = desktop.add_window("b.txt - Notepad", "notepad.exe")
        other = desktop.add_window("Calculator", "applicationframehost.exe")
        ctx = context(desktop)
        self.assertEqual(decide(close_app, {"app": "Notepad"}, ctx).action, "run")
        outcome = close_app.run({"app": "Notepad"}, ctx)
        self.assertEqual((outcome.status, outcome.done), ("ok", "closed Notepad"))
        self.assertEqual(set(desktop.closed), {first.handle, second.handle})
        self.assertIsNotNone(desktop.window(other.handle))

    def test_window_that_asks_to_save_is_reported(self):
        desktop = FakeDesktop(stubborn_apps={"notepad.exe"})
        desktop.add_window("notes.txt* - Notepad", "notepad.exe")
        outcome = close_app.run({"app": "notepad"}, context(desktop))
        self.assertEqual(outcome.status, "unverified")
        self.assertIn("asking to save", outcome.message)

    def test_close_of_missing_app_and_terminals(self):
        self.assertEqual(close_app.run({"app": "Spotify"}, context()).status, "failed")
        self.assertEqual(decide(close_app, {"app": "terminal"}, context()).action, DENY)

    def test_force_quit_asks_first_then_terminates(self):
        desktop = FakeDesktop()
        window = desktop.add_window("Untitled - Notepad (Not Responding)", "notepad.exe", pid=4242)
        ctx = context(desktop)
        decision = decide(force_quit_app, {"app": "Notepad"}, ctx)
        self.assertEqual(decision.action, CONFIRM)
        self.assertEqual(decision.question, "Force quit Notepad? Unsaved work will be lost. Say yes or no.")
        outcome = force_quit_app.run({"app": "Notepad"}, ctx)
        self.assertEqual((outcome.status, desktop.terminated), ("ok", [4242]))
        self.assertIsNone(desktop.window(window.handle))

    def test_force_quit_never_targets_windows_or_aqua(self):
        desktop = FakeDesktop()
        desktop.add_window("Downloads - File Explorer", "explorer.exe")
        desktop.add_window("Calculator", "applicationframehost.exe")
        ctx = context(desktop)
        self.assertEqual(decide(force_quit_app, {"app": "file explorer"}, ctx).action, DENY)
        self.assertEqual(decide(force_quit_app, {"app": "calculator"}, ctx).action, DENY)
        self.assertEqual(decide(force_quit_app, {"app": "terminal"}, ctx).action, DENY)

    def test_force_quit_failure_is_reported(self):
        desktop = FakeDesktop()
        desktop.add_window("Untitled - Notepad", "notepad.exe")
        desktop.terminate = Mock(side_effect=OSError("denied"))
        self.assertEqual(force_quit_app.run({"app": "notepad"}, context(desktop)).status, "failed")

    def test_force_quit_through_the_engine(self):
        desktop = FakeDesktop()
        desktop.add_window("Untitled - Notepad", "notepad.exe", pid=77)
        engine, _, _ = make_engine([call("force_quit_app", app="Notepad")], "Force-quit Notepad.", desktop=desktop)
        self.assertEqual(engine.respond("force quit notepad").outcome, "confirm")
        self.assertEqual(desktop.terminated, [])
        self.assertEqual(engine.respond("yes").text, "Force-quit Notepad.")
        self.assertEqual(desktop.terminated, [77])


class PowerCountdownTests(unittest.TestCase):
    def test_restart_flow_question_yes_countdown_cancel(self):
        engine, desktop, model = make_engine([call("power", action="restart")], "Restarting in 30 seconds.")
        asked = engine.respond("restart the computer")
        self.assertEqual((asked.outcome, asked.text), ("confirm", "Restart the computer? Say yes or no."))
        self.assertTrue(engine.awaiting_answer)
        self.assertEqual(desktop.power, [])
        started = engine.respond("yes")
        self.assertEqual(desktop.power, [("restart", 30)])
        self.assertEqual(started.text, "Restarting in 30 seconds. Say cancel to stop it.")
        self.assertTrue(engine.countdown_active)
        self.assertTrue(engine.awaiting_answer)
        self.assertIsNone(engine.countdown_announcement())       # 30 s left: nothing due yet.
        desktop.clock += 10
        self.assertEqual(engine.countdown_announcement(), "Restart in 20 seconds. Say cancel to stop it.")
        self.assertIsNone(engine.countdown_announcement())       # Each reminder only once.
        desktop.clock += 12
        self.assertEqual(engine.countdown_announcement(), "Restart in 10 seconds. Say cancel to stop it.")
        cancelled = engine.respond("cancel")
        self.assertEqual((cancelled.outcome, cancelled.text), ("cancelled", "Cancelled the restart."))
        self.assertEqual(desktop.power, [("restart", 30), ("abort", 0)])
        self.assertIsNone(desktop.scheduled)
        self.assertFalse(engine.countdown_active)
        self.assertEqual(len(model.requests), 2)                 # "cancel" never reached the model.

    def test_stop_and_no_also_cancel_a_shutdown(self):
        for word in ("stop", "no", "No, don't!", "wait, cancel that"):
            engine, desktop, _ = make_engine([call("power", action="shutdown")], "Shutting down.")
            engine.respond("shut down my computer")
            engine.respond("yes")
            self.assertEqual(engine.respond(word).text, "Cancelled the shutdown.", word)
            self.assertIn(("abort", 0), desktop.power)

    def test_unrelated_speech_keeps_the_countdown_and_expiry_ends_it(self):
        engine, desktop, _ = make_engine([call("power", action="shutdown")], "Shutting down.", "It is 3 PM.")
        engine.respond("shut down")
        engine.respond("yes")
        self.assertEqual(engine.respond("what time is it").outcome, "answer")
        self.assertTrue(engine.countdown_active)
        desktop.clock += 31
        self.assertFalse(engine.countdown_active)
        self.assertIsNone(engine.countdown_announcement())
        self.assertIsNone(engine.context.countdown)

    def test_failed_abort_tells_the_user_how_to_stop_it(self):
        engine, desktop, _ = make_engine([call("power", action="restart")], "Restarting.")
        engine.respond("restart")
        engine.respond("yes")
        desktop.abort_shutdown = Mock(side_effect=OSError("access denied"))
        self.assertIn("shutdown /a", engine.respond("cancel").text)

    def test_sleep_and_sign_out_ask_then_act_without_countdown(self):
        for action, question, effect in (
            ("sleep", "Put the computer to sleep? Say yes or no.", ("sleep", 0)),
            ("sign_out", "Sign out of Windows? Unsaved work may be lost. Say yes or no.", ("sign_out", 0)),
            ("log_off", "Sign out of Windows? Unsaved work may be lost. Say yes or no.", ("sign_out", 0)),
        ):
            ctx = context()
            decision = decide(power, {"action": action}, ctx)
            self.assertEqual((decision.action, decision.question), (CONFIRM, question))
            power.run({"action": action}, ctx)
            self.assertEqual(ctx.desktop.power, [effect])
            self.assertIsNone(ctx.countdown)

    def test_countdown_helpers(self):
        countdown = Countdown("shutdown", 30.0)
        self.assertEqual(countdown.due_announcement(25.0), "Shutdown in 5 seconds. Say cancel to stop it.")
        self.assertIsNone(countdown.due_announcement(26.0))       # Earlier marks are skipped, not replayed.
        self.assertFalse(countdown.active(30.0))
        for text in ("cancel", "Stop!", "no", "abort it", "don't do it", "never mind"):
            self.assertTrue(is_cancel(text), text)
        for text in ("what time is it", "open notepad", "", "please tell me a long story about how to cancel things later"):
            self.assertFalse(is_cancel(text), text)


class BrightnessAndMediaTests(unittest.TestCase):
    def test_brightness_up_down_set_and_undo(self):
        desktop = FakeDesktop(brightness=70)
        ctx = context(desktop)
        down = brightness.run({"action": "down"}, ctx)
        self.assertEqual((desktop.brightness, down.done), (60, "turned the brightness down to 60%"))
        brightness.run({"action": "up", "level": 50}, ctx)
        self.assertEqual(desktop.brightness, 100)
        outcome = brightness.run({"action": "set", "level": 30}, ctx)
        self.assertEqual(desktop.brightness, 30)
        self.assertEqual(outcome.undo.run(), "put the brightness back to 100%")
        self.assertEqual(desktop.brightness, 100)
        self.assertEqual(decide(brightness, {"action": "set"}, ctx).action, CLARIFY)

    def test_unsupported_display_says_so(self):
        outcome = brightness.run({"action": "up"}, context(FakeDesktop(brightness=None)))
        self.assertEqual(outcome.status, "failed")
        self.assertIn("cannot be changed", outcome.message)

    def test_brightness_undo_through_the_engine(self):
        engine, desktop, _ = make_engine([call("brightness", action="down")], "Dimmed.", [call("undo_last")], "")
        engine.respond("dim the screen")
        self.assertEqual(desktop.brightness, 60)
        engine.respond("undo that")
        self.assertEqual(desktop.brightness, 70)

    def test_pause_only_pauses_when_something_plays(self):
        playing = FakeDesktop(playback="playing")
        self.assertEqual(media.run({"action": "pause"}, context(playing)).done, "paused the playback")
        self.assertEqual(playing.media, ["play_pause"])
        for state in ("paused", "stopped", "none"):
            desktop = FakeDesktop(playback=state)
            outcome = media.run({"action": "pause"}, context(desktop))
            self.assertEqual((outcome.status, desktop.media), ("ok", []), state)
            self.assertIn("Nothing is playing", outcome.message)

    def test_play_only_resumes_when_paused(self):
        paused = FakeDesktop(playback="paused")
        self.assertEqual(media.run({"action": "play"}, context(paused)).done, "resumed the playback")
        self.assertEqual(paused.media, ["play_pause"])
        playing = FakeDesktop(playback="playing")
        self.assertIn("already playing", media.run({"action": "play"}, context(playing)).message)
        self.assertEqual(playing.media, [])
        self.assertEqual(media.run({"action": "play"}, context(FakeDesktop(playback="none"))).status, "failed")

    def test_unknown_state_falls_back_to_the_toggle(self):
        desktop = FakeDesktop(playback=None)
        outcome = media.run({"action": "pause"}, context(desktop))
        self.assertEqual((outcome.status, desktop.media), ("unverified", ["play_pause"]))
        media.run({"action": "next"}, context(desktop))
        self.assertEqual(desktop.media, ["play_pause", "next"])


class WebToolTests(unittest.TestCase):
    def test_only_web_addresses_are_accepted(self):
        for value, expected in (("youtube.com", "https://youtube.com"), ("https://example.org/a?b=1", None),
                                ("http://localhost:3000", None), ("bbc.co.uk/news", "https://bbc.co.uk/news"),
                                ("localhost:8080", "https://localhost:8080"), ("192.168.1.1", "https://192.168.1.1")):
            self.assertEqual(normalize_url(value), expected or value, value)
        for value in ("file:///C:/Users/me/secret.txt", "C:\\Windows\\System32", "c:/temp", "\\\\server\\share",
                      "/etc/passwd", "~/notes", "./run.bat", "javascript:alert(1)", "mailto:a@b.com",
                      "ftp://example.com", "https://user:pw@example.com", "not a url", "notes", ""):
            self.assertIsNone(normalize_url(value), value)

    def test_open_url_denies_local_paths_and_opens_websites(self):
        ctx = context()
        self.assertEqual(decide(open_url, {"url": "file:///C:/secret.txt"}, ctx).action, DENY)
        self.assertEqual(decide(open_url, {"url": "C:\\Users"}, ctx).action, DENY)
        self.assertEqual(decide(open_url, {"url": "youtube.com"}, ctx).action, "run")
        outcome = open_url.run({"url": "youtube.com"}, ctx)
        self.assertEqual((outcome.done, ctx.desktop.opened_urls), ("opened youtube.com", ["https://youtube.com"]))

    def test_web_search_opens_the_search_page(self):
        ctx = context()
        outcome = web_search.run({"query": "best  pizza near me"}, ctx)
        self.assertEqual(ctx.desktop.opened_urls, [SEARCH_URL.format("best+pizza+near+me")])
        self.assertTrue(ctx.desktop.opened_urls[0].startswith("https://"))
        self.assertEqual(outcome.done, "searched the web for best pizza near me")

    def test_browser_failure_is_reported(self):
        desktop = FakeDesktop()
        desktop.open_url = Mock(side_effect=OSError("no browser"))
        self.assertEqual(open_url.run({"url": "example.com"}, context(desktop)).status, "failed")
        self.assertEqual(web_search.run({"query": "x"}, context(desktop)).status, "failed")


class InspectWindowTests(unittest.TestCase):
    def test_labels_only_and_text_stays_hidden(self):
        desktop = FakeDesktop()
        window = desktop.add_window("Untitled - Notepad", "notepad.exe")
        desktop.controls[window.handle] = [
            {"control_type": "Window", "name": "Untitled - Notepad"}, {"control_type": "MenuItem", "name": "File"},
            {"control_type": "Button", "name": "Close"}, {"control_type": "Edit", "redacted": "text_input"},
            {"control_type": "Button", "redacted": "password"}]
        outcome = inspect_window.run({"window": "notepad"}, context(desktop))
        self.assertEqual(outcome.status, "ok")
        self.assertEqual([row["name"] for row in outcome.data["controls"]], ["Untitled - Notepad", "File", "Close"])
        self.assertIn("2 without readable labels", outcome.message)
        self.assertIn("untrusted", outcome.message)

    def test_missing_minimised_and_unreadable_windows(self):
        self.assertEqual(inspect_window.run({"window": "excel"}, context()).status, "failed")
        desktop = FakeDesktop()
        window = desktop.add_window("Untitled - Notepad", "notepad.exe")
        desktop.show(window.handle, "minimise")
        self.assertIn("minimised", inspect_window.run({"window": "notepad"}, context(desktop)).message)
        desktop = FakeDesktop()
        desktop.add_window("Untitled - Notepad", "notepad.exe")
        desktop.inspect = lambda handle: {"status": "timeout"}
        self.assertIn("did not answer", inspect_window.run({"window": "notepad"}, context(desktop)).message)

    def test_backend_inspect_handle_reuses_the_read_only_walker(self):
        from test_desktop_backend import Element, Native, UI
        root = Element("Window", "Main", [Element("Button", "Save"),
                                          Element("Edit", AssertionError("Edit text must never be read")),
                                          Element("Button", AssertionError("password"), password=True)])
        native = Native({100: {"handle": 100, "pid": 10, "created": 1, "image": r"C:\Apps\Any.exe"}})
        result = backend.inspect_handle({"handle": 100}, native=native, ui=UI(root))
        self.assertEqual(result["status"], "ok")
        rows = result["controls"]
        self.assertEqual([r.get("name") for r in rows[:2]], ["Main", "Save"])
        self.assertEqual({r.get("redacted") for r in rows[2:]}, {"text_input", "password"})
        self.assertEqual(backend.inspect_handle({"handle": 999}, native=native, ui=UI(root))["status"], "stale_window")
        self.assertEqual(backend.inspect_handle({"handle": "x"}, native=native, ui=UI(root))["status"], "error")


class SelectionTests(unittest.TestCase):
    def test_new_groups_fit_and_are_selected(self):
        from aqua.agent.selection import select_tools
        from aqua.tools import build_registry
        registry = build_registry()
        for text, tool in (("close notepad", "close_app"), ("force quit chrome", "force_quit_app"),
                           ("restart the computer", "power"), ("turn the brightness down", "brightness"),
                           ("open youtube.com", "open_url"), ("search the web for pizza", "web_search"),
                           ("what buttons does notepad have", "inspect_window"), ("pause the video", "media")):
            names = select_tools(text, registry)
            self.assertIn(tool, names, text)
            self.assertLessEqual(len(names), 8, text)


class HandsfreeCountdownTests(unittest.TestCase):
    def test_reminders_are_spoken_and_cancel_works_without_the_wake_word(self):
        from test_voice_handsfree import TAIL, Detector, Microphone, Vad, frame, utterance
        from aqua.brain.ollama import DEFAULT_CHAT_MODEL, ModelInfo
        from aqua.speech.recognizer import Transcript
        engine, desktop, _ = make_engine()
        desktop.clock = 0.0
        engine.context.countdown = Countdown("restart", 15.0, fresh=False)  # 15 s left: "10 seconds" is due soon.
        client = Mock()
        client.check_model.return_value = ModelInfo(DEFAULT_CHAT_MODEL, "t", "qwen3", "non_thinking")
        recognizer, console = Mock(), Mock()
        console.poll.return_value = None

        def tick():
            desktop.clock = 6.0  # 9 s left
        # The engine is created lazily on the first command; start with one wake turn.
        from test_voice_handsfree import wake_turn
        items = [*wake_turn(), tick, frame(), *[frame(TAIL) for _ in range(handsfree.FOLLOW_UP_TAIL_FRAMES)],
                 *utterance()]
        heard = iter([Transcript("Hey Aqua, what's happening?", "en", .99, .32, -10, -20, 0, -.1, .01, 1),
                      Transcript("Cancel.", "en", .99, .32, -10, -20, 0, -.1, .01, 1)])
        recognizer.transcribe.side_effect = lambda *_a, **_k: next(heard)
        engine.client.rounds.append("A restart is scheduled.")
        output = StringIO()
        with patch("aqua.handsfree.SherpaWakeDetector", return_value=Detector()), \
                patch("aqua.handsfree.StreamingVad", return_value=Vad()), \
                patch("aqua.handsfree.OfflineRecognizer", return_value=recognizer), \
                patch("aqua.handsfree.ConsoleCommands", return_value=console), \
                patch("aqua.chat.OllamaClient", return_value=client), \
                patch("aqua.agent.loop.build_engine", return_value=engine), \
                patch("aqua.handsfree.MicrophoneStream", return_value=Microphone(items)), \
                patch("aqua.chat._open_speaker", return_value=None), \
                patch("socket.create_connection", side_effect=AssertionError("offline")), \
                redirect_stdout(output), redirect_stderr(StringIO()):
            status = handsfree.run_handsfree(end_silence=.128, initial_timeout=.512, max_utterance=1.024,
                                             pre_roll=.064, wake_cooldown=0, speak=False, engine="new")
        text = output.getvalue()
        self.assertEqual(status, 0)
        self.assertIn("Aqua: Restart in 10 seconds. Say cancel to stop it.", text)
        self.assertIn("Listening for your answer...", text)
        self.assertIn("Cancelled the restart.", text)
        self.assertIn(("abort", 0), desktop.power)

    def test_asked_question_covers_the_countdown(self):
        engine, desktop, _ = make_engine()
        engine.context.countdown = Countdown("shutdown", 30.0)
        self.assertTrue(handsfree._asked_question(Mock(text="Shutting down."), ActionState(), engine))
        desktop.clock = 40.0
        self.assertFalse(handsfree._asked_question(Mock(text="Shutting down."), ActionState(), engine))


if __name__ == "__main__":
    unittest.main()
