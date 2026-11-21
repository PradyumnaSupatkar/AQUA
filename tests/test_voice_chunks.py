"""Chunking long sentences for speech, and the sentence-based spoken budget."""

import unittest

from aqua.voice.engines import FakeEngine
from aqua.voice.output import SpeechOutput
from aqua.voice.player import FakePlayer
from aqua.voice.text import (
    CHUNK_EARLY_MAX_WORDS, CHUNK_MAX_WORDS, REST_NOTICE, SentenceSplitter, SpokenBudget,
)


def chunks(text, *, size=None):
    splitter = SentenceSplitter()
    pieces = [text] if size is None else [text[i:i + size] for i in range(0, len(text), size)]
    out = [chunk for piece in pieces for chunk in splitter.feed_chunks(piece)]
    return out + splitter.flush_chunks()


def words(chunk):
    return len(chunk.text.split())


LONG = ("Sure! Formula One is the highest class of international open-wheel single-seater racing, "
        "sanctioned by the FIA, where ten teams build their own cars and twenty drivers compete across "
        "roughly two dozen races each season on circuits and street tracks around the world. "
        "Would you like to know more?")


class ChunkingTests(unittest.TestCase):
    def test_long_sentence_is_cut_with_small_early_chunks_and_no_text_lost(self):
        out = chunks(LONG)
        self.assertEqual(" ".join(c.text for c in out), LONG)
        self.assertEqual([c.sentence_end for c in out], [True, False, False, True, True])
        self.assertLessEqual(max(words(c) for c in out[:2]), CHUNK_EARLY_MAX_WORDS)
        self.assertLessEqual(max(words(c) for c in out), CHUNK_MAX_WORDS)

    def test_streamed_chunks_match_whole_text_for_any_boundary(self):
        expected = chunks(LONG)
        for size in (1, 2, 3, 5, 8, 13):
            with self.subTest(size=size):
                self.assertEqual(chunks(LONG, size=size), expected)

    def test_natural_breaks_after_six_words(self):
        text = ("Python is popular for data work and scripting, it has a huge library ecosystem; "
                "beginners find it readable: the syntax is clean \u2014 and it runs almost everywhere today.")
        out = chunks(text)
        self.assertEqual([c.text for c in out[:2]], [
            "Python is popular for data work and scripting,",
            "it has a huge library ecosystem;",
        ])
        self.assertTrue(all(c.text.endswith((",", ";", ":", "\u2014", ".")) for c in out))
        self.assertEqual(" ".join(c.text for c in out), text)

    def test_breaks_before_six_words_are_not_used(self):
        out = chunks("Yes, okay, sure, that works for me today.")
        self.assertEqual([c.text for c in out], ["Yes, okay, sure, that works for me today."])

    def test_no_fragment_under_three_words_except_at_sentence_end(self):
        text = " ".join(f"word{i}" for i in range(1, 12)) + "."  # 11 words: cap 10 would leave 1
        out = chunks(text)
        self.assertEqual(len(out), 1)
        self.assertTrue(all(words(c) >= 3 for c in chunks(LONG) if not c.sentence_end))
        self.assertEqual([c.text for c in chunks("Done. Ok.")], ["Done.", "Ok."])  # short sentences stay

    def test_existing_no_split_rules_survive_chunking(self):
        cases = [
            "Use a fruit, e.g. an apple, today.",
            "It costs 3.5 dollars.",
            "Aqua v0.8 is out.",
            r"Open C:\Users\me\notes.txt now.",
            "See https://example.com/a.b?x=1 for more.",
            "Open index.js and chat.py now.",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual([(c.text, c.sentence_end) for c in chunks(text)], [(text, True)])

    def test_numbers_and_times_are_not_breaks(self):
        text = "At 10:30 we had 1,000 people and many more guests."  # 10 words, no real break
        self.assertEqual([c.text for c in chunks(text)], [text])

    def test_hey_aqua_is_never_split_across_chunks(self):
        text = "one two three four five six seven eight nine hey Aqua is my name and that is all."
        out = chunks(text)
        self.assertFalse(any(c.text.casefold().endswith("hey") for c in out))
        text = "one two three four five six seven, eight nine, hey, Aqua is listening now."
        self.assertFalse(any(c.text.casefold().rstrip(",").endswith("hey") for c in chunks(text)))

    def test_code_fence_is_one_chunk(self):
        fence = "```python\n" + " ".join(f"x{i} = {i}," for i in range(30)) + "\n```"
        out = chunks(fence + "\nDone.")
        self.assertEqual([c.text for c in out], [fence, "Done."])

    def test_unterminated_text_and_reset(self):
        splitter = SentenceSplitter()
        splitter.feed_chunks("one two three four five six seven eight nine ten eleven twelve ")
        splitter.reset()
        self.assertEqual(splitter.flush_chunks(), [])


class SentenceBudgetTests(unittest.TestCase):
    def test_chunks_of_the_first_three_sentences_are_all_spoken(self):
        budget = SpokenBudget(3)
        spoken = []
        for text, end in (("A1,", False), ("A2.", True), ("B.", True), ("C1;", False), ("C2,", False), ("C3.", True),
                          ("D1,", False), ("D2.", True), ("Want more?", True)):
            spoken += budget.push(text, end=end)
        spoken += budget.finish()
        self.assertEqual(spoken, ["A1,", "A2.", "B.", "C1;", "C2,", "C3.", REST_NOTICE, "Want more?"])

    def test_multi_chunk_final_question_is_spoken_whole(self):
        budget = SpokenBudget(1)
        spoken = budget.push("A.") + budget.push("B.")
        spoken += budget.push("Should I open it,", end=False) + budget.push("or wait?", end=True)
        spoken += budget.finish()
        self.assertEqual(spoken, ["A.", REST_NOTICE, "Should I open it,", "or wait?"])

    def test_empty_pieces_do_not_count_as_sentences(self):
        budget = SpokenBudget(1)
        self.assertEqual(budget.push("", end=True), [])
        self.assertEqual(budget.push("Real.", end=True), ["Real."])
        self.assertEqual(budget.finish(), [])


class PipelineChunkTests(unittest.TestCase):
    def test_long_reply_is_synthesized_in_chunks_but_limited_by_sentences(self):
        engine = FakeEngine()
        speaker = SpeechOutput(engine, FakePlayer(), warn=lambda _m: None)
        self.addCleanup(speaker.close)
        reply = LONG.replace("Would you like to know more?", "Second. Third. Fourth. Fifth.")
        for index in range(0, len(reply), 4):
            speaker.feed(reply[index:index + 4])
        speaker.finish_turn()
        self.assertTrue(speaker.wait(5))
        self.assertEqual(engine.texts[0], "Sure!")
        self.assertEqual(engine.texts[1], "Formula One is the highest class of international open-wheel single-seater")
        self.assertIn("Second.", engine.texts)
        self.assertNotIn("Third.", engine.texts)  # Sure!, the long sentence, Second. = 3 sentences
        self.assertEqual(engine.texts[-1], REST_NOTICE)


if __name__ == "__main__":
    unittest.main()
