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
    def __init__(self, browser: str, origin: str, *, stdin, stdout, connect_pipe, sleep=time.sleep) -> None:
        self.browser, self.origin = browser, origin
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
                    return 0  # The browser closed the port.
                pipe = self.pipe
                if pipe is not None and is_reply(message):
                    try:
                        pipe.write(protocol.encode(message))
                    except OSError:
                        pass  # Aqua went away; the pipe loop reconnects.
        except protocol.ProtocolError:
            return 1
        finally:
            self.stopped.set()
            pipe, self.pipe = self.pipe, None
            if pipe is not None:
                pipe.close()

    def _pipe_loop(self) -> None:
        while not self.stopped.is_set():
            try:
                pipe = self.connect_pipe()
            except OSError:
                self.sleep(RETRY_SECONDS)  # Aqua is not running (yet).
                continue
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
            self.sleep(RETRY_SECONDS)


def main(argv: list[str] | None = None, *, stdin=None, stdout=None, connect_pipe=None) -> int:
    browser, origin = parse_args(list(sys.argv[1:] if argv is None else argv))
    if origin != protocol.ORIGIN:
        print("aqua browser host: refused a caller that is not the Aqua extension.", file=sys.stderr)
        return 2
    if browser not in protocol.BROWSERS:
        print("aqua browser host: unknown browser.", file=sys.stderr)
        return 2
    if connect_pipe is None:
        from . import winpipe
        name = protocol.pipe_name(winpipe.current_user_sid())

        def connect_pipe():
            return winpipe.connect(name)
    relay = Relay(browser, origin, stdin=stdin or sys.stdin.buffer, stdout=stdout or sys.stdout.buffer,
                  connect_pipe=connect_pipe)
    return relay.run()


if __name__ == "__main__":
    sys.exit(main())
