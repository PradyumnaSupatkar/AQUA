"""Disable telemetry in sherpa's own ONNX Runtime, before model creation."""

import importlib.util
import os
from pathlib import Path
import sys
from threading import Lock

from .models import WakeError

_runtime_owner = None
_runtime_lock = Lock()


def _disable_bundled_runtime(path: Path):
    """Use the public, backward-compatible OrtApi v1 prefix on Windows.

    sherpa bundles a separate runtime from Python's onnxruntime package.
    Disabling the latter does not disable the bundled DLL. These seven named
    fields are the start of the documented C ABI, not private binary offsets:
    https://github.com/microsoft/onnxruntime/blob/v1.28.2/include/onnxruntime/core/session/onnxruntime_c_api.h

    Keep one OrtEnv alive for the process: the public prefix deliberately
    excludes later release functions. This is a bounded single owner, not a
    new environment per detection/session. Windows reclaims it at process exit.
    """
    import ctypes

    call = ctypes.WINFUNCTYPE
    status = ctypes.c_void_p
    create_env = call(status, ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p))
    disable = call(status, ctypes.c_void_p)

    class OrtApiPrefix(ctypes.Structure):
        _fields_ = [
            ("CreateStatus", ctypes.c_void_p),
            ("GetErrorCode", ctypes.c_void_p),
            ("GetErrorMessage", ctypes.c_void_p),
            ("CreateEnv", create_env),
            ("CreateEnvWithCustomLogger", ctypes.c_void_p),
            ("EnableTelemetryEvents", ctypes.c_void_p),
            ("DisableTelemetryEvents", disable),
        ]

    class OrtApiBase(ctypes.Structure):
        _fields_ = [
            ("GetApi", call(ctypes.POINTER(OrtApiPrefix), ctypes.c_uint32)),
            ("GetVersionString", ctypes.c_void_p),
        ]

    library = ctypes.WinDLL(str(path))
    library.OrtGetApiBase.restype = ctypes.POINTER(OrtApiBase)
    base = library.OrtGetApiBase()
    if not base:
        raise WakeError("The wake runtime did not expose its public ONNX Runtime API.")
    api_pointer = base.contents.GetApi(1)
    if not api_pointer:
        raise WakeError("The wake runtime does not support ONNX Runtime API version 1.")
    api = api_pointer.contents
    environment = ctypes.c_void_p()
    if api.CreateEnv(3, b"Aqua-wake-private", ctypes.byref(environment)) or not environment:
        raise WakeError("Could not initialize the private wake runtime environment.")
    if api.DisableTelemetryEvents(environment):
        raise WakeError("Could not disable telemetry in the wake runtime; startup stopped.")
    return library, environment, api_pointer


def configure_runtime() -> None:
    """Disable future 1DS telemetry and the installed Windows ETW provider.

    All settings apply only to this process. No registry or Windows telemetry
    preferences are changed. A missing/unsupported bundled API fails closed.
    """
    global _runtime_owner
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    if sys.platform != "win32":
        return
    with _runtime_lock:
        if _runtime_owner is not None:
            return
        try:
            spec = importlib.util.find_spec("sherpa_onnx")
            if spec is None or spec.origin is None:
                raise WakeError("Hands-free dependencies are missing; reinstall Aqua's dependencies (pip install -e .).")
            runtime_path = Path(spec.origin).parent / "lib" / "onnxruntime.dll"
            if not runtime_path.is_file():
                raise WakeError("The bundled wake ONNX Runtime DLL is missing; startup stopped.")
            _runtime_owner = _disable_bundled_runtime(runtime_path)
        except (ImportError, OSError, AttributeError, TypeError, ValueError) as exc:
            raise WakeError("Could not disable bundled wake-runtime telemetry; startup stopped.") from exc
