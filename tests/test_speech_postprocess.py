"""Transcript fixes before the engine: 'write' homophones and spoken numbers (real Parakeet outputs)."""

import unittest

from aqua.speech.postprocess import fix_write, prepare, spoken_numbers, words_to_int


class WriteTests(unittest.TestCase):
    def test_homophones_become_write_only_with_a_typing_destination(self):
        cases = {
            "Right, this is a second line into notepad.": "Write this is a second line into notepad.",
            "Ride by milk and eggs in the notepad.": "Write by milk and eggs in the notepad.",
            "Right, buy milk and eggs into notepad": "Write buy milk and eggs into notepad",
            "bright hello I am Aqua into the notepad window": "write hello I am Aqua into the notepad window",
            "Rite this down to my notes please.": "Write this down to my notes please.",
            "Wright meeting at noon in VS Code.": "Write meeting at noon in VS Code.",
        }
        for heard, wanted in cases.items():
            text, note = fix_write(heard)
            self.assertEqual(text, wanted, heard)
            self.assertIn("-> 'write'", note)

    def test_nothing_else_changes(self):
        for heard in ("Right, hello I am Aqua.", "Right meeting at noon.", "Right, turn it up to eleven.",
                      "Right, go to the kitchen", "right now open notepad", "Bright colours look good in the sun",
                      "Write hello into notepad", "Alright, put it into notepad", "Open notepad and write right away"):
            self.assertEqual(fix_write(heard), (heard, None), heard)

    def test_answering_which_app_counts(self):
        text, note = fix_write("Right meeting at noon.", awaiting_app=True)
        self.assertEqual(text, "Write meeting at noon.")
        self.assertIn("Which app", note)

    def test_apps_known_to_the_session_count(self):
        self.assertEqual(fix_write("Right hello into Obsidian")[0], "Right hello into Obsidian")
        self.assertEqual(fix_write("Right hello into Obsidian", is_app=lambda name: name == "obsidian")[0],
                         "Write hello into Obsidian")


class NumberTests(unittest.TestCase):
    def test_parakeet_transcripts_from_the_benchmark(self):
        cases = {
            "Set the volume to thirty.": "Set the volume to 30.",
            "Go back ten seconds.": "Go back 10 seconds.",
            "Jump to two minutes thirty.": "Jump to 2:30.",
            "Set the quality to ten ADP.": "Set the quality to 1080p.",
            "Set the quality to ten Arep": "Set the quality to 1080p",
            "Set the quality to ten eighty p.": "Set the quality to 1080p.",
            "Play this video at one point five x speed": "Play this video at 1.5x speed",
            "play it at one and a half times speed": "play it at 1.5x speed",
            "Jump to two thirty.": "Jump to 2:30.",
            "write meeting at two thirty tomorrow into notepad": "write meeting at 2:30 tomorrow into notepad",
            "Turn the brightness down by twenty percent": "Turn the brightness down by 20 percent",
            "skip forward one hundred and twenty seconds": "skip forward 120 seconds",
            "watch it in four K": "watch it in 4K",
        }
        for heard, wanted in cases.items():
            self.assertEqual(spoken_numbers(heard)[0], wanted, heard)

    def test_other_words_are_left_alone(self):
        for heard in ("Play the second one.", "write one two three into notepad", "Open notepad.",
                      "What time is it?", "I have two cats", "go to one drive", "set an alarm for later",
                      "Lock my screen."):
            self.assertEqual(spoken_numbers(heard), (heard, None), heard)

    def test_words_to_int(self):
        self.assertEqual([words_to_int(w) for w in ("thirty", "twenty-five", "one hundred and five", "zero")],
                         [30, 25, 105, 0])
        self.assertIsNone(words_to_int("lots"))


class SessionWiringTests(unittest.TestCase):
    def test_prepare_spoken_uses_the_agent_state_and_prints_fixes(self):
        import io
        import types
        from contextlib import redirect_stdout
        from aqua.chat import prepare_spoken
        agent = types.SimpleNamespace(awaiting_app=True, context=types.SimpleNamespace(
            apps=types.SimpleNamespace(find=lambda name: types.SimpleNamespace(kind="found" if name == "obsidian"
                                                                               else "none"))))
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(prepare_spoken("Right meeting at noon.", agent, diagnostics=True), "Write meeting at noon.")
        self.assertIn("Speech fix: 'Right' -> 'write'", out.getvalue())
        agent.awaiting_app = False
        self.assertEqual(prepare_spoken("Right hello into Obsidian", agent), "Write hello into Obsidian")
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(prepare_spoken("Right hello I am Aqua", None, diagnostics=True), "Right hello I am Aqua")
        self.assertEqual(out.getvalue(), "")

    def test_engine_reports_awaiting_app(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from action_core_helpers import make_engine
        engine, _, _ = make_engine()
        self.assertFalse(engine.awaiting_app)
        engine.respond("write meeting at noon")  # Nothing to type into: the host asks which app.
        self.assertTrue(engine.awaiting_app)


class FillerTests(unittest.TestCase):
    def test_leading_fillers_go_before_the_other_fixes(self):
        from aqua.speech.postprocess import strip_fillers
        # Live Parakeet transcripts from hands-free mode.
        self.assertEqual(prepare("Well jump to one minute thirty seconds")[0], "jump to 1:30")
        self.assertEqual(prepare("Well Right meeting at noon into notepad")[0], "Write meeting at noon into notepad")
        self.assertEqual(prepare("Well Right meeting at noon.", awaiting_app=True)[0], "Write meeting at noon.")
        self.assertEqual(strip_fillers("Um, so, open notepad"), ("open notepad", "dropped leading filler 'Um, so'"))
        self.assertEqual(strip_fillers("Hey, pause the video")[0], "pause the video")

    def test_a_lone_filler_or_a_filler_inside_a_word_stays(self):
        from aqua.speech.postprocess import strip_fillers
        for text in ("okay", "Okay.", "well", "Wellington is in New Zealand", "Sonos speaker off", "open notepad",
                     "Oklahoma weather"):
            self.assertEqual(strip_fillers(text), (text, None), text)

    def test_diagnostics_show_the_dropped_filler(self):
        _text, notes = prepare("Well jump to two thirty")
        self.assertEqual(notes[0], "dropped leading filler 'Well'")


class PrepareTests(unittest.TestCase):
    def test_both_fixes_with_notes(self):
        text, notes = prepare("Right the volume is at thirty into notepad")
        self.assertEqual(text, "Write the volume is at 30 into notepad")
        self.assertEqual(len(notes), 2)
        self.assertEqual(prepare("Open notepad."), ("Open notepad.", []))


if __name__ == "__main__":
    unittest.main()
