"""aqua memory list|forget|clear, --no-memory, and memory lines in --diagnostics."""

from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import Mock, patch

from aqua import chat, cli
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, ModelInfo

from action_core_helpers import call, make_engine
from test_memory_store import temp_store


def run(action, store, **extra):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        status = cli.memory_command(Namespace(memory_action=action, **extra), store)
    return status, out.getvalue(), err.getvalue()


class MemoryCommandTests(unittest.TestCase):
    def setUp(self):
        self.store = temp_store()
        self.store.add_fact("My projects are in D:\\Projects")
        self.store.set_preference("browser", "youtube.com", "Brave")

    def test_the_parser_knows_the_command(self):
        args = cli._parser().parse_args(["memory", "forget", "f1"])
        self.assertEqual((args.command, args.memory_action, args.id), ("memory", "forget", "f1"))
        self.assertTrue(cli._parser().parse_args(["memory", "clear", "--yes"]).yes)

    def test_list(self):
        status, out, _ = run("list", self.store)
        self.assertEqual(status, 0)
        self.assertIn("f1  My projects are in D:\\Projects.", out)
        self.assertIn("p2  open youtube.com in Brave", out)
        self.assertIn("encrypted", out)

    def test_forget_by_id(self):
        self.assertEqual(run("forget", self.store, id="f1")[0], 0)
        self.assertEqual([e.id for e in self.store.entries()], ["p2"])
        status, _, err = run("forget", self.store, id="f9")
        self.assertEqual(status, 1)
        self.assertIn("No remembered item", err)

    def test_clear_asks_unless_yes(self):
        with patch("builtins.input", return_value="no"):
            self.assertEqual(run("clear", self.store, yes=False)[0], 1)
        self.assertEqual(len(self.store.entries()), 2)
        with patch("builtins.input", return_value="yes"):
            self.assertEqual(run("clear", self.store, yes=False)[0], 0)
        self.assertEqual(self.store.entries(), [])
        self.store.add_fact("I live in Pune")
        self.assertIn("Forgot 1", run("clear", self.store, yes=True)[1])


class NoMemoryFlagTests(unittest.TestCase):
    def invoke(self, argv, target):
        with patch(f"aqua.cli.{target}", return_value=0) as runner, redirect_stdout(StringIO()), \
                redirect_stderr(StringIO()):
            cli.main(argv)
        return runner.call_args.kwargs

    def test_no_memory_reaches_the_new_engine_only(self):
        for command, target in (("chat", "run_text_chat"), ("voice-chat", "run_voice_chat"),
                                ("handsfree", "run_handsfree")):
            self.assertIs(self.invoke([command, "--engine", "new", "--no-memory"], target)["memory"], False)
            self.assertNotIn("memory", self.invoke([command, "--engine", "new"], target))

    def test_chat_passes_it_to_the_engine_builder(self):
        client = Mock()
        client.check_model.return_value = ModelInfo(DEFAULT_CHAT_MODEL, "t", "qwen3", "non_thinking")
        engine, _, _ = make_engine()
        with patch("aqua.chat.OllamaClient", return_value=client), \
                patch("aqua.agent.loop.build_engine", return_value=engine) as build, \
                patch("builtins.input", side_effect=["/quit"]), redirect_stdout(StringIO()):
            chat.run_text_chat(engine="new", memory=False)
        self.assertIs(build.call_args.kwargs["memory"], False)

    def test_build_engine_without_memory_has_no_store_or_tools(self):
        from aqua.agent.loop import build_engine
        from aqua.brain.ollama import GenerationSettings
        from aqua.tools.fake import FakeDesktop
        engine = build_engine(Mock(), model="m", settings=GenerationSettings(), desktop=FakeDesktop(), memory=False)
        self.assertIsNone(engine.context.memory)
        self.assertNotIn("remember", engine.registry.names)
        store = temp_store()
        engine = build_engine(Mock(), model="m", settings=GenerationSettings(), desktop=FakeDesktop(),
                              memory_store=store)
        self.assertIs(engine.context.memory, store)


class DiagnosticsTests(unittest.TestCase):
    def test_used_and_saved_memory_is_shown(self):
        store = temp_store()
        store.add_fact("My projects are in D:\\Projects")
        engine, _, _ = make_engine([call("remember", request=None, kind="fact", text="My projects are in E:\\Code")])
        engine.context.memory = store
        result = engine.respond("remember that my projects are in E:\\Code")
        lines = chat.memory_diagnostics(engine, result)
        self.assertEqual(lines[0], "Memory: gave the model f1: My projects are in D:\\Projects.")
        self.assertTrue(lines[1].startswith("Memory: remember ok: Saved fact f2"), lines)

    def test_nothing_is_shown_when_memory_was_not_used(self):
        engine, _, _ = make_engine("Hi.")
        engine.context.memory = temp_store()
        self.assertEqual(chat.memory_diagnostics(engine, engine.respond("hello")), [])


if __name__ == "__main__":
    unittest.main()
