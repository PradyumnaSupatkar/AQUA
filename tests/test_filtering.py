"""Answer-channel validation never silently edits legitimate reply text."""

import unittest

from aqua.brain.filtering import AnswerChannelGuard
from aqua.brain.ollama import OllamaProtocolError


def guarded(chunks):
    response = AnswerChannelGuard()
    return "".join(response.feed(chunk) for chunk in chunks) + response.finish()


class AnswerChannelGuardTests(unittest.TestCase):
    def test_plain_text_streams_without_waiting_for_completion(self):
        response = AnswerChannelGuard()
        self.assertEqual(response.feed("Hello "), "Hello ")
        self.assertEqual(response.feed("Aqua."), "Aqua.")
        self.assertEqual(response.finish(), "")

    def test_raw_reasoning_is_rejected_before_output_at_every_chunk_boundary(self):
        for text in ("<think>private reasoning</think>Answer", " \n<THINK>private", "</think>Answer"):
            for boundary in range(len(text) + 1):
                with self.subTest(text=text[:10], boundary=boundary):
                    response = AnswerChannelGuard()
                    visible = []
                    with self.assertRaises(OllamaProtocolError):
                        visible.append(response.feed(text[:boundary]))
                        visible.append(response.feed(text[boundary:]))
                    self.assertEqual("".join(visible), "")

    def test_reasoning_marker_split_into_single_characters_never_leaks(self):
        response = AnswerChannelGuard()
        for character in "\n<think":
            self.assertEqual(response.feed(character), "")
        with self.assertRaises(OllamaProtocolError):
            response.feed(">private reasoning")

    def test_literal_tags_in_explanations_and_code_are_preserved(self):
        for text in (
            "The <think> tag is model markup, not HTML.",
            "Use `<think>example</think>` literally in this test.",
            "```xml\n<think>literal example</think>\n```",
            "2 < 3, x > 0, <b>hello</b>, <thinking>ordinary</thinking>.",
        ):
            with self.subTest(text=text):
                self.assertEqual(guarded(list(text)), text)

    def test_whitespace_and_normal_prefixes_are_not_removed(self):
        self.assertEqual(guarded([" \n", "Hello", "\n"]), " \nHello\n")
        self.assertEqual(guarded([" \n\t"]), " \n\t")
        self.assertEqual(guarded(["<thi", "ngs>ordinary"]), "<things>ordinary")

    def test_incomplete_reasoning_prefixes_fail_closed(self):
        for marker in ("<", "<t", "<thi", "<think", "</", "</thi", "</think"):
            with self.subTest(marker=marker):
                response = AnswerChannelGuard()
                self.assertEqual(response.feed(marker), "")
                with self.assertRaisesRegex(OllamaProtocolError, "incomplete reasoning marker"):
                    response.finish()

    def test_successful_answer_is_never_rewritten_after_initial_acceptance(self):
        text = "Answer: <think>these are literal tags</think>."
        self.assertEqual(guarded(["Answer: ", "<think>", "these are literal tags", "</think>."]), text)

    def test_rejected_channel_cannot_be_resumed(self):
        response = AnswerChannelGuard()
        with self.assertRaises(OllamaProtocolError):
            response.feed("<think>private")
        with self.assertRaises(OllamaProtocolError):
            response.feed("Answer")
        with self.assertRaises(OllamaProtocolError):
            response.finish()

    def test_rejected_nontext_does_not_change_guard_state(self):
        response = AnswerChannelGuard()
        with self.assertRaises(ValueError):
            response.feed(None)
        self.assertEqual(response.feed("Visible"), "Visible")

    def test_finish_is_idempotent_and_guards_are_not_reused(self):
        response = AnswerChannelGuard()
        self.assertEqual(response.finish(), "")
        self.assertEqual(response.finish(), "")
        with self.assertRaisesRegex(ValueError, "finished"):
            response.feed("next response")


if __name__ == "__main__":
    unittest.main()
