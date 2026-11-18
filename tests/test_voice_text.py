"""Sentence splitting, speakable text and the spoken-length rule (pure text)."""

import unittest

from aqua.voice.text import (
    CODE_NOTICE, REST_NOTICE, SentenceSplitter, SpokenBudget, is_question, speakable, spoken_sentences,
)


def split(text, *, chunk=None):
    splitter = SentenceSplitter()
    if chunk is None:
        out = splitter.feed(text)
    else:
        out = []
        for index in range(0, len(text), chunk):
            out += splitter.feed(text[index:index + chunk])
    return out + splitter.flush()


class SentenceSplitterTests(unittest.TestCase):
    def test_splits_at_terminators_and_newlines(self):
        self.assertEqual(split("Hello there. How are you? Great!\nNext line\nLast"),
                         ["Hello there.", "How are you?", "Great!", "Next line", "Last"])

    def test_does_not_split_abbreviations_numbers_versions_paths_or_urls(self):
        cases = {
            "Use a fruit, e.g. an apple, today.": ["Use a fruit, e.g. an apple, today."],
            "That is, i.e. the same thing.": ["That is, i.e. the same thing."],
            "It costs 3.5 dollars.": ["It costs 3.5 dollars."],
            "Aqua v0.8 is out.": ["Aqua v0.8 is out."],
            r"Open C:\Users\me\notes.txt now.": [r"Open C:\Users\me\notes.txt now."],
            "See https://example.com/a.b?x=1 for more.": ["See https://example.com/a.b?x=1 for more."],
            "Ask Dr. Smith first.": ["Ask Dr. Smith first."],
            "1. Open Notepad.": ["1. Open Notepad."],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(split(text), expected)

    def test_streamed_chunk_boundaries_give_identical_sentences(self):
        text = ("Version v0.8 costs 3.5 units, e.g. here. Visit www.example.com/x.y today! "
                "Is it ready?\nYes. Path D:\\Aqua\\src\\aqua\\chat.py works.")
        expected = split(text)
        self.assertEqual(len(expected), 5)
        for size in range(1, 12):
            with self.subTest(chunk=size):
                self.assertEqual(split(text, chunk=size), expected)

    def test_terminator_at_chunk_end_waits_for_next_character(self):
        splitter = SentenceSplitter()
        self.assertEqual(splitter.feed("It is 3."), [])
        self.assertEqual(splitter.feed("5 now. "), ["It is 3.5 now."])
        self.assertEqual(splitter.feed("Done."), [])
        self.assertEqual(splitter.flush(), ["Done."])

    def test_long_sentence_is_capped_at_a_comma_or_space(self):
        clause = "this clause keeps going with plenty of words"
        text = ", ".join([clause] * 8) + "."
        sentences = split(text)
        self.assertGreater(len(sentences), 1)
        self.assertTrue(all(len(s) <= 220 for s in sentences))
        self.assertEqual(" ".join(sentences).replace("  ", " "), text)
        no_commas = " ".join(["word"] * 80)
        self.assertTrue(all(len(s) <= 220 for s in split(no_commas)))

    def test_fenced_code_is_one_unit_even_across_chunks(self):
        text = "Here it is:\n```python\nx = 1.5\nprint(x)\n```\nRun it."
        for size in (None, 1, 3, 7):
            with self.subTest(chunk=size):
                self.assertEqual(split(text, chunk=size),
                                 ["Here it is:", "```python\nx = 1.5\nprint(x)\n```", "Run it."])

    def test_closing_quote_stays_with_its_sentence(self):
        self.assertEqual(split('He said "stop." Then left.'), ['He said "stop."', "Then left."])

    def test_unterminated_text_is_flushed(self):
        self.assertEqual(split("no terminator here"), ["no terminator here"])
        self.assertEqual(split("   \n\n  "), [])


class SpeakableTests(unittest.TestCase):
    def test_markdown_is_removed(self):
        cases = {
            "## **Bold** heading": "Bold heading",
            "- a *list* item": "a list item",
            "3. numbered step": "numbered step",
            "> quoted `code` here": "quoted code here",
            "__under__ and _em_ text": "under and em text",
            "See [the docs](https://example.com/docs).": "See the docs.",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(speakable(raw), expected)

    def test_code_block_becomes_one_notice(self):
        self.assertEqual(speakable("```python\nprint('hi')\n```"), CODE_NOTICE)
        self.assertEqual(speakable("```\nunterminated"), CODE_NOTICE)

    def test_urls_emoji_and_wake_phrase(self):
        self.assertEqual(speakable("Open https://example.com/page?q=1 now."), "Open a link now.")
        self.assertEqual(speakable("Visit www.example.org."), "Visit a link.")
        self.assertEqual(speakable("Great job 🎉👍!"), "Great job!")
        self.assertEqual(speakable("Say Hey Aqua to start."), "Say to start.")
        self.assertEqual(speakable("Hey, Aqua is ready."), "is ready.")
        self.assertNotIn("aqua", speakable("HEY AQUA hello").casefold())

    def test_nothing_speakable_returns_empty(self):
        for raw in ("", "   ", "---", "🎉", "**", "> ", "Hey Aqua.", None):
            with self.subTest(raw=raw):
                self.assertEqual(speakable(raw), "")


class SpokenLengthTests(unittest.TestCase):
    def test_three_or_fewer_sentences_are_spoken_in_full(self):
        self.assertEqual(spoken_sentences(["A.", "B.", "C."]), ["A.", "B.", "C."])
        self.assertEqual(spoken_sentences([]), [])

    def test_longer_replies_say_the_rest_is_on_screen(self):
        self.assertEqual(spoken_sentences(["A.", "B.", "C.", "D.", "E."]), ["A.", "B.", "C.", REST_NOTICE])

    def test_final_question_is_always_spoken(self):
        self.assertEqual(spoken_sentences(["A.", "B.", "C.", "D.", "Shall I open it?"]),
                         ["A.", "B.", "C.", REST_NOTICE, "Shall I open it?"])
        self.assertEqual(spoken_sentences(["A.", "B.", "C.", "Shall I?"]), ["A.", "B.", "C.", "Shall I?"])
        self.assertEqual(spoken_sentences(["A?", "B.", "C.", "D.", "E."]), ["A?", "B.", "C.", REST_NOTICE])

    def test_budget_is_incremental_and_resets(self):
        budget = SpokenBudget(2)
        self.assertEqual(budget.push("A."), ["A."])
        self.assertEqual(budget.push(""), [])
        self.assertEqual(budget.push("B."), ["B."])
        self.assertEqual(budget.push("C."), [])
        self.assertEqual(budget.finish(), [REST_NOTICE])
        self.assertEqual(budget.push("Fresh."), ["Fresh."])

    def test_question_detection(self):
        self.assertTrue(is_question('Ready?"'))
        self.assertFalse(is_question("Ready."))


if __name__ == "__main__":
    unittest.main()
