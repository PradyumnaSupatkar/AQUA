"""The agent loop end to end: scripted model, real registry and policy, fake desktop.

The first class is the milestone 1 "must work" list.
"""

import json
import unittest

from aqua.agent.loop import MAX_DECISIONS, MAX_OPERATIONS, first_sentence, join_phrases, strip_emoji
from aqua.agent.prompt import SYSTEM_PROMPT
from aqua.agent.registry import DISRUPTIVE, Registry, Tool, ToolOutcome
from aqua.brain.ollama import OllamaConnectionError
from aqua.tools import build_registry
from aqua.tools.fake import FakeDesktop

from action_core_helpers import Clock, call, make_engine


def results(model, index=-1):
    return [json.loads(m["content"]) for m in model.tool_results(index)]


class MustWorkScenarios(unittest.TestCase):
    def test_open_notepad_and_write(self):
        engine, desktop, model = make_engine(
            [call("launch_app", app="Notepad")], [call("type_text", text="Hello I am Aqua")],
            "Opened Notepad and typed your text.")
        spoken = []
        result = engine.respond("Open Notepad and write Hello I am Aqua", on_text=spoken.append)
        self.assertEqual(result.text, "Opened Notepad and typed your text.")
        self.assertEqual("".join(spoken), result.text)
        self.assertEqual((result.ok, result.outcome, result.requests), (True, "action", 3))
        self.assertEqual(desktop.launched, ["Notepad"])
        self.assertEqual(list(desktop.text.values()), ["Hello I am Aqua"])
        self.assertEqual([op.status for op in result.task.operations], ["ok", "ok"])
        self.assertEqual(model.requests[0]["tools"][:2], ["launch_app", "type_text"])

    def test_open_notepad_and_write_in_one_round_with_silent_model(self):
        engine, desktop, model = make_engine(
            [call("launch_app", app="notepad"), call("type_text", text="Hello I am Aqua")], "")
        result = engine.respond("Open Notepad and write Hello I am Aqua")
        self.assertEqual(result.text, "Opened Notepad and typed your text.")  # Host fallback from the steps.
        self.assertEqual(list(desktop.text.values()), ["Hello I am Aqua"])

    def test_write_into_the_already_open_notepad_window(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("notes.txt - Notepad", "notepad.exe", front=False)
        desktop.add_window("notepad tips - Google Chrome", "chrome.exe", front=True)
        engine, _, model = make_engine(
            [call("type_text", text="hello I am your assistant", app="notepad window")],
            "Typed your text into Notepad.", desktop=desktop)
        result = engine.respond("Write hello I am your assistant into the notepad window")
        self.assertEqual(result.text, "Typed your text into Notepad.")
        self.assertEqual(desktop.text, {notepad.handle: "hello I am your assistant"})
        self.assertEqual(desktop.launched, [])

    def test_open_any_installed_app_via_the_index(self):
        for request, app, name in (("Open Spotify", "Spotify", "Spotify"), ("open calculator", "calculator", "Calculator"),
                                   ("open VS Code", "VS Code", "Visual Studio Code")):
            engine, desktop, model = make_engine([call("launch_app", app=app)], f"Opened {name}.")
            result = engine.respond(request)
            self.assertEqual(desktop.launched, [name], request)
            self.assertEqual(result.text, f"Opened {name}.")

    def test_open_command_prompt_is_denied(self):
        engine, desktop, model = make_engine(
            [call("launch_app", app="command prompt")], "I can't open Command Prompt because it's blocked.")
        result = engine.respond("open command prompt")
        self.assertEqual(desktop.launched, [])
        self.assertEqual(results(model)[0]["status"], "failed")
        self.assertIn("blocked", results(model)[0]["message"])
        self.assertEqual(result.task.operations[0].status, "denied")
        self.assertEqual(result.text, "I can't open Command Prompt because it's blocked.")

    def test_system_requests(self):
        cases = (
            ("Turn the volume down", call("volume", action="down"), lambda d: d.volume == 40),
            ("mute", call("volume", action="mute"), lambda d: d.muted),
            ("pause the music", call("media", action="play_pause"), lambda d: d.media == ["play_pause"]),
            ("lock my screen", call("lock_screen", now=True), lambda d: d.locked),
        )
        for request, proposal, effect in cases:
            engine, desktop, model = make_engine([proposal], "Done.")
            result = engine.respond(request)
            self.assertTrue(effect(desktop), request)
            self.assertEqual(result.outcome, "action", request)

    def test_time_and_battery(self):
        engine, _, model = make_engine([call("get_time")], "It's 3:42 PM.")
        self.assertEqual(engine.respond("what time is it").text, "It's 3:42 PM.")
        self.assertRegex(results(model)[0]["message"], r"^It is ")
        self.assertIn("3:42 PM", model.requests[0]["messages"][-1]["content"])  # The clock is also in context.
        engine, _, model = make_engine([call("get_battery")], "You have 76% battery.")
        self.assertEqual(engine.respond("how much battery do I have").text, "You have 76% battery.")
        self.assertIn("76%", results(model)[0]["message"])

    def test_minimise_notepad_then_undo_that(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("Untitled - Notepad", "notepad.exe")
        engine, _, model = make_engine(
            [call("window_action", action="minimise", window="Notepad")], "Minimised Notepad.",
            [call("undo_last")], "Put Notepad back.", desktop=desktop)
        self.assertEqual(engine.respond("minimise Notepad").text, "Minimised Notepad.")
        self.assertTrue(desktop.window(notepad.handle).minimized)
        self.assertEqual(engine.respond("undo that").text, "Put Notepad back.")
        self.assertFalse(desktop.window(notepad.handle).minimized)

    def test_undo_volume_change(self):
        engine, desktop, _ = make_engine([call("volume", action="down")], "Volume down.", [call("undo_last")], "")
        engine.respond("Turn the volume down")
        self.assertEqual(desktop.volume, 40)
        result = engine.respond("undo that")
        self.assertEqual(desktop.volume, 50)
        self.assertEqual(result.text, "Put the volume back to 50%.")

    def test_dont_open_chrome_offers_no_tools_and_does_nothing(self):
        engine, desktop, model = make_engine("Okay, I won't open Chrome.")
        result = engine.respond("Don't open Chrome")
        self.assertEqual(model.requests[0]["tools"], [])
        self.assertEqual((result.outcome, result.text, desktop.launched), ("answer", "Okay, I won't open Chrome.", []))

    def test_hypothetical_offers_no_tools_and_ignores_a_stray_call(self):
        engine, desktop, model = make_engine(
            [call("launch_app", app="Chrome")], "I would open Google Chrome for you.")
        result = engine.respond("what would happen if I opened Chrome?")
        self.assertEqual(model.requests[0]["tools"], [])
        self.assertEqual(desktop.launched, [])
        self.assertEqual(result.task.operations[0].status, "invalid")


class ConfirmationFlowTests(unittest.TestCase):
    def registry(self, ran):
        shutdown = Tool(name="shutdown", group="power", tier=DISRUPTIVE, description="Shut down.",
                        parameters={"type": "object", "properties": {}},
                        confirm=lambda args: "Shut down the computer? Say yes or no.",
                        run=lambda args, ctx: ran.append("shutdown") or ToolOutcome("ok", "Shutting down.",
                                                                                     done="started the shutdown"))
        registry = build_registry()
        registry.register(shutdown)
        return registry

    def test_yes_runs_the_exact_action_and_resumes_the_loop(self):
        ran = []
        engine, _, model = make_engine([call("shutdown")], "Shutting down in 30 seconds.", registry=self.registry(ran))
        first = engine.respond("shut down the computer")
        self.assertEqual((first.outcome, first.text), ("confirm", "Shut down the computer? Say yes or no."))
        self.assertTrue(first.asked_question)
        self.assertIsNotNone(engine.pending)
        self.assertEqual(ran, [])
        second = engine.respond("yeah, go ahead")
        self.assertEqual(ran, ["shutdown"])
        self.assertEqual(second.text, "Shutting down in 30 seconds.")
        self.assertIsNone(engine.pending)
        self.assertEqual(results(model)[-1]["status"], "ok")

    def test_no_declines_and_unrelated_speech_cancels(self):
        ran = []
        engine, _, _ = make_engine([call("shutdown")], [call("shutdown")], "Hello!", registry=self.registry(ran))
        engine.respond("shut down")
        self.assertEqual(engine.respond("no").text, "Okay, I won't.")
        engine.respond("shut down")
        self.assertEqual(engine.respond("what's the weather like").outcome, "answer")
        self.assertIsNone(engine.pending)
        self.assertEqual(ran, [])

    def test_expired_confirmation_is_not_used(self):
        ran, clock = [], Clock(0.0)
        engine, _, _ = make_engine([call("shutdown")], "Hi.", registry=self.registry(ran), clock=clock)
        engine.respond("shut down")
        clock.value = 61.0
        self.assertEqual(engine.respond("yes").outcome, "answer")
        self.assertEqual(ran, [])

    def test_earlier_steps_are_reported_with_the_question(self):
        ran = []
        engine, desktop, _ = make_engine([call("launch_app", app="notepad"), call("shutdown")],
                                         registry=self.registry(ran))
        result = engine.respond("open notepad and shut down")
        self.assertEqual(result.text, "Opened Notepad. Shut down the computer? Say yes or no.")
        self.assertEqual(desktop.launched, ["Notepad"])


class LoopBehaviourTests(unittest.TestCase):
    def test_plain_conversation_streams_and_is_remembered(self):
        engine, _, model = make_engine("Hi Sam, nice to meet you.", "Your name is Sam.")
        streamed = []
        engine.respond("Hi, I'm Sam", on_text=streamed.append)
        self.assertEqual(len(streamed), 2)  # Live chunks, not one buffered string.
        engine.respond("what's my name")
        history = model.requests[1]["messages"]
        self.assertEqual(history[0], {"role": "system", "content": SYSTEM_PROMPT})
        self.assertEqual(history[1], {"role": "user", "content": "Hi, I'm Sam"})
        self.assertEqual(history[2]["content"], "Hi Sam, nice to meet you.")
        self.assertTrue(history[3]["content"].startswith("what's my name\n\n[Aqua host context"))

    def test_system_message_is_identical_across_requests(self):
        engine, _, model = make_engine("One.", "Two.")
        engine.respond("first")
        engine.respond("second")
        self.assertEqual(model.requests[0]["messages"][0], model.requests[1]["messages"][0])

    def test_reply_after_tools_is_one_sentence_without_emoji(self):
        engine, _, _ = make_engine([call("volume", action="mute")], "Muted! 🔇 Let me know if you need more. 😊")
        self.assertEqual(engine.respond("mute").text, "Muted!")

    def test_ambiguous_app_asks_a_question_and_launches_nothing(self):
        from aqua.tools.adapter import AppEntry
        desktop = FakeDesktop(apps=[AppEntry("Visual Studio Code", "a"), AppEntry("Visual Studio 2022", "b")])
        engine, _, _ = make_engine([call("launch_app", app="visual studio")], desktop=desktop)
        result = engine.respond("open visual studio")
        self.assertEqual(result.outcome, "clarify")
        self.assertTrue(result.text.endswith("?"))
        self.assertEqual(desktop.launched, [])

    def test_invalid_arguments_go_back_to_the_model(self):
        engine, desktop, model = make_engine([call("volume", action="sideways")], [call("volume", action="up")], "Louder.")
        result = engine.respond("volume up")
        self.assertIn("Invalid arguments", results(model, 1)[0]["message"])
        self.assertEqual(desktop.volume, 60)
        self.assertEqual([op.status for op in result.task.operations], ["invalid", "ok"])

    def test_operation_and_decision_budgets(self):
        rounds = [[call("volume", action="up", level=2 * i + 1), call("volume", action="up", level=2 * i + 2)]
                  for i in range(10)]  # Distinct calls, so none is skipped as a repeat.
        engine, desktop, model = make_engine(*rounds)
        result = engine.respond("volume up a lot")
        self.assertEqual(sum(op.status == "ok" for op in result.task.operations), MAX_OPERATIONS)
        self.assertEqual(result.outcome, "limit")
        self.assertIn("too many steps", result.text)
        engine, _, model = make_engine(*[[call("no_such_tool")]] * MAX_DECISIONS)
        result = engine.respond("what time is it")
        self.assertEqual((len(model.requests), result.outcome), (MAX_DECISIONS, "limit"))

    def test_model_error_reports_what_already_happened(self):
        engine, desktop, _ = make_engine([call("volume", action="mute")], OllamaConnectionError("Ollama went away."))
        result = engine.respond("mute")
        self.assertFalse(result.ok)
        self.assertEqual(result.text, "Muted the sound.")
        self.assertEqual(result.error, "Ollama went away.")

    def test_failing_tool_is_contained(self):
        desktop = FakeDesktop()
        desktop.lock = lambda: (_ for _ in ()).throw(OSError("no"))
        engine, _, model = make_engine([call("lock_screen", now=True)], "I couldn't lock the screen.", desktop=desktop)
        result = engine.respond("lock my screen")
        self.assertEqual(results(model)[0]["status"], "failed")
        self.assertEqual(result.text, "I couldn't lock the screen.")

    def test_repeated_same_tool_calls_stay_valid_for_ollama(self):
        desktop = FakeDesktop()
        desktop.add_window("Untitled - Notepad", "notepad.exe")
        engine, _, model = make_engine(
            [call("type_text", text="one", app="notepad"), call("type_text", text=" two", app="notepad")], "Typed it.",
            desktop=desktop)
        engine.respond("type one two into notepad")
        self.assertEqual(list(desktop.text.values()), ["one two"])  # ScriptedModel ran Ollama's validator.

    def test_clear_forgets_history_and_pending(self):
        engine, _, model = make_engine("Hello.", "Again.")
        engine.respond("hi")
        engine.clear()
        engine.respond("hi again")
        self.assertEqual(len(model.requests[1]["messages"]), 2)

    def test_unbacked_claim_gets_one_retry_then_an_honest_reply(self):
        engine, desktop, model = make_engine("The sound has been muted.", [call("volume", action="mute")], "Muted.")
        result = engine.respond("mute")
        self.assertTrue(desktop.muted)
        self.assertEqual(result.text, "Muted.")
        self.assertIn("no tool was called", model.requests[1]["messages"][-1]["content"])
        engine, desktop, _ = make_engine("The screen is locked.", "The screen is locked now.")
        result = engine.respond("lock my screen")
        self.assertFalse(desktop.locked)
        self.assertEqual(result.text, "Sorry, I couldn't do that.")

    def test_claims_backed_by_tools_or_negated_attempts_pass(self):
        from aqua.agent.loop import unsupported_claim
        self.assertFalse(unsupported_claim("Opened Notepad and typed your text.", {"launch_app", "type_text"}))
        self.assertTrue(unsupported_claim("Opened Notepad and typed your text.", {"launch_app"}))
        self.assertFalse(unsupported_claim("Command Prompt was not opened; it is blocked.", {"launch_app"}))
        self.assertFalse(unsupported_claim("The time is 9:21 AM.", set()))
        self.assertFalse(unsupported_claim("I won't open Chrome.", set()))
        self.assertTrue(unsupported_claim("The volume change has been undone.", set()))

    def test_plain_text_tool_call_is_accepted_only_for_visible_tools(self):
        from aqua.agent.loop import parse_text_tool_call
        engine, desktop, _ = make_engine('{"name": "volume", "arguments": {"action": "mute"}}', "Muted.")
        self.assertEqual(engine.respond("mute").text, "Muted.")
        self.assertTrue(desktop.muted)
        visible = ("volume",)
        self.assertEqual(parse_text_tool_call('<tool_call>{"name": "volume", "arguments": {}}</tool_call>', visible),
                         {"function": {"name": "volume", "arguments": {}}})
        for text in ('{"name": "lock_screen", "arguments": {}}', 'Sure: {"name": "volume"}', "{not json}",
                     '{"name": "volume", "arguments": [], "extra": 1}', ""):
            self.assertIsNone(parse_text_tool_call(text, visible), text)

    def test_repeated_finished_call_is_not_run_twice(self):
        engine, desktop, model = make_engine([call("lock_screen", now=True), call("lock_screen", now=True)], "Locked.")
        result = engine.respond("lock my screen")
        self.assertEqual([op.status for op in result.task.operations], ["ok", "duplicate"])
        self.assertEqual(results(model)[1]["message"], "Already done; not repeated.")

    def test_results_restate_the_request_until_it_is_finished(self):
        engine, _, model = make_engine([call("launch_app", app="notepad")], [call("type_text", text="hi")], "Done.")
        engine.respond("open notepad and write hi")
        reminder = results(model, 1)[-1]["next"]
        self.assertIn("'open notepad and write hi'", reminder)
        self.assertIn("call the tool for it now", reminder)

    def test_action_turns_keep_their_tool_calls_in_history(self):
        engine, _, model = make_engine([call("volume", action="down")], "Volume down.", [call("undo_last")], "Undone.")
        engine.respond("turn the volume down")
        engine.respond("undo that")
        history = model.requests[-1]["messages"]
        roles = [m["role"] for m in history]
        self.assertEqual(roles[:5], ["system", "user", "assistant", "tool", "assistant"])
        self.assertEqual(history[2]["tool_calls"][0]["function"]["name"], "volume")
        self.assertNotIn("next", json.loads(history[3]["content"]))  # Reminders are not kept.
        self.assertEqual(history[4]["content"], "Volume down.")
        undo_request = model.requests[2]["messages"][-1]["content"]
        self.assertIn("undo_last would undo: the volume change", undo_request)

    def test_already_open_app_is_focused_not_relaunched(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("notes.txt - Notepad", "notepad.exe", front=False)
        engine, _, _ = make_engine([call("launch_app", app="notepad")], [call("type_text", text="hi")], "Typed it.",
                                   desktop=desktop)
        engine.respond("write hi in notepad")
        self.assertEqual(desktop.launched, [])
        self.assertEqual(desktop.text, {notepad.handle: "hi"})

    def test_reply_helpers(self):
        self.assertEqual(join_phrases(["opened Notepad", "typed your text"]), "Opened Notepad and typed your text.")
        self.assertEqual(join_phrases(["a", "b", "c"]), "A, b and c.")
        self.assertEqual(join_phrases(["", ""]), "")
        self.assertEqual(first_sentence("Done. More text!"), "Done.")
        self.assertEqual(strip_emoji("Hi 👋🏽 there ✨"), "Hi  there ")  # Skin-tone modifiers go too.
        self.assertEqual(strip_emoji("Café, naïve, 5°C"), "Café, naïve, 5°C")


if __name__ == "__main__":
    unittest.main()
