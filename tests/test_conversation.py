"""Context stays bounded, ordered, transactional, and entirely in memory."""

import unittest

from aqua.brain.conversation import Conversation, DEFAULT_SYSTEM_PROMPT


class ConversationTests(unittest.TestCase):
    def test_new_conversation_contains_system_prompt_and_pending_user_only(self):
        conversation = Conversation()
        self.assertEqual(conversation.messages_for(" Hello Aqua "), [
            {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": "Hello Aqua"},
        ])
        self.assertEqual(conversation.turn_count, 0)
        self.assertEqual(conversation.history, [])

    def test_complete_turns_preserve_roles_and_order(self):
        conversation = Conversation(system_prompt="System")
        conversation.commit("My name is Alex.", "Hello Alex.")
        conversation.commit("What is my name?", "Alex.")
        messages = conversation.messages_for("Thank you.")
        self.assertEqual([item["role"] for item in messages], [
            "system", "user", "assistant", "user", "assistant", "user",
        ])
        self.assertEqual([item["content"] for item in messages], [
            "System", "My name is Alex.", "Hello Alex.", "What is my name?", "Alex.", "Thank you.",
        ])
        self.assertEqual(conversation.turn_count, 2)

    def test_oldest_whole_pairs_are_removed_at_turn_limit(self):
        conversation = Conversation(max_turns=2, system_prompt="S")
        for number in range(3):
            conversation.commit(f"user {number}", f"answer {number}")
        self.assertEqual(conversation.turn_count, 2)
        self.assertEqual([item["content"] for item in conversation.history], [
            "user 1", "answer 1", "user 2", "answer 2",
        ])

    def test_character_budget_counts_system_and_complete_pairs(self):
        conversation = Conversation(max_chars=17, system_prompt="System")
        conversation.commit("one", "ONE")
        conversation.commit("two", "TWO")
        self.assertEqual(conversation.history, [
            {"role": "user", "content": "two"}, {"role": "assistant", "content": "TWO"},
        ])
        self.assertLessEqual(sum(len(item["content"]) for item in conversation.messages_for("new")), 17)

    def test_preparing_large_pending_message_does_not_erase_history_on_failure(self):
        conversation = Conversation(max_chars=21, system_prompt="S")
        conversation.commit("first", "FIRST")
        before = conversation.history
        request = conversation.messages_for("a longer message")
        self.assertEqual([item["role"] for item in request], ["system", "user"])
        self.assertEqual(conversation.history, before)
        self.assertEqual(conversation.turn_count, 1)
        # A failed stream never calls commit, so the old pair is still usable.
        self.assertEqual(conversation.messages_for("ok")[1:-1], before)

    def test_requests_and_history_are_independent_copies(self):
        conversation = Conversation(system_prompt="S")
        conversation.commit("hello", "hi")
        request = conversation.messages_for("next")
        history = conversation.history
        request[1]["content"] = "modified"
        history[1]["content"] = "modified"
        history.clear()
        self.assertEqual(conversation.history, [
            {"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"},
        ])

    def test_clear_forgets_turns_but_keeps_system_prompt(self):
        conversation = Conversation(system_prompt="Keep this system prompt")
        conversation.commit("private question", "private answer")
        conversation.clear()
        self.assertEqual(conversation.turn_count, 0)
        self.assertEqual(conversation.history, [])
        self.assertEqual(conversation.messages_for("new")[0]["content"], "Keep this system prompt")

    def test_oversized_completed_pair_is_omitted_without_truncating_prior_history(self):
        conversation = Conversation(max_chars=20, system_prompt="S")
        conversation.commit("old", "answer")
        before = conversation.history
        conversation.commit("new", "x" * 20)
        self.assertEqual(conversation.history, before)

    def test_invalid_limits_and_system_prompts_are_rejected(self):
        for field in ("max_turns", "max_chars"):
            for value in (0, -1, 1.5, True, "8"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    Conversation(**{field: value})
        for prompt in ("", " \n", None):
            with self.subTest(prompt=prompt), self.assertRaises(ValueError):
                Conversation(system_prompt=prompt)
        with self.assertRaisesRegex(ValueError, "system prompt"):
            Conversation(max_chars=3, system_prompt="longer")

    def test_invalid_user_or_assistant_turns_never_change_history(self):
        conversation = Conversation(max_chars=20, system_prompt="S")
        for text in ("", " \n", None, "x" * 20):
            with self.subTest(text=text), self.assertRaises(ValueError):
                conversation.messages_for(text)
        for answer in ("", " \n", None):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                conversation.commit("hello", answer)
        self.assertEqual(conversation.history, [])


if __name__ == "__main__":
    unittest.main()
