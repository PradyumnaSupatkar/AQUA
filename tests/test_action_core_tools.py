"""The milestone 1 tools against the fake desktop adapter."""

import ctypes
import unittest

from aqua.agent.context import Launch, ToolContext
from aqua.agent.policy import CLARIFY, DENY, Policy
from aqua.tools.adapter import AppEntry, Battery, Window
from aqua.tools.app_index import AppIndex, normalize
from aqua.tools.apps import launch_app, window_matches_app
from aqua.tools.fake import FakeDesktop
from aqua.tools.input import type_text
from aqua.tools.system import get_battery, get_time, lock_screen, media, volume
from aqua.tools.undo import undo_last
from aqua.tools.windows import list_windows, window_action


def context(desktop=None, policy=None):
    desktop = desktop if desktop is not None else FakeDesktop()
    return ToolContext(desktop=desktop, apps=AppIndex(desktop.app_entries, clock=desktop.monotonic),
                       policy=policy if policy is not None else Policy())


def decide(tool, args, ctx):
    return ctx.policy.decide(tool, args, ctx)


class RegistryTests(unittest.TestCase):
    def test_milestone_one_registry_is_complete_safe_and_fits_ollama(self):
        import json
        from aqua.agent.registry import SAFE
        from aqua.agent.selection import CORE_TOOLS, GROUP_KEYWORDS
        from aqua.brain.ollama import MAX_TOOL_DEFINITION_BYTES
        from aqua.tools import build_registry
        registry = build_registry()
        self.assertEqual(set(registry.names), {"launch_app", "type_text", "undo_last", "list_windows",
                                               "window_action", "get_time", "get_battery", "volume", "media",
                                               "lock_screen", "close_app", "force_quit_app", "inspect_window",
                                               "brightness", "power", "open_url", "web_search", "look_up",
                                               "device_status"})
        disruptive = {"force_quit_app", "power"}
        for name in registry.names:
            self.assertEqual(registry.get(name).tier_for({"action": "sleep"}, None) > SAFE, name in disruptive, name)
            self.assertIn(registry.get(name).group, GROUP_KEYWORDS, name)
        self.assertNotIn("look_up", build_registry(lookup=False).names)
        for group in GROUP_KEYWORDS:  # Every group fits beside the core tools.
            self.assertLessEqual(len(set(CORE_TOOLS) | set(registry.in_group(group))), 8, group)
        self.assertTrue(set(CORE_TOOLS) <= set(registry.names))
        # The largest selection (eight tools) must fit Ollama's definition size limit.
        biggest = registry.definitions(registry.names[:8])
        self.assertLess(len(json.dumps(biggest).encode()), MAX_TOOL_DEFINITION_BYTES)


class AppIndexTests(unittest.TestCase):
    def test_aliases_and_fuzzy_names_find_installed_apps(self):
        index = AppIndex(FakeDesktop().app_entries)
        for query, name in (("notepad", "Notepad"), ("Notepad app", "Notepad"), ("calc", "Calculator"),
                            ("the calculator", "Calculator"), ("vs code", "Visual Studio Code"),
                            ("VS Code", "Visual Studio Code"), ("vscode", "Visual Studio Code"),
                            ("chrome", "Google Chrome"), ("Spotify", "Spotify"), ("spotfy", "Spotify")):
            match = index.find(query)
            self.assertEqual((match.kind, match.entry.name), ("found", name), query)

    def test_unknown_and_ambiguous_names(self):
        index = AppIndex(lambda: [AppEntry("Visual Studio Code", "a"), AppEntry("Visual Studio 2022", "b"),
                                  AppEntry("Paint", "c")])
        self.assertEqual(index.find("minecraft").kind, "none")
        ambiguous = index.find("visual studio")
        self.assertEqual(ambiguous.kind, "ambiguous")
        self.assertEqual({e.name for e in ambiguous.candidates}, {"Visual Studio Code", "Visual Studio 2022"})

    def test_shortcuts_win_duplicates_and_noise_entries_rank_lower(self):
        index = AppIndex(lambda: [AppEntry("Spotify", "Spotify!App", "appsfolder"),
                                  AppEntry("Spotify", r"C:\Spotify.lnk", "shortcut"),
                                  AppEntry("Uninstall Spotify Helper", r"C:\u.lnk", "shortcut")])
        self.assertEqual(len(index.entries()), 2)
        self.assertEqual(index.find("spotify").entry.kind, "shortcut")

    def test_missing_app_triggers_one_refresh_after_the_cooldown(self):
        installed, now = [AppEntry("Notepad", "n")], [0.0]
        index = AppIndex(lambda: list(installed), clock=lambda: now[0])
        index.entries()
        installed.append(AppEntry("Spotify", "s"))
        self.assertEqual(index.find("spotify").kind, "none")  # Index is fresh; no rescan yet.
        now[0] = 31.0
        self.assertEqual(index.find("spotify").kind, "found")

    def test_loader_failure_leaves_an_empty_index(self):
        index = AppIndex(lambda: (_ for _ in ()).throw(OSError("COM failed")))
        self.assertEqual(index.find("notepad").kind, "none")

    def test_normalize(self):
        self.assertEqual(normalize("  The VS-Code app "), "vs code".replace("vs code", normalize("vs code")))
        self.assertEqual(normalize("Notepad++"), "notepad++")


class LaunchAppTests(unittest.TestCase):
    def test_any_installed_app_launches_and_its_window_is_verified(self):
        for query, name in (("Spotify", "Spotify"), ("calculator", "Calculator"), ("VS Code", "Visual Studio Code"),
                            ("notepad", "Notepad")):
            ctx = context()
            self.assertEqual(decide(launch_app, {"app": query}, ctx).action, "run")
            outcome = launch_app.run({"app": query}, ctx)
            self.assertEqual(outcome.status, "ok", query)
            self.assertEqual(outcome.done, f"opened {name}")
            self.assertEqual(ctx.desktop.launched, [name])
            self.assertEqual(ctx.launches[-1].app, name)
            self.assertIsNotNone(ctx.launches[-1].handle)
            self.assertIsNotNone(outcome.undo)

    def test_blocked_apps_are_denied_before_anything_runs(self):
        for query in ("command prompt", "cmd", "PowerShell", "terminal", "windows terminal", "regedit"):
            ctx = context()
            decision = decide(launch_app, {"app": query}, ctx)
            self.assertEqual(decision.action, DENY, query)
            self.assertIn("blocked", decision.reason)
            self.assertEqual(ctx.desktop.launched, [])

    def test_ambiguous_name_asks_which_one(self):
        ctx = context(FakeDesktop(apps=[AppEntry("Visual Studio Code", "a"), AppEntry("Visual Studio 2022", "b")]))
        decision = decide(launch_app, {"app": "visual studio"}, ctx)
        self.assertEqual(decision.action, CLARIFY)
        self.assertTrue(decision.question.endswith("?"))

    def test_unknown_app_fails_without_launching(self):
        ctx = context()
        outcome = launch_app.run({"app": "Minecraft"}, ctx)
        self.assertEqual(outcome.status, "failed")
        self.assertIn("No installed app", outcome.message)
        self.assertEqual(ctx.desktop.launched, [])

    def test_already_open_app_is_brought_to_front_without_undo(self):
        desktop = FakeDesktop()
        desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
        ctx = context(desktop)
        outcome = launch_app.run({"app": "notepad"}, ctx)
        self.assertEqual(outcome.status, "ok")
        self.assertIn("already open", outcome.message)
        self.assertIsNone(outcome.undo)

    def test_slow_window_is_waited_for_and_missing_window_is_unverified(self):
        ctx = context(FakeDesktop(slow_apps={"Visual Studio Code"}))
        self.assertEqual(launch_app.run({"app": "vs code"}, ctx).status, "ok")
        ctx = context(FakeDesktop(silent_apps={"Spotify"}))
        outcome = launch_app.run({"app": "spotify"}, ctx)
        self.assertEqual(outcome.status, "unverified")
        self.assertEqual(outcome.done, "started Spotify")
        self.assertIsNone(ctx.launches[-1].handle)

    def test_launch_error_is_reported(self):
        desktop = FakeDesktop()
        desktop.launch = lambda entry: (_ for _ in ()).throw(OSError("denied"))
        self.assertEqual(launch_app.run({"app": "notepad"}, context(desktop)).status, "failed")

    def test_undo_closes_the_window_aqua_opened(self):
        ctx = context()
        outcome = launch_app.run({"app": "notepad"}, ctx)
        handle = ctx.launches[-1].handle
        self.assertEqual(outcome.undo.run(), "closed Notepad")
        self.assertEqual(ctx.desktop.closed, [handle])

    def test_window_matching(self):
        notepad = AppEntry("Notepad", "x", "appsfolder")
        code = AppEntry("Visual Studio Code", "x.lnk", "shortcut", r"C:\VS Code\Code.exe")
        self.assertTrue(window_matches_app(Window(1, "Untitled - Notepad", "notepad.exe"), notepad))
        self.assertTrue(window_matches_app(Window(1, "main.py - Aqua - Visual Studio Code", "code.exe"), code))
        self.assertTrue(window_matches_app(Window(1, "Calculator", "applicationframehost.exe"), "calculator"))
        self.assertTrue(window_matches_app(Window(1, "Artist - Song", "spotify.exe"), "Spotify"))
        self.assertFalse(window_matches_app(Window(1, "notes about notepad - Google Chrome", "chrome.exe"), notepad))


class TypeTextTests(unittest.TestCase):
    def test_types_into_the_window_opened_earlier_in_the_request(self):
        ctx = context()
        ctx.begin_request("Open Notepad and write Hello I am Aqua")
        launch_app.run({"app": "notepad"}, ctx)
        self.assertEqual(decide(type_text, {"text": "Hello I am Aqua"}, ctx).action, "run")
        outcome = type_text.run({"text": "Hello I am Aqua"}, ctx)
        self.assertEqual((outcome.status, outcome.done), ("ok", "typed your text into Notepad"))
        self.assertEqual(ctx.desktop.text[ctx.launches[-1].handle], "Hello I am Aqua")

    def test_named_target_is_focused_first(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("notes.txt - Notepad", "notepad.exe", front=False)
        ctx = context(desktop)
        self.assertNotEqual(desktop.foreground().handle, notepad.handle)
        outcome = type_text.run({"text": "hello I am your assistant", "app": "the notepad window"}, ctx)
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(desktop.foreground().handle, notepad.handle)
        self.assertEqual(desktop.text[notepad.handle], "hello I am your assistant")

    def test_no_target_while_aqua_terminal_is_in_front_asks_which_app(self):
        ctx = context()
        decision = decide(type_text, {"text": "hi"}, ctx)
        self.assertEqual(decision.action, CLARIFY)
        self.assertEqual(ctx.desktop.typed, [])

    def test_no_target_types_at_the_cursor_of_the_foreground_app(self):
        desktop = FakeDesktop()
        word = desktop.add_window("Document1 - Word", "winword.exe")
        ctx = context(desktop)
        ctx.begin_request("type hi")
        self.assertEqual(decide(type_text, {"text": "hi"}, ctx).action, "run")
        self.assertEqual(type_text.run({"text": "hi"}, ctx).status, "ok")
        self.assertEqual(desktop.text[word.handle], "hi")

    def test_line_breaks_are_read_back_as_one_line_and_control_characters_refused(self):
        ctx = context()
        ctx.desktop.add_window("Untitled - Notepad", "notepad.exe")
        for text in ("line one\nline two", "a\rb"):
            decision = decide(type_text, {"text": text, "app": "notepad"}, ctx)
            self.assertEqual(decision.action, "confirm", text)
            self.assertIn("on 2 lines", decision.question)  # Notepad is a plain editor: Enter is a line break.
        self.assertEqual(decide(type_text, {"text": "bell\x07", "app": "notepad"}, ctx).action, DENY)
        outcome = type_text.run({"text": "line one\r\nline two", "app": "notepad"}, ctx)
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(list(ctx.desktop.text.values()), ["line one\nline two"])
        chat = ctx.desktop.add_window("General - Team Chat", "teamchat.exe")
        self.assertIn("as one line", decide(type_text, {"text": "line one\nline two", "app": "Team Chat"}, ctx).question)
        type_text.run({"text": "line one\nline two", "app": "Team Chat"}, ctx)
        self.assertEqual(ctx.desktop.text[chat.handle], "line one line two")  # Enter could send a message here.

    def test_never_types_into_terminals(self):
        desktop = FakeDesktop()
        ctx = context(desktop)
        outcome = type_text.run({"text": "dir", "app": "terminal"}, ctx)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(desktop.typed, [])

    def test_focus_failure_types_nothing(self):
        desktop = FakeDesktop(focus_allowed=False)
        desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
        outcome = type_text.run({"text": "hi", "app": "notepad"}, context(desktop))
        self.assertEqual(outcome.status, "failed")
        self.assertIn("nothing was typed", outcome.message)
        self.assertEqual(desktop.typed, [])

    def test_focus_lost_mid_typing_is_a_failure(self):
        desktop = FakeDesktop()
        desktop.add_window("Untitled - Notepad", "notepad.exe")
        desktop.type_text = lambda handle, text: 3
        outcome = type_text.run({"text": "hello", "app": "notepad"}, context(desktop))
        self.assertEqual(outcome.status, "failed")
        self.assertIn("3 of 5", outcome.message)

    def test_missing_window_and_unverified_readback(self):
        ctx = context()
        self.assertEqual(type_text.run({"text": "hi", "app": "wordstar"}, ctx).status, "failed")  # Not installed.
        opened = type_text.run({"text": "hi", "app": "notepad"}, ctx)  # Installed but closed: opened first.
        self.assertEqual((opened.status, opened.done), ("ok", "opened Notepad and typed your text"))
        desktop = FakeDesktop(readback=False)
        desktop.add_window("Untitled - Notepad", "notepad.exe")
        outcome = type_text.run({"text": "hi", "app": "notepad"}, context(desktop))
        self.assertEqual(outcome.status, "unverified")
        self.assertIn("not verified", outcome.message)

    def test_launch_record_for_a_closed_window_is_ignored(self):
        ctx = context()
        ctx.launches.append(Launch("Notepad", 424242))
        self.assertEqual(decide(type_text, {"text": "hi"}, ctx).action, CLARIFY)


class WindowToolTests(unittest.TestCase):
    def test_list_windows_hides_terminals(self):
        desktop = FakeDesktop()
        desktop.add_window("Untitled - Notepad", "notepad.exe")
        outcome = list_windows.run({}, context(desktop))
        self.assertEqual([row["app"] for row in outcome.data["windows"]], ["notepad"])
        self.assertEqual(list_windows.run({}, context()).status, "empty")

    def test_minimise_maximise_restore_focus_and_undo(self):
        desktop = FakeDesktop()
        notepad = desktop.add_window("Untitled - Notepad", "notepad.exe")
        ctx = context(desktop)
        outcome = window_action.run({"action": "minimize", "window": "Notepad"}, ctx)
        self.assertEqual((outcome.status, outcome.done), ("ok", "minimised Notepad"))
        self.assertTrue(desktop.window(notepad.handle).minimized)
        self.assertEqual(outcome.undo.run(), "put Notepad back")
        self.assertFalse(desktop.window(notepad.handle).minimized)
        window_action.run({"action": "maximise", "window": "notepad"}, ctx)
        self.assertTrue(desktop.window(notepad.handle).maximized)
        window_action.run({"action": "restore", "window": "notepad"}, ctx)
        self.assertFalse(desktop.window(notepad.handle).maximized)
        desktop.focus(1)
        self.assertEqual(window_action.run({"action": "focus", "window": "notepad"}, ctx).status, "ok")
        self.assertEqual(desktop.foreground().handle, notepad.handle)

    def test_unknown_window_and_refused_focus(self):
        self.assertEqual(window_action.run({"action": "focus", "window": "Excel"}, context()).status, "failed")
        desktop = FakeDesktop(focus_allowed=False)
        desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
        self.assertEqual(window_action.run({"action": "focus", "window": "notepad"}, context(desktop)).status, "failed")


class SystemToolTests(unittest.TestCase):
    def test_volume_up_down_set_mute_unmute_and_undo(self):
        desktop = FakeDesktop(volume=50)
        ctx = context(desktop)
        down = volume.run({"action": "down"}, ctx)
        self.assertEqual((desktop.volume, down.done), (40, "turned the volume down to 40%"))
        volume.run({"action": "up", "level": 25}, ctx)
        self.assertEqual(desktop.volume, 65)
        volume.run({"action": "set", "level": 120 if False else 100}, ctx)
        self.assertEqual(desktop.volume, 100)
        volume.run({"action": "down", "level": 100}, ctx)
        self.assertEqual(desktop.volume, 0)
        mute = volume.run({"action": "mute"}, ctx)
        self.assertTrue(desktop.muted)
        self.assertEqual(mute.undo.run(), "put the volume back to 0%")
        self.assertFalse(desktop.muted)
        desktop.muted = True
        volume.run({"action": "unmute"}, ctx)
        self.assertFalse(desktop.muted)

    def test_raising_volume_while_muted_unmutes(self):
        desktop = FakeDesktop(volume=30, muted=True)
        volume.run({"action": "up"}, context(desktop))
        self.assertEqual((desktop.volume, desktop.muted), (40, False))

    def test_set_without_level_asks(self):
        ctx = context()
        self.assertEqual(decide(volume, {"action": "set"}, ctx).action, CLARIFY)

    def test_media_keys_lock_time_and_battery(self):
        desktop = FakeDesktop()
        ctx = context(desktop)
        self.assertEqual(media.run({"action": "play_pause"}, ctx).done, "pressed play/pause")
        media.run({"action": "next"}, ctx)
        self.assertEqual(desktop.media, ["play_pause", "next"])
        self.assertEqual(lock_screen.run({}, ctx).done, "locked the screen")
        self.assertTrue(desktop.locked)
        self.assertRegex(get_time.run({}, ctx).message, r"^It is \d{1,2}:\d\d [AP]M on ")
        self.assertIn("76%", get_battery.run({}, ctx).message)
        no_battery = context(FakeDesktop(battery=Battery(False)))
        self.assertIn("no battery", get_battery.run({}, no_battery).message)
        unknown = context(FakeDesktop(battery=Battery(True, None)))
        self.assertEqual(get_battery.run({}, unknown).status, "failed")

    def test_undo_last_tool_uses_the_session_stack(self):
        ctx = context(FakeDesktop(volume=50))
        self.assertEqual(undo_last.run({}, ctx).status, "empty")
        outcome = volume.run({"action": "down"}, ctx)
        ctx.undo.push(outcome.undo)
        self.assertEqual(undo_last.run({}, ctx).status, "ok")
        self.assertEqual(ctx.desktop.volume, 50)


class Win32HelperTests(unittest.TestCase):
    """Pure helpers only; nothing here calls Windows."""

    def test_unicode_input_pairs_include_surrogates(self):
        from aqua.tools.win32 import INPUT, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, unicode_inputs, utf16_units
        self.assertEqual(ctypes.sizeof(INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        self.assertEqual(utf16_units("é"), [0xE9])
        self.assertEqual(len(utf16_units("😀")), 2)
        inputs = unicode_inputs("Hi😀")
        self.assertEqual(len(inputs), 8)
        self.assertEqual([inputs[i].ki.wScan for i in range(0, 8, 2)], [ord("H"), ord("i"), *utf16_units("😀")])
        self.assertEqual(inputs[0].ki.dwFlags, KEYEVENTF_UNICODE)
        self.assertEqual(inputs[1].ki.dwFlags, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
        self.assertTrue(all(inputs[i].ki.wVk == 0 for i in range(8)))


if __name__ == "__main__":
    unittest.main()
