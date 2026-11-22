"""Group B: reliable typing (real key presses, paced, verified), finding an already-open window,
dictation across a clarification, no dead "Go ahead?" questions, and yes/no typos."""

import unittest

from aqua.agent.confirm import classify_reply
from aqua.tools.fake import FakeDesktop
from aqua.tools.input import dictated
from aqua.tools.win32 import key_plan

from action_core_helpers import call, make_engine

# A US layout stand-in for VkKeyScanEx: low byte the key, high byte 1 = Shift, 6 = Ctrl+Alt.
US = {**{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"},
      **{c: 0x100 | ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
      **{c: ord(c) for c in "0123456789"}, " ": 0x20, ",": 0xBC, ".": 0xBE, "!": 0x131, "?": 0x1BF, "\t": 0x09,
      "€": 0x645}


def scan(ch):
    return US.get(ch, -1)


class KeyPlanTests(unittest.TestCase):
    def test_layout_characters_become_real_key_presses_with_shift(self):
        self.assertEqual(key_plan("Hi!", scan), [("key", ord("H"), True), ("key", ord("I"), False),
                                                  ("key", 0x31, True)])
        self.assertEqual(key_plan("a 1", scan), [("key", ord("A"), False), ("key", 0x20, False), ("key", 0x31, False)])

    def test_everything_else_is_sent_as_unicode(self):
        # Tab could move focus and Ctrl+Alt (AltGr) could fire shortcuts; unknown characters have no key.
        self.assertEqual(key_plan("\t€é😀", scan), [("unicode", "\t"), ("unicode", "€"), ("unicode", "é"),
                                                   ("unicode", "😀")])


class ReadbackTests(unittest.TestCase):
    def test_garbled_text_is_reported_not_claimed(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("Untitled - Notepad", "notepad.exe")
        desktop.type_text = lambda handle, text: (desktop.text.__setitem__(handle, "Hello aaaaaaaaa"), len(text))[1]
        engine, _, model = make_engine([call("type_text", text="Hello I am Aqua", app="Notepad")],
                                       "I couldn't type it correctly.", desktop=desktop)
        result = engine.respond("write Hello I am Aqua into Notepad")
        self.assertEqual(result.task.operations[0].status, "failed")
        self.assertIn("did not arrive correctly", result.task.operations[0].message)
        self.assertEqual(desktop.text[notepad.handle], "Hello aaaaaaaaa")


class ExistingWindowTests(unittest.TestCase):
    def test_window_named_in_the_request_is_used_when_the_model_omits_the_app(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("*notes - Notepad", "notepad.exe", front=False)
        engine, _, _ = make_engine([call("type_text", text="hello I am your assistant")], "Typed it.",
                                   desktop=desktop)
        result = engine.respond("Write hello I am your assistant into the notepad window")
        self.assertEqual(result.outcome, "action")
        self.assertEqual(desktop.text, {notepad.handle: "hello I am your assistant"})

    def test_minimised_notepad_is_found_restored_and_typed_into(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
        desktop.show(notepad.handle, "minimise")
        engine, _, _ = make_engine([call("type_text", text="hi there", app="Notepad")], "Typed it.", desktop=desktop)
        engine.respond("type hi there in notepad")
        self.assertEqual(desktop.text, {notepad.handle: "hi there"})

    def test_speech_split_app_name_is_found(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
        engine, _, _ = make_engine([call("type_text", text="hello")], "Typed it.", desktop=desktop)
        engine.respond("write hello into the note pad")
        self.assertEqual(desktop.text, {notepad.handle: "hello"})


class ClarificationGroundingTests(unittest.TestCase):
    def test_dictation_survives_a_which_app_question(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
        engine, _, _ = make_engine([call("type_text", text="meeting at noon")],
                                   [call("type_text", text="meeting at noon", app="Notepad")], "Typed it.",
                                   desktop=desktop)
        desktop.focus(1)  # Aqua's terminal in front: no target, so the host asks.
        asked = engine.respond("write meeting at noon")
        self.assertEqual(asked.text, "Which app should I type that into?")
        typed = engine.respond("Notepad")
        self.assertEqual(typed.outcome, "action")  # Typed at once, no read-back.
        self.assertEqual(desktop.text, {notepad.handle: "meeting at noon"})

    def test_grounding_reaches_back_only_across_questions(self):
        self.assertTrue(dictated("meeting at noon", "Notepad", ("write meeting at noon",)))
        self.assertFalse(dictated("meeting at noon", "Notepad"))
        engine, _, _ = make_engine("Hello!", "Fine.")
        engine.respond("write meeting at noon somewhere")  # Answered, not asked.
        engine.respond("Notepad")
        self.assertEqual(engine.context.earlier, ())


class DeadQuestionTests(unittest.TestCase):
    def test_model_written_go_ahead_becomes_a_host_confirmation(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
        engine, _, _ = make_engine("I'll type: 'Hello from Aqua'. Go ahead?",
                                   [call("type_text", text="Hello from Aqua", app="Notepad")], "Typed it.",
                                   desktop=desktop)
        asked = engine.respond("put a greeting into notepad")
        self.assertEqual(asked.outcome, "confirm")
        self.assertIsNotNone(engine.pending)
        self.assertEqual(desktop.typed, [])
        engine.respond("yes")
        self.assertEqual(desktop.text, {notepad.handle: "Hello from Aqua"})

    def test_other_trailing_checks_count_too(self):
        for reply in ("I'll mute it. Is that okay?", "Turning it down. Sound good?", "Ready to lock. Proceed?"):
            engine, _, model = make_engine(reply, "Fine.")
            engine.respond("the volume, lock things, whatever")
            self.assertIn('request \\"unsure\\"', str(model.requests[-1]["messages"][-1]).replace('"', '\\"'), reply)


class LineBreakAndClaimTests(unittest.TestCase):
    POEM = "Rain falls gently on the earth,\nA soft melody, a quiet birth."

    def test_poem_with_line_breaks_is_read_back_as_one_line_then_typed(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
        engine, _, _ = make_engine([call("type_text", text=self.POEM, app="Notepad")], "Typed the poem.",
                                   desktop=desktop)
        asked = engine.respond("write a short poem about rain into Notepad")
        self.assertEqual(asked.outcome, "confirm")
        self.assertIn("one line", asked.text)
        engine.respond("yes")
        self.assertEqual(desktop.text[notepad.handle],
                         "Rain falls gently on the earth, A soft melody, a quiet birth.")

    def test_a_refused_attempt_does_not_back_a_success_claim(self):
        from aqua.agent.loop import unsupported_claim
        self.assertTrue(unsupported_claim("I have written a short poem into Notepad.", set()))
        self.assertFalse(unsupported_claim("I have written a short poem into Notepad.", {"type_text"}))
        for negated in ("Nothing was typed.", "I couldn't open Command Prompt.", "Notepad wasn't opened."):
            self.assertFalse(unsupported_claim(negated, set()), negated)


class TypoTests(unittest.TestCase):
    def test_close_typos_for_yes_and_no(self):
        for text in ("ys", "yse", "yess", "yesss", "yeahh", "Yess.", "okk"):
            self.assertEqual(classify_reply(text), "yes", text)
        for text in ("noo", "nooo", "nop", "Noo!"):
            self.assertEqual(classify_reply(text), "no", text)

    def test_real_words_and_sentences_are_not_typos(self):
        for text in ("now", "yet", "new", "noon", "know", "you", "so", "on", "open notepad", "yes no", "yes and no"):
            self.assertIsNone(classify_reply(text), text)

    def test_typo_yes_runs_a_pending_action(self):
        engine, desktop, _ = make_engine([call("power", action="sleep")], "Going to sleep.")
        engine.respond("put the computer to sleep")
        engine.respond("yse")
        self.assertEqual(desktop.power, [("sleep", 0)])


if __name__ == "__main__":
    unittest.main()
