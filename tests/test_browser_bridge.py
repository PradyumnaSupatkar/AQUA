"""Browser bridge: framing, the native host's checks, the pipe server's tab choice, setup."""

import io
import json
import struct
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from aqua.browser import protocol
from aqua.browser.bridge import BridgeError, BridgeUnavailable, BrowserBridge, BrowserConnection
from aqua.browser.host import Relay, main as host_main
from aqua.browser.setup import install, installed_browsers, launcher_text, load_steps, run, uninstall

ROOT = Path(__file__).resolve().parents[1]


class MemoryPipe:
    """One end of an in-memory duplex byte pipe."""

    def __init__(self) -> None:
        self.peer = None
        self._buffer = bytearray()
        self._cond = threading.Condition()
        self.closed = False

    @classmethod
    def pair(cls):
        a, b = cls(), cls()
        a.peer, b.peer = b, a
        return a, b

    def write(self, data: bytes) -> None:
        if self.closed or self.peer.closed:
            raise OSError("closed")
        with self.peer._cond:
            self.peer._buffer += data
            self.peer._cond.notify_all()

    def read(self, size: int) -> bytes:
        with self._cond:
            while not self._buffer and not self.closed and not self.peer.closed:
                self._cond.wait(0.05)
            data = bytes(self._buffer[:size])
            del self._buffer[:size]
            return data

    def close(self) -> None:
        self.closed = True
        for end in (self, self.peer):
            with end._cond:
                end._cond.notify_all()


def fake_extension(pipe: MemoryPipe, browser: str, tab, results=None):
    """Plays a native host + extension on the far end of ``pipe``."""
    pipe.write(protocol.encode({"hello": "aqua-browser-host", "version": 1, "browser": browser,
                                "origin": protocol.ORIGIN}))
    seen = []

    def loop():
        while True:
            try:
                message = protocol.read_message(pipe.read)
            except protocol.ProtocolError:
                return
            if message is None:
                return
            seen.append(message)
            if message["cmd"] == "status":
                body = {"tab": tab}
            else:
                body = (results or {}).get(message["action"], {"done": message["action"]})
            pipe.write(protocol.encode({"id": message["id"], "ok": True, "result": body}))

    threading.Thread(target=loop, daemon=True).start()
    return seen


class ProtocolTests(unittest.TestCase):
    def test_round_trip_and_limits(self):
        data = protocol.encode({"id": 1, "cmd": "status", "text": "Grüße"})
        self.assertEqual(protocol.read_message(io.BytesIO(data).read), {"id": 1, "cmd": "status", "text": "Grüße"})
        self.assertIsNone(protocol.read_message(io.BytesIO(b"").read))
        with self.assertRaises(protocol.ProtocolError):
            protocol.read_message(io.BytesIO(struct.pack("<I", protocol.MAX_MESSAGE + 1)).read)
        with self.assertRaises(protocol.ProtocolError):
            protocol.read_message(io.BytesIO(struct.pack("<I", 2) + b"[]").read)  # Not an object.
        with self.assertRaises(protocol.ProtocolError):
            protocol.read_message(io.BytesIO(struct.pack("<I", 10) + b"{}").read)  # Truncated.

    def test_extension_id_matches_the_manifest_key(self):
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(protocol.extension_id_from_key(manifest["key"]), protocol.EXTENSION_ID)

    def test_extension_permissions_are_minimal(self):
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(sorted(manifest["permissions"]), ["alarms", "nativeMessaging", "scripting"])
        self.assertEqual(manifest["host_permissions"], ["https://www.youtube.com/*"])
        self.assertNotIn("content_scripts", manifest)
        self.assertNotIn("externally_connectable", manifest)
        source = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
        for forbidden in ("onMessageExternal", "fetch(", "XMLHttpRequest", "WebSocket", "eval("):
            self.assertNotIn(forbidden, source)
        for action in ("like", "subscribe", "comment"):
            self.assertNotIn(f'"{action}"', source)

    def test_pipe_name_is_per_user(self):
        a, b = protocol.pipe_name("S-1-5-21-1-2-3-1001"), protocol.pipe_name("S-1-5-21-1-2-3-1002")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith(r"\\.\pipe\aqua-browser-bridge-"))


class HostTests(unittest.TestCase):
    def test_other_callers_are_refused(self):
        for argv in (["--browser", "chrome", "chrome-extension://abcdefghijklmnopabcdefghijklmnop/"],
                     ["--browser", "chrome"], ["--browser", "edge", protocol.ORIGIN]):
            logged = []
            self.assertEqual(host_main(argv, stdin=io.BytesIO(), stdout=io.BytesIO(),
                                       connect_pipe=lambda: self.fail("connected"),
                                       write_log=lambda browser, event: logged.append(event)), 2, argv)
            self.assertTrue(logged[0].startswith("refused"), argv)

    def test_relays_commands_in_and_replies_out_only(self):
        aqua_end, host_end = MemoryPipe.pair()
        reply = protocol.encode({"id": 1, "ok": True, "result": {"tab": None}})
        junk = protocol.encode({"cmd": "youtube"})  # No id: not a reply.
        stdout = _Out()
        relay = Relay("chrome", protocol.ORIGIN, stdin=_AfterForward(junk + reply, stdout), stdout=stdout,
                      connect_pipe=lambda: host_end, sleep=lambda _s: None)
        aqua_end.write(protocol.encode({"id": 1, "cmd": "status"}))
        aqua_end.write(protocol.encode({"id": 2, "cmd": "run_program", "path": "calc"}))  # Not a command.
        thread = threading.Thread(target=relay.run, daemon=True)
        thread.start()
        hello = protocol.read_message(aqua_end.read)
        self.assertEqual((hello["browser"], hello["origin"]), ("chrome", protocol.ORIGIN))
        self.assertEqual(protocol.read_message(aqua_end.read), {"id": 1, "ok": True, "result": {"tab": None}})
        thread.join(2)
        out = io.BytesIO(stdout.getvalue())
        self.assertEqual(protocol.read_message(out.read), {"id": 1, "cmd": "status"})
        self.assertIsNone(protocol.read_message(out.read))  # run_program was dropped.


class _Out(io.BytesIO):
    """stdout that signals once the host has forwarded something to the extension."""

    def __init__(self) -> None:
        super().__init__()
        self.written = threading.Event()

    def flush(self):
        super().flush()
        self.written.set()


class _AfterForward:
    """stdin from the browser: its messages arrive only after the host forwarded Aqua's command
    (so the pipe is up), and its end comes after a short grace for the dropped message."""

    def __init__(self, data: bytes, stdout: _Out) -> None:
        self.stream, self.stdout = io.BytesIO(data), stdout

    def read(self, size):
        self.stdout.written.wait(2)
        chunk = self.stream.read(size)
        if not chunk:
            threading.Event().wait(0.3)  # Let the pipe thread finish reading run_program (dropped).
        return chunk


class BridgeTests(unittest.TestCase):
    def _bridge(self, tabs, results=None):
        bridge, seen = BrowserBridge(), {}
        for browser, tab in tabs.items():
            aqua_end, host_end = MemoryPipe.pair()
            seen[browser] = fake_extension(host_end, browser, tab, (results or {}).get(browser))
            connection = BrowserConnection(aqua_end)
            self.assertTrue(connection.handshake())
            bridge.add_connection(connection)
        return bridge, seen

    def test_most_recently_active_youtube_tab_wins_across_browsers(self):
        bridge, seen = self._bridge({"brave": {"id": 3, "lastAccessed": 2000}, "chrome": {"id": 9, "lastAccessed": 1000}})
        result = bridge.youtube("pause", {})
        self.assertEqual(result, {"done": "pause", "browser": "brave"})
        self.assertEqual([m["cmd"] for m in seen["brave"]], ["status", "youtube"])
        self.assertEqual([m["cmd"] for m in seen["chrome"]], ["status"])

    def test_no_tab_anywhere(self):
        bridge, _ = self._bridge({"chrome": None})
        self.assertEqual(bridge.youtube("pause", {}), {"browser": None, "error": "no_tab"})
        self.assertEqual(bridge.youtube("search", {"query": "x"})["browser"], "chrome")  # Opens a tab.

    def test_not_connected_and_bad_handshakes(self):
        with self.assertRaises(BridgeUnavailable):
            BrowserBridge().youtube("pause", {})
        for hello in ({"hello": "aqua-browser-host", "browser": "chrome", "origin": "chrome-extension://evil/"},
                      {"hello": "aqua-browser-host", "browser": "firefox", "origin": protocol.ORIGIN}, {"x": 1}):
            aqua_end, host_end = MemoryPipe.pair()
            host_end.write(protocol.encode(hello))
            self.assertFalse(BrowserConnection(aqua_end).handshake(timeout=1), hello)

    def test_extension_errors_are_raised(self):
        aqua_end, host_end = MemoryPipe.pair()
        host_end.write(protocol.encode({"hello": "aqua-browser-host", "browser": "chrome", "origin": protocol.ORIGIN}))
        connection = BrowserConnection(aqua_end)
        connection.handshake()

        def answer():
            while (message := protocol.read_message(host_end.read)) is not None:
                if message["cmd"] == "status":
                    host_end.write(protocol.encode({"id": message["id"], "ok": True,
                                                    "result": {"tab": {"id": 1, "lastAccessed": 5}}}))
                else:
                    host_end.write(protocol.encode({"id": message["id"], "ok": False, "error": "unknown action"}))

        threading.Thread(target=answer, daemon=True).start()
        bridge = BrowserBridge()
        bridge.add_connection(connection)
        with self.assertRaises(BridgeError):
            bridge.youtube("pause", {})


class FakeRegistry:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set_default(self, path, value):
        self.values[path] = value

    def get_default(self, path):
        return self.values.get(path)

    def delete(self, path):
        return self.values.pop(path, None) is not None


SHARED = r"Software\Google\Chrome\NativeMessagingHosts\com.aqua.browser_bridge"
BRAVE_OWN = r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\com.aqua.browser_bridge"
PYTHON = r"D:\Aqua\.venv\Scripts\python.exe"


class SetupTests(unittest.TestCase):
    def test_brave_uses_the_chrome_key_windows_chromium_reads(self):
        # Live Brave said "Specified native messaging host not found" with only its own key set.
        registry = FakeRegistry()
        with tempfile.TemporaryDirectory() as temp:
            manifest = install("brave", registry=registry, folder=Path(temp), python=PYTHON)
            self.assertEqual(registry.values, {SHARED: str(manifest), BRAVE_OWN: str(manifest)})

    def test_both_browsers_side_by_side_and_uninstall_one(self):
        registry = FakeRegistry()
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "browser-bridge"
            folder.mkdir()
            (folder / "aqua-browser-host-brave.cmd").write_text("old")  # First-version files are cleaned up.
            install("brave", registry=registry, folder=folder, python=PYTHON)
            manifest = install("chrome", registry=registry, folder=folder, python=PYTHON)
            self.assertFalse((folder / "aqua-browser-host-brave.cmd").exists())
            self.assertEqual(installed_browsers(folder), ["brave", "chrome"])
            data = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(data["allowed_origins"], [protocol.ORIGIN])
            self.assertEqual((data["name"], data["type"]), ("com.aqua.browser_bridge", "stdio"))
            self.assertTrue(Path(data["path"]).read_bytes().endswith(b"-I -m aqua.browser.host %*\r\n"))
            self.assertTrue(uninstall("brave", registry=registry, folder=folder))
            self.assertEqual(registry.values, {SHARED: str(manifest)})  # Chrome still uses the shared key.
            self.assertTrue(manifest.exists())
            self.assertTrue(uninstall("chrome", registry=registry, folder=folder))
            self.assertEqual(registry.values, {})
            self.assertFalse(folder.exists())
            self.assertFalse(uninstall("chrome", registry=registry, folder=folder))

    def test_run_prints_the_load_steps(self):
        lines = []
        with tempfile.TemporaryDirectory() as temp:
            run("brave", registry=FakeRegistry(), folder=Path(temp), out=lines.append)
        text = "\n".join(lines)
        for part in ("brave://extensions", "Developer mode", "Load unpacked", protocol.EXTENSION_ID):
            self.assertIn(part, text)
        self.assertIn("chrome://extensions", load_steps("chrome"))

    def test_launcher_quotes_the_interpreter(self):
        self.assertEqual(launcher_text(r"C:\Program Files\Py\python.exe"),
                         '@echo off\r\n"C:\\Program Files\\Py\\python.exe" -I -m aqua.browser.host %*\r\n')

    def test_cli_command(self):
        from aqua.cli import _parser
        args = _parser().parse_args(["setup-browser", "--browser", "chrome", "--uninstall"])
        self.assertEqual((args.command, args.browser, args.uninstall, args.check), ("setup-browser", "chrome", True, False))
        self.assertTrue(_parser().parse_args(["setup-browser", "--check", "--browser", "brave"]).check)

    def test_check_reports_each_part(self):
        from aqua.browser.setup import check
        registry = FakeRegistry()
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            items = check("brave", registry=registry, folder=folder, launcher_check=lambda _p: (True, "ok"),
                          pipe_check=lambda: (False, "not reachable"))
            self.assertEqual([ok for _item, ok, _detail in items], [False, False, False, False, False])
            install("brave", registry=registry, folder=folder, python=PYTHON)
            (folder / "host-brave.log").write_text("2026-11-24T16:00:00+0530 started by the extension (pid 1)\n"
                                                   "2026-11-24T16:00:01+0530 connected to Aqua\n")
            items = dict((item, (ok, detail)) for item, ok, detail in check(
                "brave", registry=registry, folder=folder, launcher_check=lambda _p: (True, "aqua browser host: ok"),
                pipe_check=lambda: (True, "Aqua is running")))
            self.assertTrue(all(ok for ok, _detail in items.values()), items)
            self.assertIn("last reached Aqua 2026-11-24T16:00:01+0530", items["extension last seen"][1])

    def test_host_check_mode_and_browser_detection(self):
        from aqua.browser.host import detect_browser
        out = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(out):
            self.assertEqual(host_main(["--check"], write_log=lambda *a: self.fail("logged")), 0)
        self.assertIn("aqua browser host: ok", out.getvalue())
        # python (30) <- venv launcher (20) <- cmd.exe (10) <- brave.exe (5)
        table = {30: (20, "python.exe"), 20: (10, "python.exe"), 10: (5, "cmd.exe"), 5: (1, "brave.exe"),
                 1: (0, "explorer.exe")}
        self.assertEqual(detect_browser(30, table), "brave")
        table[5] = (1, "chrome.exe")
        self.assertEqual(detect_browser(30, table), "chrome")
        table[5] = (1, "explorer.exe")
        self.assertIsNone(detect_browser(30, table))


@unittest.skipUnless(sys.platform == "win32", "Windows named pipes")
class RealPipeTests(unittest.TestCase):
    """A real local pipe in this process (no browser, no registry)."""

    def test_round_trip_and_first_instance_rule(self):
        from aqua.browser import winpipe
        name = r"\\.\pipe\aqua-test-" + uuid.uuid4().hex
        server = winpipe.PipeServer(name)
        accepted = {}
        thread = threading.Thread(target=lambda: accepted.setdefault("c", server.accept()), daemon=True)
        thread.start()
        client = None
        for _ in range(50):
            try:
                client = winpipe.connect(name)
                break
            except OSError:
                import time
                time.sleep(0.05)
        self.assertIsNotNone(client)
        thread.join(2)
        conn = accepted["c"]
        client.write(protocol.encode({"id": 1, "ok": True}))
        self.assertEqual(protocol.read_message(conn.read), {"id": 1, "ok": True})
        self.assertEqual(conn.peer_pid(), client.peer_pid())  # Same process on both ends here.
        with self.assertRaises(OSError):
            winpipe.PipeServer(name)._create()  # The name is taken: first-instance rule.
        client.close()
        conn.close()
        server.close()

    def test_reads_and_writes_at_the_same_time(self):
        """The live bug: a synchronous pipe handle blocked every write while a read was pending."""
        from aqua.browser import winpipe
        name = r"\\.\pipe\aqua-test-" + uuid.uuid4().hex
        server = winpipe.PipeServer(name)
        accepted = {}
        thread = threading.Thread(target=lambda: accepted.setdefault("c", server.accept()), daemon=True)
        thread.start()
        client = None
        for _ in range(50):
            try:
                client = winpipe.connect(name)
                break
            except OSError:
                import time
                time.sleep(0.05)
        thread.join(2)
        conn = accepted["c"]
        got = {}
        reader = threading.Thread(target=lambda: got.setdefault("m", protocol.read_message(conn.read)), daemon=True)
        reader.start()  # The server now waits in read() ...
        import time
        time.sleep(0.2)
        done = threading.Event()
        threading.Thread(target=lambda: (conn.write(protocol.encode({"id": 7, "cmd": "status"})), done.set()),
                         daemon=True).start()
        self.assertTrue(done.wait(2), "write blocked behind a pending read")  # ... and can still write.
        self.assertEqual(protocol.read_message(client.read), {"id": 7, "cmd": "status"})
        client.write(protocol.encode({"id": 7, "ok": True}))
        reader.join(2)
        self.assertEqual(got["m"], {"id": 7, "ok": True})
        client.close()
        conn.close()
        server.close()

    def test_sddl_is_owner_only(self):
        from aqua.browser import winpipe
        sid = winpipe.current_user_sid()
        self.assertTrue(sid.startswith("S-1-"))
        self.assertEqual(winpipe.owner_only_sddl(sid), f"D:P(A;;GA;;;{sid})")
        with self.assertRaises(ValueError):
            winpipe.owner_only_sddl("S-1-1-0)(A;;GA;;;WD")


if __name__ == "__main__":
    unittest.main()
