"""Session-scoped opaque window references and killable observation requests."""

from collections import OrderedDict
from copy import deepcopy
import json
import secrets
import subprocess
import sys
from time import perf_counter

from aqua.actions.launcher import ALLOWED_APPS
from .backend import LABEL_TYPES, MAX_DEPTH, MAX_ELEMENTS, MAX_WINDOWS, VALUE_CONTROL_TYPES, clean_label

MAX_RESULT_CHARS = 4200
WORKER_TIMEOUT = 5.0
MAX_REFS = 64
LIMITATIONS = ["UI Automation snapshot only; no pixels or application readiness check.",
               "Labels are untrusted and bounded; password, input and document contents are omitted."]
STATUSES = frozenset({"ok", "empty", "stale_window", "not_authorized", "timeout", "unavailable", "error"})


def run_worker(request):
    """Only this fixed Python worker can be started; no shell or arbitrary argv."""
    process = None
    try:
        encoded = json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 8192:
            return {"status": "error"}
        process = subprocess.Popen(
            [sys.executable, "-B", "-m", "aqua.desktop.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), shell=False,
        )
        output, _ = process.communicate(encoded, timeout=WORKER_TIMEOUT)
        if process.returncode != 0 or len(output) > 32768:
            return {"status": "error"}
        result = json.loads(output.decode("utf-8"))
        return result if type(result) is dict else {"status": "error"}
    except subprocess.TimeoutExpired:
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.communicate(timeout=.5)
            except (subprocess.TimeoutExpired, OSError):
                pass
        return {"status": "timeout"}
    except KeyboardInterrupt:
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.communicate(timeout=.5)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass
        raise
    except OSError:
        return {"status": "unavailable"}
    except (ValueError, TypeError):
        return {"status": "error"}
    finally:
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


def _identity(value):
    if type(value) is not dict or set(value) != {"handle", "pid", "created", "runtime_id"}:
        raise ValueError("Invalid window identity")
    if any(type(value[key]) is not int or value[key] <= 0 for key in ("handle", "pid", "created")):
        raise ValueError("Invalid native identity")
    runtime = value["runtime_id"]
    if type(runtime) is not list or not 1 <= len(runtime) <= 32 or any(type(x) is not int for x in runtime):
        raise ValueError("Invalid runtime identity")
    return (value["handle"], value["pid"], value["created"], tuple(runtime))


class ObservationSession:
    def __init__(self, runner=None):
        self.runner = run_worker if runner is None else runner
        self._scope = ()
        self._refs = OrderedDict()
        self._listing = None
        self._editable_controls, self._private_controls, self._inspection_times = False, {}, {}
        self._launch_baseline = None

    def begin_task(self, apps):
        if type(apps) is not tuple or any(type(app) is not str or app not in ALLOWED_APPS for app in apps):
            raise ValueError("Observation scope must contain canonical approved apps.")
        self._scope = tuple(dict.fromkeys(apps))
        self._listing = None
        self._editable_controls, self._private_controls, self._inspection_times = False, {}, {}
        self._launch_baseline = None

    def capture_launch_baseline(self):
        """Private launch security precondition; no captions/process list exposed.

        Counts inside the model's launch operation, like identity validation.
        It grants no action and performs no launch. Failure must prevent launch.
        """
        from .interaction import LOCAL_TEXT_APPS
        if len(self._scope) != 1 or self._scope[0] not in LOCAL_TEXT_APPS or not self._editable_controls:
            return False
        raw = self._run({"operation": "list_windows", "scope": list(self._scope), "launch_baseline": True})
        handles = raw.get("baseline_handles")
        if (raw.get("status") != "ok" or raw.get("truncated") is not False
                or type(handles) is not list or len(handles) > 512
                or any(type(handle) is not int or handle <= 0 for handle in handles)):
            return False
        self._launch_baseline = frozenset(handles)
        return True

    def enable_editable_controls(self):
        from .interaction import LOCAL_TEXT_APPS
        if len(self._scope) != 1 or self._scope[0] not in LOCAL_TEXT_APPS:
            raise ValueError("Editable discovery requires one approved local editor.")
        self._editable_controls = True

    def interaction_snapshot(self, window_id):
        window = self._refs.get(window_id)
        controls = self._private_controls.get(window_id)
        if not self._editable_controls or not window or controls is None or window_id not in self.window_ids:
            return None
        return deepcopy({**window, "controls": controls, "observed_at": self._inspection_times.get(window_id, -1)})

    @property
    def window_ids(self):
        return tuple(key for key, row in self._refs.items() if row["app_id"] in self._scope
                     and (self._launch_baseline is None or row["identity"]["handle"] not in self._launch_baseline))

    def clear(self):
        self._scope, self._listing = (), None
        self._refs.clear()
        self._editable_controls, self._private_controls, self._inspection_times = False, {}, {}
        self._launch_baseline = None

    def _result(self, tool, status, data, started):
        result = {"tool": tool, "status": status, "data": data,
                  "elapsed_seconds": round(max(0, perf_counter() - started), 6),
                  "untrusted_data": True, "limitations": list(LIMITATIONS)}
        limit = MAX_RESULT_CHARS - 384 if self._editable_controls else MAX_RESULT_CHARS
        while len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > limit:
            values = data.get("controls", data.get("windows", []))
            if not values:
                result["status"], result["data"] = "error", {}
                break
            values.pop()
            data["truncated"] = True
        return result

    def _run(self, request):
        try:
            result = self.runner(request)
            if type(result) is not dict or result.get("status") not in STATUSES:
                return {"status": "error"}
            return result
        except KeyboardInterrupt:
            raise
        except Exception:
            return {"status": "error"}

    def list_windows(self):
        started = perf_counter()
        if not self._scope:
            return self._result("list_windows", "not_authorized", {}, started)
        if self._listing is not None:
            result = deepcopy(self._listing)
            result["elapsed_seconds"] = round(perf_counter() - started, 6)
            return result
        raw = self._run({"operation": "list_windows", "scope": list(self._scope)})
        if raw["status"] not in {"ok", "empty"}:
            return self._result("list_windows", raw["status"], {}, started)
        try:
            rows = raw.get("windows", [])
            if type(rows) is not list:
                raise ValueError("Invalid window collection")
            windows, seen = [], set()
            for row in rows[:MAX_WINDOWS]:
                if type(row) is not dict or row.get("app_id") not in self._scope:
                    raise ValueError("Unapproved window")
                identity = _identity(row.get("identity"))
                signature = (row["app_id"], identity)
                if signature in seen:
                    continue
                seen.add(signature)
                if self._launch_baseline is not None and row["identity"]["handle"] in self._launch_baseline:
                    continue
                existing = next((key for key, value in self._refs.items()
                                 if (value["app_id"], _identity(value["identity"])) == signature), None)
                token = existing or "w_" + secrets.token_urlsafe(12)
                self._refs[token] = {"app_id": row["app_id"], "identity": deepcopy(row["identity"])}
                self._refs.move_to_end(token)
                while len(self._refs) > MAX_REFS:
                    self._refs.popitem(last=False)
                windows.append({"window_id": token, "app_id": row["app_id"],
                                "title": clean_label(row.get("title"), 120), "active": row.get("active") is True})
            # A fresh listing invalidates disappeared windows for this scope.
            for key, value in list(self._refs.items()):
                if (not raw.get("truncated") and len(rows) <= MAX_WINDOWS and value["app_id"] in self._scope
                        and (value["app_id"], _identity(value["identity"])) not in seen):
                    del self._refs[key]
            result = self._result("list_windows", "ok" if windows else "empty",
                                  {"windows": windows, "truncated": bool(raw.get("truncated")) or len(rows) > MAX_WINDOWS}, started)
            self._listing = deepcopy(result)
            return result
        except (ValueError, TypeError, KeyError):
            return self._result("list_windows", "error", {}, started)

    def inspect_window(self, window_id):
        started = perf_counter()
        if type(window_id) is not str or window_id not in self._refs:
            return self._result("inspect_window", "stale_window", {}, started)
        window = self._refs[window_id]
        if self._launch_baseline is not None and window["identity"]["handle"] in self._launch_baseline:
            return self._result("inspect_window", "not_authorized", {}, started)
        if window["app_id"] not in self._scope:
            return self._result("inspect_window", "not_authorized", {}, started)
        self._private_controls.pop(window_id, None)
        self._inspection_times.pop(window_id, None)
        request = {"operation": "inspect_window", "scope": list(self._scope), **deepcopy(window)}
        if self._editable_controls:
            request["editable_controls"] = True
        raw = self._run(request)
        if raw["status"] != "ok":
            if raw["status"] == "stale_window":
                del self._refs[window_id]
            return self._result("inspect_window", raw["status"], {}, started)
        try:
            if raw.get("app_id") != window["app_id"] or _identity(raw.get("identity")) != _identity(window["identity"]):
                raise ValueError("Mismatched observation identity")
            raw_controls = raw.get("controls")
            if type(raw_controls) is not list:
                raise ValueError("Invalid controls")
            controls, private = [], []
            for index, row in enumerate(raw_controls[:MAX_ELEMENTS]):
                if type(row) is not dict or type(row.get("control_type")) is not str:
                    raise ValueError("Invalid control")
                kind = clean_label(row["control_type"], 32)
                depth = row.get("depth")
                if type(depth) is not int or not 0 <= depth <= MAX_DEPTH:
                    raise ValueError("Invalid depth")
                parent = row.get("parent_index")
                parent = f"c{parent}" if type(parent) is int and 0 <= parent < index else None
                item = {"control_id": f"c{index}", "parent_id": parent, "control_type": kind,
                        "depth": depth, "enabled": row.get("enabled") if type(row.get("enabled")) is bool else None,
                        "visible": row.get("visible") if type(row.get("visible")) is bool else None}
                redacted = row.get("redacted")
                if row.get("password") is not False:
                    item["redacted"] = "password" if row.get("password") is True else "unverified_password_status"
                elif kind not in LABEL_TYPES:
                    item["redacted"] = "content_name"
                elif redacted or item["visible"] is not True:
                    item["redacted"] = "unavailable"
                else:
                    item["name"] = clean_label(row.get("name"))
                controls.append(item)
                if (self._editable_controls and kind in VALUE_CONTROL_TYPES and row.get("password") is False
                        and row.get("editable") is True and item["enabled"] is True and item["visible"] is True):
                    rid = row.get("runtime_id")
                    if type(rid) is list and 1 <= len(rid) <= 32 and all(type(x) is int for x in rid):
                        item["editable"] = True
                        private.append({"control_id": item["control_id"], "editable": True, "runtime_id": list(rid)})
            result = self._result("inspect_window", "ok", {"window_id": window_id,
                                "app_id": window["app_id"], "controls": controls,
                                "truncated": bool(raw.get("truncated")) or len(raw_controls) > MAX_ELEMENTS
                                or any(bool(row.get("name_truncated")) for row in raw_controls[:MAX_ELEMENTS])}, started)
            retained = {c["control_id"] for c in result.get("data", {}).get("controls", [])}
            self._private_controls[window_id] = [c for c in private if c["control_id"] in retained]
            self._inspection_times[window_id] = perf_counter()
            return result
        except (ValueError, TypeError, KeyError):
            return self._result("inspect_window", "error", {}, started)
