"""Native messaging host: relays between the Aqua extension (stdin/stdout) and Aqua (named pipe).

The browser starts this process when the extension connects, passing the caller's origin as the
first argument. The host refuses any origin except the Aqua extension's, relays only well-formed
messages (commands from Aqua, replies from the extension), and keeps retrying the pipe while
Aqua is not running. Run as: python -I -m aqua.browser.host --browser chrome <origin>
"""

import sys
import threading
import time

from . import protocol

RETRY_SECONDS = 2.0
COMMANDS = frozenset({"status", "youtube"})


def parse_args(argv: list[str]) -> tuple[str | None, str | None]:
    browser = origin = None
    items = iter(argv)
    for item in items:
        if item == "--browser":
            browser = next(items, None)
        elif item.startswith("chrome-extension://") and origin is None:
            origin = item
    return browser, origin


def is_command(message: dict) -> bool:
    """From Aqua to the extension."""
    return isinstance(message.get("id"), int) and message.get("cmd") in COMMANDS


def is_reply(message: dict) -> bool:
    """From the extension to Aqua."""
    return isinstance(message.get("id"), int) and isinstance(message.get("ok"), bool)


class Relay:
    def __init__(self, browser: str, origin: str, *, stdin, stdout, connect_pipe, sleep=time.sleep,
                 log=None) -> None:
        self.browser, self.origin = browser, origin
        self.log = log or (lambda _event: None)
        self.stdin, self.stdout = stdin, stdout
        self.connect_pipe, self.sleep = connect_pipe, sleep
        self.pipe = None
        self.stopped = threading.Event()
        self.out_lock = threading.Lock()

    def run(self) -> int:
        worker = threading.Thread(target=self._pipe_loop, name="aqua-host-pipe", daemon=True)
        worker.start()
        try:
            while True:
                message = protocol.read_message(self.stdin.read)
                if message is None:
                    self.log("the browser closed the port; exiting")
                    return 0  # The browser closed the port.
                pipe = self.pipe
                if pipe is not None and is_reply(message):
                    try:
                        pipe.write(protocol.encode(message))
                    except OSError:
                        pass  # Aqua went away; the pipe loop reconnects.
        except protocol.ProtocolError as exc:
            self.log(f"bad message from the browser: {exc}")
            return 1
        finally:
            self.stopped.set()
            pipe, self.pipe = self.pipe, None
            if pipe is not None:
                pipe.close()

    def _pipe_loop(self) -> None:
        waiting = False
        while not self.stopped.is_set():
            try:
                pipe = self.connect_pipe()
            except OSError as exc:
                if not waiting:
                    self.log(f"waiting for Aqua (pipe not available: {exc})")
                    waiting = True
                self.sleep(RETRY_SECONDS)  # Aqua is not running (yet).
                continue
            waiting = False
            self.log("connected to Aqua")
            try:
                pipe.write(protocol.encode({"hello": "aqua-browser-host", "version": 1,
                                            "browser": self.browser, "origin": self.origin}))
                self.pipe = pipe
                while not self.stopped.is_set():
                    message = protocol.read_message(pipe.read)
                    if message is None:
                        break
                    if is_command(message):
                        data = protocol.encode(message)
                        with self.out_lock:
                            self.stdout.write(data)
                            self.stdout.flush()
            except (OSError, protocol.ProtocolError):
                pass
            finally:
                self.pipe = None
                pipe.close()
            if not self.stopped.is_set():
                self.log("Aqua disconnected; retrying")
            self.sleep(RETRY_SECONDS)


LOG_LINES = 200
BROWSER_EXES = {"brave.exe": "brave", "chrome.exe": "chrome"}


def detect_browser(pid: int | None = None, processes: dict | None = None) -> str | None:
    """The browser that started this host: walk up the parent processes (python, the venv
    launcher, cmd.exe) to brave.exe or chrome.exe. Both browsers share one registration on
    Windows, so the launcher cannot say which one it is."""
    import os
    processes = processes if processes is not None else _process_table()
    pid = os.getpid() if pid is None else pid
    for _ in range(6):
        entry = processes.get(pid)
        if entry is None:
            return None
        parent, _exe = entry
        parent_entry = processes.get(parent)
        if parent_entry is None:
            return None
        if parent_entry[1] in BROWSER_EXES:
            return BROWSER_EXES[parent_entry[1]]
        pid = parent
    return None


def _process_table() -> dict:
    """{pid: (parent pid, exe name lowercased)} from a Toolhelp snapshot."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    table = {}
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return table
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile.casefold())
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return table


def log_path(browser: str):
    from .setup import install_folder
    return install_folder() / f"host-{browser if browser in protocol.BROWSERS else 'unknown'}.log"


def log(browser: str, event: str) -> None:
    """One line in %LOCALAPPDATA%\\Aqua\\browser-bridge\\host-<browser>.log (last 200 lines kept).
    `aqua setup-browser --check` reads it to say when the extension last started the host."""
    try:
        path = log_path(browser)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8").splitlines()[-(LOG_LINES - 1):] if path.exists() else []
        lines.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {event}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def main(argv: list[str] | None = None, *, stdin=None, stdout=None, connect_pipe=None, write_log=log,
         detect=detect_browser) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        # `aqua setup-browser --check`: proves the launcher and interpreter work; relays nothing.
        print("aqua browser host: ok", flush=True)
        return 0
    browser, origin = parse_args(argv)
    if browser is None and origin == protocol.ORIGIN:
        try:
            browser = detect() or "chrome"  # Unknown Chromium browser: it uses Chrome's registration anyway.
        except OSError:
            browser = "chrome"
    if origin != protocol.ORIGIN:
        write_log(browser or "unknown", f"refused caller {str(origin)[:80]!r}")
        print("aqua browser host: refused a caller that is not the Aqua extension.", file=sys.stderr)
        return 2
    if browser not in protocol.BROWSERS:
        write_log("unknown", f"refused unknown browser {str(browser)[:20]!r}")
        print("aqua browser host: unknown browser.", file=sys.stderr)
        return 2
    write_log(browser, f"started by the extension (pid {__import__('os').getpid()})")
    try:
        return _serve(browser, origin, stdin, stdout, connect_pipe, write_log)
    except Exception as exc:  # Logged so a crash is visible in --check, then re-raised for the browser's log.
        write_log(browser, f"crashed: {type(exc).__name__}: {exc}")
        raise


def _serve(browser, origin, stdin, stdout, connect_pipe, write_log) -> int:
    if connect_pipe is None:
        from . import winpipe
        name = protocol.pipe_name(winpipe.current_user_sid())

        def connect_pipe():
            return winpipe.connect(name)
    relay = Relay(browser, origin, stdin=stdin or sys.stdin.buffer, stdout=stdout or sys.stdout.buffer,
                  connect_pipe=connect_pipe, log=lambda event: write_log(browser, event))
    return relay.run()


if __name__ == "__main__":
    sys.exit(main())
