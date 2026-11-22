"""Unit tests for the new action core: registry, policy, confirmations, selection, undo, prompt."""

from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from aqua.agent.confirm import ConfirmationGate, PendingAction, classify_reply
from aqua.agent.policy import CLARIFY, CONFIRM, DENY, RUN, Policy, clarify, deny, read_user_blocklist
from aqua.agent.prompt import SYSTEM_PROMPT, request_context, user_message
from aqua.agent.registry import (DESTRUCTIVE, DISRUPTIVE, READ, SAFE, Registry, SchemaError, Tool,
                                 ToolOutcome, UndoRecord, validate_arguments)
from aqua.agent.selection import CORE_TOOLS, MAX_VISIBLE_TOOLS, matched_groups, select_tools, wants_no_action
from aqua.agent.undo import UndoStack


def tool(name="thing", tier=SAFE, **options):
    return Tool(name=name, group=options.pop("group", "apps"), description="d",
                parameters=options.pop("parameters", {"type": "object", "properties": {}}),
                run=lambda args, ctx: ToolOutcome("ok", "done"), tier=tier, **options)


def milestone_one_shaped_registry():
    """Stub tools with the M1 names and groups, so selection is tested without aqua.tools."""
    groups = {"launch_app": "apps", "type_text": "input", "undo_last": "undo", "list_windows": "windows",
              "window_action": "windows", "get_time": "system", "get_battery": "system", "volume": "audio",
              "media": "audio", "lock_screen": "power", "look_up": "knowledge", "web_search": "web",
              "brightness": "display"}
    read_only = {"list_windows", "get_time", "get_battery", "look_up"}
    return Registry([tool(name, group=group, tier=READ if name in read_only else SAFE)
                     for name, group in groups.items()])


class ArgumentValidationTests(unittest.TestCase):
    schema = {"type": "object", "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 5},
        "level": {"type": "integer", "minimum": 0, "maximum": 100},
        "mode": {"type": "string", "enum": ["up", "down"]},
        "flag": {"type": "boolean"}},
        "required": ["text"]}

    def test_valid_arguments_pass_and_are_normalized(self):
        self.assertEqual(validate_arguments(self.schema, {"text": "hi", "level": "40", "mode": "UP", "flag": "true"}),
                         {"text": "hi", "level": 40, "mode": "up", "flag": True})

    def test_json_string_arguments_are_parsed(self):
        self.assertEqual(validate_arguments(self.schema, '{"text": "hi"}'), {"text": "hi"})

    def test_unknown_keys_are_dropped_so_a_model_cannot_add_fields(self):
        self.assertEqual(validate_arguments(self.schema, {"text": "hi", "tier": 0, "confirmed": True}), {"text": "hi"})

    def test_empty_optional_string_counts_as_missing(self):
        schema = {"type": "object", "properties": {"app": {"type": "string"}}}
        self.assertEqual(validate_arguments(schema, {"app": ""}), {})

    def test_invalid_arguments_raise_schema_errors(self):
        for arguments in ({}, {"text": ""}, {"text": "toolong"}, {"text": "a", "level": 101},
                          {"text": "a", "level": 2.5}, {"text": "a", "mode": "sideways"},
                          {"text": "a", "flag": "maybe"}, {"text": ["a"]}, "not json", [1]):
            with self.subTest(arguments=arguments), self.assertRaises(SchemaError):
                validate_arguments(self.schema, arguments)


class RegistryTests(unittest.TestCase):
    def test_definitions_use_ollama_function_format(self):
        registry = Registry([tool("a"), tool("b", group="windows")])
        self.assertEqual(registry.names, ("a", "b"))
        self.assertEqual(registry.in_group("windows"), ("b",))
        definition = registry.definitions(["b", "missing"])[0]
        self.assertEqual(definition["type"], "function")
        self.assertEqual(definition["function"]["name"], "b")

    def test_duplicate_or_non_object_tools_are_rejected(self):
        registry = Registry([tool("a")])
        with self.assertRaises(ValueError):
            registry.register(tool("a"))
        with self.assertRaises(ValueError):
            registry.register(tool("c", parameters={"type": "string"}))

    def test_invalid_tier_from_tool_code_is_an_error(self):
        with self.assertRaises(ValueError):
            tool(tier=7).tier_for({}, None)


class PolicyTests(unittest.TestCase):
    def test_tiers_map_to_run_or_confirm(self):
        policy = Policy()
        self.assertEqual(policy.decide(tool(tier=READ), {}, None).action, RUN)
        self.assertEqual(policy.decide(tool(tier=SAFE), {}, None).action, RUN)
        decision = policy.decide(tool(tier=DISRUPTIVE, confirm=lambda a: "Shut down the computer? Say yes or no."), {}, None)
        self.assertEqual((decision.action, decision.question), (CONFIRM, "Shut down the computer? Say yes or no."))
        self.assertEqual(policy.decide(tool(tier=DESTRUCTIVE), {}, None).action, CONFIRM)

    def test_scope_rules_can_deny_or_clarify(self):
        policy = Policy()
        self.assertEqual(policy.decide(tool(check=lambda a, c: deny("blocked")), {}, None).action, DENY)
        decision = policy.decide(tool(check=lambda a, c: clarify("Which one?")), {}, None)
        self.assertEqual((decision.action, decision.question), (CLARIFY, "Which one?"))

    def test_off_by_default_tools_are_denied_until_enabled(self):
        risky = tool("send_email", tier=DESTRUCTIVE, off_by_default=True)
        self.assertEqual(Policy().decide(risky, {}, None).action, DENY)
        self.assertEqual(Policy(enabled_tools=frozenset({"send_email"})).decide(risky, {}, None).action, CONFIRM)

    def test_model_arguments_cannot_lower_a_tier(self):
        risky = tool(tier=DISRUPTIVE)
        self.assertEqual(Policy().decide(risky, {"tier": 0, "confirmed": True}, None).action, CONFIRM)

    def test_blocked_apps_by_name_executable_and_store_id(self):
        policy = Policy()
        for name in ("cmd", "Command Prompt", "Windows PowerShell", "PowerShell 7", "pwsh", "Terminal",
                     "Windows Terminal", "Registry Editor", "Node.js command prompt", "Uninstall Spotify"):
            self.assertTrue(policy.blocked(name=name), name)
        for path in (r"C:\Windows\System32\cmd.exe", r"C:\x\pwsh.exe", r"C:\x\wt.exe", r"C:\Windows\regedit.exe",
                     r"C:\Downloads\tool.msi", r"C:\Downloads\SetupFoo.exe", r"C:\x\install_app.exe"):
            self.assertTrue(policy.blocked(executable=path), path)
        self.assertTrue(policy.blocked(app_id="Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"))
        self.assertTrue(policy.blocked(app_id="{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\cmd.exe"))
        for name in ("Notepad", "Spotify", "Visual Studio Code", "Calculator", "Google Chrome"):
            self.assertFalse(policy.blocked(name=name), name)
        self.assertFalse(policy.blocked(executable=r"C:\x\Code.exe"))

    def test_user_blocklist_file_adds_patterns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "blocked-apps.txt"
            path.write_text("# my rules\nsteam\n*game*.exe\n\n", encoding="utf-8")
            self.assertEqual(read_user_blocklist(path), ("steam", "*game*.exe"))
            policy = Policy.from_user_config(path)
            self.assertTrue(policy.blocked(name="Steam"))
            self.assertTrue(policy.blocked(executable=r"C:\Games\bestgame.exe"))
            self.assertEqual(read_user_blocklist(Path(folder) / "missing.txt"), ())


class ConfirmationTests(unittest.TestCase):
    def test_natural_affirmatives_and_negatives(self):
        for text in ("yes", "Yeah.", "yep", "yup", "Sure!", "ok", "Okay", "go ahead", "do it", "confirm",
                     "please do", "yes please", "affirmative", "Yes, go ahead.", "Aqua, yes", "yeah do it"):
            self.assertEqual(classify_reply(text), "yes", text)
        for text in ("no", "Nope.", "don't", "cancel", "stop", "never mind", "No thanks", "no, don't do it"):
            self.assertEqual(classify_reply(text), "no", text)
        for text in ("", "open Chrome", "yes and also open Spotify", "what?", "thank you", "yes no",
                     "don't open chrome", 42):
            self.assertIsNone(classify_reply(text), text)

    def pending(self, created=0.0):
        return PendingAction("shutdown", {"delay": 30}, "Shut down? Say yes or no.", created, "shut down")

    def test_answer_returns_the_exact_pending_action_once(self):
        gate = ConfirmationGate()
        action = self.pending()
        gate.offer(action)
        verdict, returned = gate.answer("yes", 10.0)
        self.assertEqual(verdict, "yes")
        self.assertIs(returned, action)
        self.assertEqual(returned.args, {"delay": 30})
        self.assertIsNone(gate.pending)
        self.assertEqual(gate.answer("yes", 11.0), ("none", None))

    def test_expiry_and_unrelated_utterances_cancel(self):
        gate = ConfirmationGate()
        gate.offer(self.pending())
        self.assertEqual(gate.answer("yes", 61.0), ("none", None))
        gate.offer(self.pending())
        self.assertEqual(gate.answer("open notepad", 1.0), ("none", None))
        self.assertIsNone(gate.pending)
        gate.offer(self.pending())
        self.assertEqual(gate.answer("no", 1.0)[0], "no")


class SelectionTests(unittest.TestCase):
    registry = milestone_one_shaped_registry()

    def test_core_tools_are_always_shown(self):
        self.assertEqual(select_tools("hello there", self.registry)[:3], CORE_TOOLS)
        self.assertEqual(select_tools("turn the volume down", self.registry)[:3], CORE_TOOLS)

    def test_keyword_groups_add_their_tools(self):
        self.assertEqual(matched_groups("Open Notepad and write Hello I am Aqua"), ("apps", "input"))
        self.assertIn("volume", select_tools("Turn the volume down", self.registry))
        self.assertIn("media", select_tools("pause the music", self.registry))
        self.assertIn("lock_screen", select_tools("lock my screen", self.registry))
        self.assertIn("get_battery", select_tools("how much battery do I have", self.registry))
        self.assertIn("get_time", select_tools("what time is it", self.registry))
        self.assertIn("window_action", select_tools("minimise Notepad", self.registry))
        self.assertIn("window_action", select_tools("Write hello into the notepad window", self.registry))
        self.assertIn("undo_last", select_tools("undo that", self.registry))

    def test_visible_tools_are_capped_for_ollama(self):
        names = select_tools("open the window, mute the volume, lock it, what time, undo", self.registry)
        self.assertLessEqual(len(names), MAX_VISIBLE_TOOLS)
        self.assertEqual(names[:3], CORE_TOOLS)

    def test_negations_and_hypotheticals_hide_every_tool(self):
        for text in ("Don't open Chrome", "do not open chrome", "Never launch Spotify", "Aqua, don't mute",
                     "What would happen if I opened Chrome?", "what if I said open notepad",
                     "If I asked you to lock the screen, what would you do?", "Hypothetically, open Chrome"):
            self.assertTrue(wants_no_action(text), text)
            self.assertEqual(select_tools(text, self.registry), (), text)
        for text in ("Open Chrome", "Don't open Chrome, open Edge instead", "Can you open Chrome?",
                     "Would you open Chrome please"):
            self.assertFalse(wants_no_action(text), text)

    def test_polite_requests_keep_their_tools(self):
        for text in ("would you be able to open Chrome?", "Would you ever open Chrome for me?",
                     "never mind that, open Calculator", "Never mind. Open Calculator",
                     "don't forget to open Notepad", "Aqua, do not forget to open Notepad"):
            self.assertFalse(wants_no_action(text), text)
            self.assertIn("launch_app", select_tools(text, self.registry), text)

    def test_real_negations_and_hypotheticals_still_hide_tools(self):
        for text in ("Never open Chrome", "don't open Notepad", "Do not launch Calculator",
                     "no need to open Spotify", "What would happen if I opened Chrome?",
                     "Imagine you opened Notepad"):
            self.assertTrue(wants_no_action(text), text)
            self.assertEqual(select_tools(text, self.registry), (), text)


class UndoStackTests(unittest.TestCase):
    def test_last_action_is_undone_first_and_stack_is_bounded(self):
        stack, calls = UndoStack(limit=2), []
        for name in ("a", "b", "c"):
            stack.push(UndoRecord(name, lambda name=name: calls.append(name) or f"undid {name}"))
        self.assertEqual(len(stack), 2)
        self.assertEqual(stack.undo_last().done, "undid c")
        self.assertEqual(stack.undo_last().done, "undid b")
        self.assertEqual(stack.undo_last().status, "empty")
        self.assertEqual(calls, ["c", "b"])

    def test_failing_undo_reports_failure(self):
        stack = UndoStack()
        stack.push(UndoRecord("volume", lambda: (_ for _ in ()).throw(OSError("gone"))))
        self.assertEqual(stack.undo_last().status, "failed")


class PromptTests(unittest.TestCase):
    def test_system_prompt_is_short_static_and_has_no_clock(self):
        self.assertLess(len(SYSTEM_PROMPT), 2400)
        self.assertNotIn("2026", SYSTEM_PROMPT)
        self.assertIn("ONE short sentence", SYSTEM_PROMPT)
        self.assertIn("no emoji", SYSTEM_PROMPT)

    def test_per_request_context_goes_last_in_the_user_message(self):
        now = datetime(2026, 11, 22, 15, 42)
        self.assertIn("3:42 PM, Sunday 22 November 2026", request_context(now))
        message = user_message("what time is it", now)
        self.assertTrue(message.startswith("what time is it\n\n"))
        self.assertTrue(message.endswith(request_context(now)))
        self.assertIn("12:05 AM", request_context(datetime(2026, 1, 1, 0, 5)))


if __name__ == "__main__":
    unittest.main()
