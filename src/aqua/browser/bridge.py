"""Aqua's end of the browser bridge: a per-user pipe server that native hosts connect to.

Each connected browser (Brave, Chrome) reports its most recently active YouTube tab; a command
goes to the browser whose YouTube tab was used last. Everything a page returns is untrusted.
"""

import itertools
import threading
import time

from . import protocol

STATUS_TIMEOUT = 1.5
COMMAND_TIMEOUT = 8.0  # play_result waits up to 5 s for results to render.


class BridgeUnavailable(Exception):
    """The extension is not connected (or Aqua could not open its pipe)."""


class BridgeError(Exception):
    """The extension answered with an error."""


class BrowserConnection:
    """One native host (one browser) on the pipe."""

    def __init__(self, pipe, *, on_close=None) -> None:
        self.pipe = pipe
        self.browser: str | None = None
        self.connected_at = time.monotonic()
        self._ids = itertools.count(1)
        self._pending: dict[int, list] = {}
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._closed = False
        self._on_close = on_close

    def handshake(self, timeout: float = 5.0) -> bool:
        """Read the host's hello; the connection is kept only for the Aqua extension's origin."""
        result = {}

        def first():
            try:
                result["hello"] = protocol.read_message(self.pipe.read)
            except (OSError, protocol.ProtocolError):
                result["hello"] = None

        reader = threading.Thread(target=first, daemon=True)
        reader.start()
        reader.join(timeout)
        hello = result.get("hello")
        if (not isinstance(hello, dict) or hello.get("hello") != "aqua-browser-host"
                or hello.get("origin") != protocol.ORIGIN or hello.get("browser") not in protocol.BROWSERS):
            self.close()
            return False
        self.browser = hello["browser"]
        threading.Thread(target=self._read_loop, name=f"aqua-bridge-{self.browser}", daemon=True).start()
        return True

    def _read_loop(self) -> None:
        try:
            while True:
                message = protocol.read_message(self.pipe.read)
                if message is None:
                    break
                with self._lock:
                    slot = self._pending.get(message.get("id"))
                if slot is not None:
                    slot[1] = message
                    slot[0].set()
        except (OSError, protocol.ProtocolError):
            pass
        finally:
            self.close()

    def request(self, cmd: str, timeout: float, **fields) -> dict:
        if self._closed:
            raise BridgeUnavailable("disconnected")
        number = next(self._ids)
        slot = [threading.Event(), None]
        with self._lock:
            self._pending[number] = slot
        try:
            self.pipe.write(protocol.encode({"id": number, "cmd": cmd, **fields}))
            if not slot[0].wait(timeout):
                raise BridgeError("timeout")
            if slot[1] is None:
                raise BridgeUnavailable("disconnected")
            return slot[1]
        except OSError as exc:
            self.close()
            raise BridgeUnavailable("disconnected") from exc
        finally:
            with self._lock:
                self._pending.pop(number, None)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._lock:
            for slot in self._pending.values():
                slot[0].set()  # Waiters see no reply: disconnected.
        try:
            self.pipe.close()
        finally:
            if self._on_close is not None:
                self._on_close(self)


class BrowserBridge:
    """The bridge as tools see it: ``youtube(action, args)`` and ``connected``."""

    def __init__(self, *, server=None) -> None:
        self._server = server  # A winpipe.PipeServer (or a fake with accept()/close()).
        self._connections: dict[str, BrowserConnection] = {}
        self._lock = threading.Lock()
        self.error: str | None = None

    def start(self) -> "BrowserBridge":
        """Open the pipe and accept hosts in the background. Failure leaves the bridge
        disconnected (``error`` says why), never stops Aqua."""
        if self._server is None:
            try:
                from . import winpipe
                self._server = winpipe.PipeServer(protocol.pipe_name(winpipe.current_user_sid()))
            except Exception as exc:  # Not Windows, or no access.
                self.error = str(exc)
                return self
        threading.Thread(target=self._accept_loop, name="aqua-bridge-accept", daemon=True).start()
        return self

    def _accept_loop(self) -> None:
        while True:
            try:
                pipe = self._server.accept()
            except OSError as exc:
                self.error = str(exc)  # E.g. another Aqua owns the pipe.
                return
            if pipe is None:
                return
            connection = BrowserConnection(pipe, on_close=self._forget)
            threading.Thread(target=self._admit, args=(connection,), daemon=True).start()

    def _admit(self, connection: BrowserConnection) -> None:
        if not connection.handshake():
            return
        with self._lock:
            old = self._connections.get(connection.browser)
            self._connections[connection.browser] = connection
        if old is not None and old is not connection:
            old.close()  # The browser restarted its host.

    def _forget(self, connection: BrowserConnection) -> None:
        with self._lock:
            if self._connections.get(connection.browser) is connection:
                del self._connections[connection.browser]

    def add_connection(self, connection: BrowserConnection) -> None:
        """For tests: a connection that has already said hello."""
        with self._lock:
            self._connections[connection.browser] = connection

    @property
    def connected(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(name for name, c in self._connections.items() if not c.closed)

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
        with self._lock:
            connections = list(self._connections.values())
        for connection in connections:
            connection.close()

    def youtube(self, action: str, args: dict) -> dict:
        """Run ``action`` in the most recently active YouTube tab of any connected browser.
        Returns the extension's result plus ``browser``; raises BridgeUnavailable or BridgeError."""
        with self._lock:
            connections = [c for c in self._connections.values() if not c.closed]
        if not connections:
            raise BridgeUnavailable(self.error or "not connected")
        best, best_used = None, None
        for connection in connections:
            try:
                status = connection.request("status", STATUS_TIMEOUT)
            except (BridgeUnavailable, BridgeError):
                continue
            tab = (status.get("result") or {}).get("tab") if status.get("ok") else None
            used = tab.get("lastAccessed") if isinstance(tab, dict) else None
            if isinstance(used, (int, float)) and (best_used is None or used > best_used):
                best, best_used = connection, used
        if best is None:
            if action != "search":
                return {"browser": None, "error": "no_tab"}
            # A search can open a new tab: use the browser that connected most recently.
            best = max(connections, key=lambda c: c.connected_at)
        reply = best.request("youtube", COMMAND_TIMEOUT, action=action, args=args)
        if not reply.get("ok"):
            raise BridgeError(str(reply.get("error") or "failed")[:200])
        result = reply.get("result")
        return {**(result if isinstance(result, dict) else {}), "browser": best.browser}
