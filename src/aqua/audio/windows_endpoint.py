"""Read-only Windows capture-endpoint identity and volume metadata.

No microphone is opened here. Only COM getters, WinMM identity queries, and
PortAudio enumeration are used; there are no setters or registry operations.
Unavailable mappings remain unknown rather than guessed from a friendly name.
"""

from contextlib import contextmanager
import ctypes
import sys
import uuid

from .backend import get_backend

HRESULT = ctypes.c_int32
UINT = ctypes.c_uint32
RPC_E_CHANGED_MODE = 0x80010106
CAPTURE_ROLES = (("console", 0), ("multimedia", 1), ("communications", 2))


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _check(result: int, operation: str) -> None:
    if result < 0:
        raise OSError(f"{operation} failed (HRESULT 0x{result & 0xffffffff:08x})")


def _method(pointer, slot: int, result, *arguments):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[slot])


def _release(pointer) -> None:
    if pointer:
        _method(pointer, 2, UINT)(pointer)


@contextmanager
def _apartment(ole):
    result = ole.CoInitializeEx(None, 0)
    initialized = result >= 0
    if not initialized and (result & 0xffffffff) != RPC_E_CHANGED_MODE:
        _check(result, "Initialize COM")
    # PortAudio or a host application can already own a different apartment.
    # In that case we may use it, but must not decrement its initialization.
    try:
        yield
    finally:
        if initialized:
            ole.CoUninitialize()


def _identity(device, ole) -> str:
    memory = ctypes.c_void_p()
    try:
        _check(_method(device, 5, HRESULT, ctypes.POINTER(ctypes.c_void_p))(
            device, ctypes.byref(memory)), "Read endpoint identity")
        if not memory:
            raise OSError("The capture endpoint returned no identity")
        return ctypes.wstring_at(memory)
    finally:
        if memory:
            ole.CoTaskMemFree(memory)


def _volume(volume) -> dict:
    """IAudioEndpointVolume getter slots only; dB is endpoint level, not dBFS."""
    count, mute = UINT(), ctypes.c_int32()
    scalar, level = ctypes.c_float(), ctypes.c_float()
    _check(_method(volume, 5, HRESULT, ctypes.POINTER(UINT))(volume, ctypes.byref(count)), "Read channel count")
    if count.value > 64:
        raise OSError("The capture endpoint reported an invalid channel count")
    _check(_method(volume, 9, HRESULT, ctypes.POINTER(ctypes.c_float))(volume, ctypes.byref(scalar)), "Read endpoint volume")
    _check(_method(volume, 8, HRESULT, ctypes.POINTER(ctypes.c_float))(volume, ctypes.byref(level)), "Read endpoint dB")
    _check(_method(volume, 15, HRESULT, ctypes.POINTER(ctypes.c_int32))(volume, ctypes.byref(mute)), "Read endpoint mute")
    channels = []
    for index in range(count.value):
        value = ctypes.c_float()
        _check(_method(volume, 13, HRESULT, UINT, ctypes.POINTER(ctypes.c_float))(
            volume, index, ctypes.byref(value)), "Read channel volume")
        channels.append(round(value.value * 100, 3))
    lower, upper, step = ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
    _check(_method(volume, 20, HRESULT, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float))(
        volume, ctypes.byref(lower), ctypes.byref(upper), ctypes.byref(step)), "Read endpoint dB range")
    return {
        "volume_percent": round(scalar.value * 100, 3), "muted": bool(mute.value),
        "channels": count.value, "channel_percents": channels,
        "level_db": float(level.value),
        "range_db": {"minimum": float(lower.value), "maximum": float(upper.value), "step": float(step.value)},
    }


def _defaults(ole) -> list[dict]:
    enumerator = ctypes.c_void_p()
    clsid = _Guid.parse("BCDE0395-E52F-467C-8E3D-C4579291692E")
    iid = _Guid.parse("A95664D2-9614-4F35-A746-DE8DB63617E6")
    volume_iid = _Guid.parse("5CDF2C82-841E-4546-9722-0CF74078229A")
    rows = []
    try:
        _check(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(enumerator)), "Create endpoint enumerator")
        for name, role in CAPTURE_ROLES:
            device, volume = ctypes.c_void_p(), ctypes.c_void_p()
            try:
                _check(_method(enumerator, 4, HRESULT, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                    enumerator, 1, role, ctypes.byref(device)), "Read default capture endpoint")
                identity = _identity(device, ole)
                _check(_method(device, 3, HRESULT, ctypes.POINTER(_Guid), UINT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                    device, ctypes.byref(volume_iid), 23, None, ctypes.byref(volume)), "Read endpoint volume interface")
                rows.append({"role": name, "endpoint_id": identity, **_volume(volume)})
            finally:
                _release(volume)
                _release(device)
    finally:
        _release(enumerator)
    return rows


def _winmm_devices() -> list[dict]:
    class Caps(ctypes.Structure):
        _fields_ = [
            ("manufacturer", ctypes.c_uint16), ("product", ctypes.c_uint16),
            ("driver", UINT), ("name", ctypes.c_wchar * 32),
            ("formats", UINT), ("channels", ctypes.c_uint16), ("reserved", ctypes.c_uint16),
        ]

    winmm = ctypes.WinDLL("winmm")
    winmm.waveInGetNumDevs.restype = UINT
    winmm.waveInGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(Caps), UINT]
    winmm.waveInGetDevCapsW.restype = UINT
    winmm.waveInMessage.argtypes = [ctypes.c_void_p, UINT, ctypes.c_size_t, ctypes.c_size_t]
    winmm.waveInMessage.restype = UINT
    rows = []
    for index in range(min(winmm.waveInGetNumDevs(), 256)):
        caps = Caps()
        if winmm.waveInGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps)):
            continue
        size = UINT()
        # Public read-only WinMM driver messages from mmddk.h:
        # DRV_QUERYFUNCTIONINSTANCEIDSIZE and DRV_QUERYFUNCTIONINSTANCEID.
        result = winmm.waveInMessage(ctypes.c_void_p(index), 0x0800 + 18, ctypes.addressof(size), 0)
        identity = None
        if result == 0 and 0 < size.value <= 65536:
            text = ctypes.create_unicode_buffer((size.value + 1) // 2)
            if winmm.waveInMessage(ctypes.c_void_p(index), 0x0800 + 17, ctypes.addressof(text), size.value) == 0:
                identity = text.value or None
        rows.append({"wavein_index": index, "name": caps.name, "endpoint_id": identity})
    return rows


def _map_devices(backend, ole, defaults: list[dict]) -> list[dict]:
    devices, apis = backend.query_devices(), backend.query_hostapis()
    winmm = _winmm_devices()
    library = ctypes.CDLL(backend._libname)
    wasapi = getattr(library, "PaWasapi_GetIMMDevice", None)
    if wasapi is not None:
        wasapi.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        wasapi.restype = ctypes.c_int
    console = next((item["endpoint_id"] for item in defaults if item["role"] == "console"), None)
    rows = []
    for index, device in enumerate(devices):
        if device["max_input_channels"] < 1:
            continue
        host = apis[device["hostapi"]]["name"]
        identity, wavein_index = None, None
        mapping = "unavailable for this audio backend"
        if host == "Windows WASAPI" and wasapi is not None:
            pointer = ctypes.c_void_p()
            if wasapi(index, ctypes.byref(pointer)) == 0 and pointer:
                # PortAudio owns this IMMDevice; do not Release its borrowed
                # reference. GetId allocates a separate string we do free.
                identity = _identity(pointer, ole)
                mapping = "PortAudio PaWasapi_GetIMMDevice/GetId"
        elif host == "MME":
            matches = [item for item in winmm if item["name"] == device["name"]]
            if len(matches) == 1:
                identity, wavein_index = matches[0]["endpoint_id"], matches[0]["wavein_index"]
                mapping = "unique WinMM input name and function-instance ID" if identity else "WinMM identity query unavailable"
            elif len(matches) > 1:
                mapping = "ambiguous WinMM input names; identity not inferred"
        rows.append({
            "index": index, "name": device["name"], "host_api": host,
            "endpoint_id": identity, "mapping": mapping, "wavein_index": wavein_index,
            "matches_console_default": identity == console if identity and console else None,
        })
    return rows


def inspect_capture_endpoints() -> dict:
    """Return metadata or a qualified unavailable result; never change settings."""
    result = {"available": False, "defaults": [], "devices": [], "errors": []}
    if sys.platform != "win32":
        result["errors"].append("Windows capture metadata is unavailable on this platform")
        return result
    try:
        backend = get_backend()
        # WinDLL preserves raw HRESULTs, allowing RPC_E_CHANGED_MODE to be
        # handled when PortAudio already owns an STA. OleDLL raises first.
        ole = ctypes.WinDLL("ole32")
        ole.CoInitializeEx.argtypes = [ctypes.c_void_p, UINT]
        ole.CoInitializeEx.restype = HRESULT
        ole.CoCreateInstance.argtypes = [ctypes.POINTER(_Guid), ctypes.c_void_p, UINT, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)]
        ole.CoCreateInstance.restype = HRESULT
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole.CoTaskMemFree.restype = None
        ole.CoUninitialize.argtypes = []
        ole.CoUninitialize.restype = None
        with _apartment(ole):
            result["defaults"] = _defaults(ole)
            result["available"] = True
            try:
                result["devices"] = _map_devices(backend, ole, result["defaults"])
            except (OSError, AttributeError, TypeError, ValueError, KeyError) as exc:
                result["errors"].append(f"PortAudio endpoint mapping unavailable ({type(exc).__name__})")
    except Exception as exc:
        # Metadata is optional; cancellation/SystemExit still propagate. Do
        # not echo driver exception text or change settings to get a result.
        result["errors"].append(f"Windows capture metadata unavailable ({type(exc).__name__})")
    return result
