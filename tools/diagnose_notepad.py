"""Explicit, read-only Notepad UIA probe. No model, focus, input or content dump.

The fixed child is killable even if a provider hangs. Progress survives timeout;
only exception categories are reported, never provider messages/document text.
This diagnostic is NOT registered as an agent tool or a text-entry capability.
"""

import argparse
from collections import Counter, deque
from contextlib import redirect_stdout
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

from aqua.desktop.backend import (NativeWindows, UIAutomation, _identity,
                                  _property, _runtime_id, match_application)
from aqua.desktop.session import ObservationSession, run_worker

MAX_NODES = 96
MAX_DEPTH = 8
SOFT_SECONDS = 3.5
HARD_SECONDS = 6.0


def native_model_metadata(element):
    """Read-only OBJID_NATIVEOM/type-info query; no document methods invoked."""
    try:
        import ctypes
        from ctypes import wintypes
        from comtypes.automation import IDispatch
        handle = int(element.CurrentNativeWindowHandle)
        if handle <= 0:
            return {"native_object_model": "no_control_handle"}
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.GetWindowThreadProcessId.restype = wintypes.DWORD
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value != int(element.CurrentProcessId):
            return {"native_object_model": "stale_control"}
        function = ctypes.WinDLL("oleacc").AccessibleObjectFromWindow
        function.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
        function.restype = wintypes.LONG
        pointer = ctypes.POINTER(IDispatch)()
        iid = IDispatch._iid_
        status = function(handle, 0xFFFFFFF0, ctypes.byref(iid), ctypes.byref(pointer))
        if status != 0 or not pointer:
            return {"native_object_model": "unavailable", "native_model_hresult": int(status)}
        # Type metadata only. Never invoke Range, Text, Selection or setters.
        name = pointer.GetTypeInfo(0).GetDocumentation(-1)[0]
        metadata = {"native_object_model": "available",
                    "native_model_type": name if name in {"ITextDocument", "ITextDocument2"} else "other"}
        if name == "ITextDocument2":
            from comtypes.client.dynamic import Dispatch
            document = Dispatch(pointer)
            # Creates only a private range object, not a UI selection. No
            # text, focus, gravity setter or selection setter is invoked.
            span = document.Range2(0x3FFFFFFF, 0x3FFFFFFF)
            start, end = int(span.Start), int(span.End)
            metadata["native_end_range_collapsed"] = start == end
            metadata["native_end_range_at_story_end"] = end == int(span.StoryLength) - 1
            metadata["native_range_gravity"] = int(span.Gravity)
            length = int(span.StoryLength)
            metadata["native_story_end_delta"] = length - end
            tail = document.Range2(max(0, length - 1), length)
            sample = tail.Text  # At most one UTF-16 unit; never returned.
            metadata["native_final_eop"] = sample == "\r"
            metadata["native_tail_units"] = len(sample.encode("utf-16-le")) // 2
        return metadata
    except Exception as exc:
        return {"native_object_model": "error", "native_model_exception": type(exc).__name__}


def describe_editor(element, get_pattern, pattern_names, native_probe=None):
    """Caller has validated native identity; return metadata, never text/IDs."""
    row = {name: _property(element, "Current" + name) for name in (
        "IsPassword", "IsEnabled", "IsOffscreen", "IsKeyboardFocusable", "HasKeyboardFocus")}
    if (row["IsPassword"] is not False or row["IsEnabled"] is not True
            or row["IsOffscreen"] is not False):
        row["status"] = "ineligible"
        return row
    try:
        row["native_control_handle_available"] = int(element.CurrentNativeWindowHandle) > 0
    except Exception:
        row["native_control_handle_available"] = None
    if native_probe is not None:
        row.update(native_probe(element))
    try:
        _runtime_id(element)
        row["private_runtime_identity_available"] = True
    except Exception:
        row["private_runtime_identity_available"] = False
    patterns, text_pattern, value_pattern = {}, None, None
    for name in pattern_names:
        try:
            pattern = get_pattern(element, name)
            patterns[name] = "available"
            if name == "Text":
                text_pattern = pattern
            if name == "Value":
                value_pattern = pattern
                row["value_readonly"] = _property(pattern, "CurrentIsReadOnly")
        except Exception as exc:
            patterns[name] = "absent" if type(exc).__name__ == "NoPatternInterfaceError" else type(exc).__name__
    row["patterns"] = patterns
    row["text_empty_state"] = "unknown"
    if text_pattern is not None:
        try:
            # Two read-only, bounded checks. No stripping: whitespace is content.
            # GetText(1) never requests the whole document, and its output is NOT
            # returned. A collapsed range alone does not prove writable emptiness.
            span = text_pattern.DocumentRange
            sample = span.GetText(1)
            collapsed = span.CompareEndpoints(0, span, 1) == 0  # Start vs End
            row["text_range_collapsed"] = collapsed
            row["text_empty_state"] = ("empty" if sample == "" and collapsed
                                       else "nonempty" if type(sample) is str and sample != ""
                                       else "inconsistent_or_unknown")
        except Exception as exc:
            row["text_read_error"] = type(exc).__name__
    # Only read Value after the bounded Text check reports empty. Nothing from
    # CurrentValue leaves this function, even if a concurrent edit races it.
    if value_pattern is not None and row["text_empty_state"] == "empty":
        try:
            row["value_empty_state"] = "empty" if value_pattern.CurrentValue == "" else "nonempty"
        except Exception as exc:
            row["value_read_error"] = type(exc).__name__
    row["status"] = "metadata_only"
    return row


def probe(*, native=None, ui=None, get_pattern=None, pattern_names=None, native_probe=None, progress=lambda stage: None):
    """Only one visible OS-verified, nonprivileged Notepad window is examined."""
    started = perf_counter()
    deadline = started + SOFT_SECONDS
    native = native or NativeWindows()
    progress("native_enumeration")
    candidates = []
    for handle in native.visible_handles(deadline):
        if perf_counter() >= deadline:
            return {"status": "deadline", "stage": "native_enumeration"}
        info = native.window_info(handle)
        if match_application(info, ("notepad",)) == "notepad":
            candidates.append(info)
    if len(candidates) != 1 or native.truncated:
        return {"status": "ambiguous" if candidates else "empty", "native_matches": len(candidates),
                "truncated": native.truncated}
    info = candidates[0]
    if not native.interaction_allowed(info["pid"]):
        return {"status": "protected", "native_matches": 1}
    progress("uia_initialization")
    ui = ui or UIAutomation()
    if get_pattern is None:
        from pywinauto.uia_defines import get_elem_interface, pattern_ids
        get_pattern, pattern_names = get_elem_interface, tuple(sorted(pattern_ids))
        native_probe = native_model_metadata if native_probe is None else native_probe
    progress("uia_binding")
    root = ui.bind(info["handle"])
    identity = _identity(info, root)
    queue, counts, editors, visited, truncated = deque([(root, 0)]), Counter(), [], 0, False
    progress("tree_and_patterns")
    while queue and visited < MAX_NODES and perf_counter() < deadline:
        element, depth = queue.popleft()
        visited += 1
        if (int(element.CurrentProcessId) != info["pid"]
                or _property(element, "CurrentIsPassword") is not False
                or _property(element, "CurrentIsOffscreen") is not False):
            continue
        kind = ui.control_type(element)
        counts[kind] += 1
        if kind in {"Document", "Edit"}:
            # Never output names, values, selection or document contents.
            editors.append({"control_type": kind, "depth": depth,
                            **describe_editor(element, get_pattern, pattern_names, native_probe)})
        if kind in {"Document", "Edit", "Text"}:
            continue
        if depth >= MAX_DEPTH:
            truncated = True
            continue
        child = ui.first_child(element)
        while child:
            if visited + len(queue) >= MAX_NODES or perf_counter() >= deadline:
                truncated = True
                break
            queue.append((child, depth + 1))
            child = ui.next_sibling(child)
    progress("identity_revalidation")
    after = native.window_info(info["handle"])
    if (match_application(after, ("notepad",)) != "notepad"
            or not native.interaction_allowed(info["pid"])
            or _identity(after, ui.bind(info["handle"])) != identity):
        return {"status": "stale_window"}
    return {"status": "ok", "native_matches": 1, "visited": visited, "types": dict(counts),
            "truncated": truncated or bool(queue), "editors": editors,
            "elapsed_seconds": round(perf_counter() - started, 6)}


def emit(data):
    print(json.dumps(data, ensure_ascii=True, allow_nan=False), flush=True)


def child_probe():
    try:
        # Keep third-party warnings/debug output away from JSON/progress.
        with redirect_stdout(sys.stderr):
            result = probe(progress=lambda stage: emit_progress(stage))
    except Exception as exc:
        result = {"status": "error", "exception": type(exc).__name__}
    emit({"result": result})


def emit_progress(stage):
    # redirect_stdout must not redirect our protocol as well.
    sys.__stdout__.write(json.dumps({"stage": stage}) + "\n")
    sys.__stdout__.flush()


def isolated_probe():
    started = perf_counter()
    process = subprocess.Popen(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--live", "--_worker"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), shell=False)
    timeout = False
    try:
        try:
            output, errors = process.communicate(timeout=HARD_SECONDS)
        except subprocess.TimeoutExpired:
            timeout = True
            process.kill()
            output, errors = process.communicate(timeout=.5)
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=.5)
        raise
    finally:
        process.stdout.close()
        process.stderr.close()
    records = []
    if len(output) <= 32768:
        for line in output.splitlines():
            try:
                records.append(json.loads(line))
            except (ValueError, UnicodeError):
                pass
    return {"probe_process": {"returncode": process.returncode, "timeout": timeout,
                              "stdout_bytes": len(output), "stderr_bytes": len(errors),
                              "warning_detected": b"Warning" in errors,
                              "elapsed_seconds": round(perf_counter() - started, 6)},
            "records": records}


def discovery_trials(trials):
    for attempt in range(1, trials + 1):
        started = perf_counter()
        raw = run_worker({"operation": "list_windows", "scope": ["notepad"]})
        emit({"discovery": "worker", "attempt": attempt, "status": raw["status"],
              "windows": len(raw.get("windows", [])), "truncated": raw.get("truncated"),
              "elapsed_seconds": round(perf_counter() - started, 6)})
        observer = ObservationSession()
        observer.begin_task(("notepad",))
        listed = observer.list_windows()
        emit({"discovery": "fresh_session", "attempt": attempt, "status": listed["status"],
              "windows": len(listed["data"].get("windows", [])),
              "truncated": listed["data"].get("truncated"), "elapsed_seconds": listed["elapsed_seconds"]})
        if attempt == 1 and len(listed["data"].get("windows", [])) == 1:
            observer.enable_editable_controls()
            inspected = observer.inspect_window(listed["data"]["windows"][0]["window_id"])
            controls = inspected["data"].get("controls", [])
            emit({"production_inspection": inspected["status"], "controls": len(controls),
                  "types": dict(Counter(c["control_type"] for c in controls)),
                  "editable": sum(c.get("editable") is True for c in controls),
                  "truncated": inspected["data"].get("truncated"),
                  "elapsed_seconds": inspected["elapsed_seconds"]})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow read-only local Notepad UIA queries.")
    parser.add_argument("--trials", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.live:
        print("No desktop access. Add --live for the read-only Notepad diagnostic.")
        return 2
    if args._worker:
        child_probe()
        return 0
    report = isolated_probe()
    emit(report)
    discovery_trials(args.trials)
    completed = any(type(row) is dict and type(row.get("result")) is dict
                    and row["result"].get("status") == "ok" for row in report["records"])
    return 0 if completed and not report["probe_process"]["timeout"] and report["probe_process"]["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
