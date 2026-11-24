"""Per-user named pipes (Windows, ctypes). No network: local pipes only, remote clients rejected.

Server (Aqua): the first instance is created with FILE_FLAG_FIRST_PIPE_INSTANCE, so Aqua fails
instead of sharing a name another process already owns, and every instance carries a DACL that
grants access to the current user's SID only. Client (the native host): connects with
identification-level impersonation only, then checks that the pipe's server process runs as
the same user before sending anything.
"""

import ctypes
import threading
from ctypes import wintypes

PIPE_ACCESS_DUPLEX = 0x3
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_REJECT_REMOTE_CLIENTS = 0x8
PIPE_UNLIMITED_INSTANCES = 255
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
OPEN_EXISTING = 3
SECURITY_SQOS_PRESENT, SECURITY_IDENTIFICATION = 0x00100000, 0x00010000
TOKEN_QUERY, TOKEN_USER_CLASS = 0x8, 1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_BROKEN_PIPE, ERROR_PIPE_CONNECTED, ERROR_NO_DATA, ERROR_OPERATION_ABORTED = 109, 535, 232, 995
ERROR_IO_PENDING, ERROR_INVALID_HANDLE = 997, 6
INVALID_HANDLE = wintypes.HANDLE(-1).value
BUFFER = 64 * 1024


class PipeError(OSError):
    pass


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", wintypes.LPVOID),
                ("bInheritHandle", wintypes.BOOL)]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]


def _dlls():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    H, D, B, P = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.LPVOID
    kernel32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, D, D, D, D, D, D, ctypes.POINTER(_SecurityAttributes)]
    kernel32.CreateNamedPipeW.restype = H
    kernel32.ConnectNamedPipe.argtypes, kernel32.ConnectNamedPipe.restype = [H, P], B
    kernel32.DisconnectNamedPipe.argtypes, kernel32.DisconnectNamedPipe.restype = [H], B
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, D, D, P, D, D, H]
    kernel32.CreateFileW.restype = H
    kernel32.ReadFile.argtypes = [H, P, D, ctypes.POINTER(D), P]
    kernel32.ReadFile.restype = B
    kernel32.WriteFile.argtypes = [H, P, D, ctypes.POINTER(D), P]
    kernel32.WriteFile.restype = B
    kernel32.CloseHandle.argtypes, kernel32.CloseHandle.restype = [H], B
    kernel32.CancelIoEx.argtypes, kernel32.CancelIoEx.restype = [H, P], B
    kernel32.CreateEventW.argtypes, kernel32.CreateEventW.restype = [P, B, B, wintypes.LPCWSTR], H
    kernel32.GetOverlappedResult.argtypes = [H, P, ctypes.POINTER(D), B]
    kernel32.GetOverlappedResult.restype = B
    kernel32.GetCurrentProcess.restype = H
    kernel32.OpenProcess.argtypes, kernel32.OpenProcess.restype = [D, B, D], H
    kernel32.GetNamedPipeServerProcessId.argtypes = [H, ctypes.POINTER(wintypes.ULONG)]
    kernel32.GetNamedPipeServerProcessId.restype = B
    kernel32.GetNamedPipeClientProcessId.argtypes = [H, ctypes.POINTER(wintypes.ULONG)]
    kernel32.GetNamedPipeClientProcessId.restype = B
    kernel32.LocalFree.argtypes, kernel32.LocalFree.restype = [P], P
    advapi32.OpenProcessToken.argtypes, advapi32.OpenProcessToken.restype = [H, D, ctypes.POINTER(H)], B
    advapi32.GetTokenInformation.argtypes = [H, ctypes.c_int, P, D, ctypes.POINTER(D)]
    advapi32.GetTokenInformation.restype = B
    advapi32.ConvertSidToStringSidW.argtypes = [P, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = B
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, D, ctypes.POINTER(P), ctypes.POINTER(D)]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = B
    return kernel32, advapi32


_KERNEL32 = _ADVAPI32 = None


def _api():
    global _KERNEL32, _ADVAPI32
    if _KERNEL32 is None:
        _KERNEL32, _ADVAPI32 = _dlls()
    return _KERNEL32, _ADVAPI32


def _fail(what: str) -> PipeError:
    code = ctypes.get_last_error()
    return PipeError(code, f"{what} failed (Windows error {code}).")


def _token_sid(token) -> str:
    _, advapi32 = _api()
    size = wintypes.DWORD()
    advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    if not advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, buffer, size, ctypes.byref(size)):
        raise _fail("GetTokenInformation")
    user = _SidAndAttributes.from_buffer(buffer)
    text = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(user.Sid, ctypes.byref(text)):
        raise _fail("ConvertSidToStringSid")
    try:
        return text.value
    finally:
        _api()[0].LocalFree(text)


def _process_token_sid(process) -> str:
    kernel32, advapi32 = _api()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
        raise _fail("OpenProcessToken")
    try:
        return _token_sid(token)
    finally:
        kernel32.CloseHandle(token)


def current_user_sid() -> str:
    return _process_token_sid(_api()[0].GetCurrentProcess())


def process_user_sid(pid: int) -> str:
    kernel32, _ = _api()
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        raise _fail("OpenProcess")
    try:
        return _process_token_sid(process)
    finally:
        kernel32.CloseHandle(process)


def owner_only_sddl(sid: str) -> str:
    """Protected DACL: full access for this user's SID only (no SYSTEM, no Administrators)."""
    if not sid.startswith("S-1-") or not all(part.isdigit() for part in sid[4:].split("-")):
        raise ValueError("Not a SID.")
    return f"D:P(A;;GA;;;{sid})"


class _Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t), ("Offset", wintypes.DWORD),
                ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]


def _overlapped_io(call, handle, *args) -> tuple[bool, int, int]:
    """Run an overlapped call to completion: (ok, bytes transferred, error code)."""
    kernel32, _ = _api()
    overlapped = _Overlapped()
    overlapped.hEvent = kernel32.CreateEventW(None, True, False, None)
    if not overlapped.hEvent:
        raise _fail("CreateEvent")
    done = wintypes.DWORD()
    try:
        ok = bool(call(handle, *args, ctypes.byref(done) if args else None, ctypes.byref(overlapped))) if args \
            else bool(call(handle, ctypes.byref(overlapped)))
        error = 0 if ok else ctypes.get_last_error()
        if not ok and error == ERROR_IO_PENDING:
            ok = bool(kernel32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(done), True))
            error = 0 if ok else ctypes.get_last_error()
        return ok, done.value, error
    finally:
        kernel32.CloseHandle(overlapped.hEvent)


class PipeConnection:
    """One connected pipe end. Overlapped I/O: one thread can wait in read() while another
    writes (a synchronous pipe handle serialises them, so a pending read would block every write)."""

    def __init__(self, handle, *, server: bool) -> None:
        self._handle, self._server = handle, server
        self._write_lock = threading.Lock()

    def read(self, size: int) -> bytes:
        handle = self._handle
        if handle is None:
            return b""
        buffer = ctypes.create_string_buffer(min(size, BUFFER))
        ok, got, error = _overlapped_io(_api()[0].ReadFile, handle, buffer, len(buffer))
        if not ok:
            if error in (ERROR_BROKEN_PIPE, ERROR_NO_DATA, ERROR_OPERATION_ABORTED, ERROR_INVALID_HANDLE):
                return b""
            raise PipeError(error, f"ReadFile failed (Windows error {error}).")
        return buffer.raw[:got]

    def write(self, data: bytes) -> None:
        with self._write_lock:
            view = memoryview(data)
            while view:
                handle = self._handle
                if handle is None:
                    raise PipeError(ERROR_BROKEN_PIPE, "The pipe is closed.")
                chunk = ctypes.create_string_buffer(bytes(view[:BUFFER]), len(view[:BUFFER]))
                ok, done, error = _overlapped_io(_api()[0].WriteFile, handle, chunk, len(chunk))
                if not ok:
                    raise PipeError(error, f"WriteFile failed (Windows error {error}).")
                view = view[done:]

    def peer_pid(self) -> int:
        kernel32, _ = _api()
        pid = wintypes.ULONG()
        query = kernel32.GetNamedPipeClientProcessId if self._server else kernel32.GetNamedPipeServerProcessId
        if not query(self._handle, ctypes.byref(pid)):
            raise _fail("GetNamedPipe*ProcessId")
        return pid.value

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        kernel32, _ = _api()
        kernel32.CancelIoEx(handle, None)  # Wakes a thread waiting in read().
        if self._server:
            kernel32.DisconnectNamedPipe(handle)
        kernel32.CloseHandle(handle)


class PipeServer:
    """Accepts clients one at a time on a per-user pipe."""

    def __init__(self, name: str, sid: str | None = None) -> None:
        self.name = name
        self._sid = sid or current_user_sid()
        self._first = True
        self._waiting = None
        self._closed = False

    def _create(self):
        kernel32, advapi32 = _api()
        descriptor = wintypes.LPVOID()
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                owner_only_sddl(self._sid), 1, ctypes.byref(descriptor), None):
            raise _fail("ConvertStringSecurityDescriptorToSecurityDescriptor")
        try:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            mode = (PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED
                    | (FILE_FLAG_FIRST_PIPE_INSTANCE if self._first else 0))
            handle = kernel32.CreateNamedPipeW(self.name, mode, PIPE_REJECT_REMOTE_CLIENTS,
                                               PIPE_UNLIMITED_INSTANCES, BUFFER, BUFFER, 0, ctypes.byref(attributes))
        finally:
            kernel32.LocalFree(descriptor)
        if handle == INVALID_HANDLE or handle is None:
            raise _fail("CreateNamedPipe (is another Aqua already running?)")
        self._first = False
        return handle

    def accept(self) -> PipeConnection | None:
        """The next client, or None once the server is closed."""
        kernel32, _ = _api()
        if self._closed:
            return None
        handle = self._create()
        self._waiting = handle
        ok, _, error = _overlapped_io(kernel32.ConnectNamedPipe, handle)
        self._waiting = None
        ok = ok or error == ERROR_PIPE_CONNECTED
        if not ok or self._closed:
            kernel32.CloseHandle(handle)
            if self._closed:
                return None
            raise PipeError(error, f"ConnectNamedPipe failed (Windows error {error}).")
        return PipeConnection(handle, server=True)

    def close(self) -> None:
        self._closed = True
        waiting = self._waiting
        if waiting is not None:
            _api()[0].CancelIoEx(waiting, None)  # Overlapped: the waiting accept() returns at once.


def connect(name: str) -> PipeConnection:
    """Open the pipe as a client and check that its server runs as this same user."""
    kernel32, _ = _api()
    handle = kernel32.CreateFileW(name, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING,
                                  FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, None)
    if handle == INVALID_HANDLE or handle is None:
        raise _fail("Opening the Aqua pipe")
    connection = PipeConnection(handle, server=False)
    try:
        if process_user_sid(connection.peer_pid()) != current_user_sid():
            raise PipeError(5, "The Aqua pipe is owned by another user; not connecting.")
    except Exception:
        connection.close()
        raise
    return connection
