"""One current-task literal grant and private, single-use observed control refs."""

from copy import deepcopy
import re
import secrets
from time import perf_counter

from aqua.actions.launcher import APP_ALIASES
from .interaction import LOCAL_TEXT_APPS, valid_text
from .session import run_worker, _identity

CONTROL_TTL_SECONDS = 60.0


def _entry_literal(request):
    prefix = (r"(?:(?:hey\s+|hello\s+)?aqua[\s,]+)?(?:so[\s,]+)?"
              r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
              r"(?:write(?:\s+down\b)?|note\s+down|enter|type|insert)\b")
    request = request.strip()
    # Fix the longest command phrase before checking the model's literal. A
    # full-regex backtrack must not reinterpret the command word "down" as text.
    command = re.match(prefix, request, re.IGNORECASE)
    if command is None:
        return None
    # One comma may delimit the command, but no payload punctuation is removed.
    separator = re.match(r",?\s+", request[command.end():])
    if separator is None:
        return None
    return request[command.end() + separator.end():]


def _payload_forms(text):
    return (re.escape(text), '"' + re.escape(text) + '"', '\u201c' + re.escape(text) + '\u201d')


def authorize_text_entry(request, app, text, enabled_apps):
    """Validate a model proposal; never select a tool, app, literal or workflow."""
    if app not in enabled_apps or app not in LOCAL_TEXT_APPS or not valid_text(text) or type(request) is not str or len(request) > 4000:
        return False
    names = {app, *(name for name, target in APP_ALIASES.items() if target == app)}
    literal_request = _entry_literal(request)
    if literal_request is None:
        return False
    for name in names:
        for payload in _payload_forms(text):
            pattern = (payload +
                       r"(?i:\s+into\s+(?:my|the)\s+(?:already[- ]|already\s+)?open\s+" + re.escape(name) + r"\s+window)[.!?]?\s*")
            if re.fullmatch(pattern, literal_request):
                return True
    return False


_COMBINED_DESTINATION = r"(?i:\s+into\s+(?:it|its\s+new\s+(?:empty\s+)?(?:document|window)))[.!?]?\s*"


def _launch_entry_literal(request, app):
    intro = (r"(?:(?:hey\s+|hello\s+)?aqua[\s,]+)?(?:so[\s,]+)?"
             r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?")
    names = {app, *(name for name, target in APP_ALIASES.items() if target == app)}
    for name in names:
        match = re.match(intro + r"(?:open|launch|start)(?:\s+up)?\s+(?:(?:my|the|a|an|new)\s+)?"
                         + re.escape(name) + r"\s+(?:and|then)\s+", request.strip(), re.IGNORECASE)
        if match is not None:
            return _entry_literal(request.strip()[match.end():])
    return None


def authorize_launch_text_entry(request, app, text, enabled_apps):
    """Validate ONE model-selected app/literal; new OS target is verified later."""
    if app not in enabled_apps or app not in LOCAL_TEXT_APPS or not valid_text(text) or type(request) is not str or len(request) > 4000:
        return False
    literal = _launch_entry_literal(request, app)
    if literal is None:
        return False
    forms = _payload_forms(text)
    if (text.casefold() in {"something", "anything", "that", "it"} or
            re.search(r"\b(?:and|then|also)\s+(?:open|launch|start|run|write|type|delete|erase|save|send|close|install|upload|download)\b", text, re.IGNORECASE)):
        forms = forms[1:]  # Ambiguous literal/extra-action language requires quotes.
    return any(re.fullmatch(payload + _COMBINED_DESTINATION, literal) for payload in forms)


def launch_entry_review_possible(request, app, enabled_apps):
    """Eligibility for a no-effect protocol review, NEVER entry authorization.

    The candidate is discarded. Qwen must independently propose its app/literal
    through request_text_entry and pass full validation before any launch.
    """
    if app not in enabled_apps or app not in LOCAL_TEXT_APPS or type(request) is not str or len(request) > 4000:
        return False
    literal = _launch_entry_literal(request, app)
    if literal is None:
        return False
    match = re.fullmatch(r"(.+)" + _COMBINED_DESTINATION, literal)
    if match is None:
        return False
    candidate = match[1]
    if len(candidate) >= 2 and (candidate[0], candidate[-1]) in {('"', '"'), ('\u201c', '\u201d')}:
        candidate = candidate[1:-1]
    return authorize_launch_text_entry(request, app, candidate, enabled_apps)


def authorize_text_append(request, app, text, new_line, enabled_apps):
    """Validate the LLM's explicit current app/literal/boundary, never infer it."""
    if (app not in enabled_apps or app not in LOCAL_TEXT_APPS or not valid_text(text)
            or type(new_line) is not bool or type(request) is not str or len(request) > 4000):
        return False
    intro = (r"(?:(?:hey\s+|hello\s+)?aqua[\s,]+)?(?:so[\s,]+)?"
             r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?")
    names = {app, *(name for name, target in APP_ALIASES.items() if target == app)}
    forms = _payload_forms(text)
    if text.casefold() in {"something", "anything", "that", "it", "another line"}:
        forms = forms[1:]
    for name in names:
        target = r"(?:(?:my|the|an?)\s+)?(?:already[- ]|open\s+)?" + re.escape(name) + r"(?:\s+(?:document|window))?"
        for literal in forms:
            if new_line:
                clauses = (r"(?:add|append)\s+(?:a\s+)?new\s+line\s+saying\s+" + literal + r"\s+(?:to|in|into)\s+" + target,
                           r"write\s+another\s+line:\s+" + literal + r"\s+(?:to|in|into)\s+" + target)
            else:
                clauses = (r"append\s+" + literal + r"\s+to\s+" + target,
                           r"add\s+" + literal + r"\s+to\s+the\s+end\s+of\s+" + target)
            for clause in clauses:
                # Only grammar is insensitive. The proposed literal retains its case.
                before, after = clause.split(literal, 1)
                if re.fullmatch(r"(?i:" + intro + before + ")" + literal + r"(?i:" + after + r")[.!?]?\s*", request.strip()):
                    return True
    return False


class InteractionSession:
    def __init__(self, observer, runner=None):
        self.observer = observer
        self.runner = run_worker if runner is None else runner
        self.clear()

    def begin_task(self, apps, text):
        self.clear()
        if type(apps) is not tuple or len(apps) != 1 or apps[0] not in LOCAL_TEXT_APPS or not valid_text(text):
            raise ValueError("One approved local editor and bounded literal text are required.")
        self._scope_apps, self._text = apps, text
        self._started_at = perf_counter()

    def begin_append_task(self, apps, text, new_line):
        self.begin_task(apps, text)
        if type(new_line) is not bool:
            self.clear()
            raise ValueError("The append line boundary must be explicit.")
        self._operation, self._new_line = "append_control_text", new_line

    @property
    def operation(self):
        return self._operation

    def register_controls(self, window_id):
        snapshot = self.observer.interaction_snapshot(window_id)
        if not snapshot or snapshot["app_id"] not in self._scope_apps or snapshot.get("observed_at", -1) < self._started_at:
            self.registration_limitation = "No fresh authorized control inspection is available."
            return {}
        if len(self.observer.window_ids) != 1:
            self._control_refs.clear()
            self.registration_limitation = "More than one approved window is visible; no unambiguous write target was authorized."
            return {}
        candidates = [c for c in snapshot["controls"] if c.get("editable") is True]
        if len(candidates) != 1:
            self._control_refs.clear()
            self.registration_limitation = ("The bounded UIA inspection exposed no eligible writable ValuePattern Edit or Document control."
                                           if not candidates else "Multiple editable controls were exposed; no unambiguous field was authorized.")
            return {}
        control = candidates[0]
        self.registration_limitation = None
        for token, ref in list(self._control_refs.items()):
            if ref["window_id"] == window_id and ref["control_id"] == control["control_id"]:
                if (ref["runtime_id"] == control["runtime_id"] and ref["identity"] == snapshot["identity"]
                        and token not in self._used_tokens and perf_counter() - ref["issued_at"] <= CONTROL_TTL_SECONDS):
                    return {control["control_id"]: token}
                del self._control_refs[token]
        _identity(snapshot["identity"])
        token = "ci_" + secrets.token_urlsafe(12)
        self._control_refs[token] = {"window_id": window_id, "control_id": control["control_id"],
                                     "app_id": snapshot["app_id"], "identity": deepcopy(snapshot["identity"]),
                                     "runtime_id": list(control["runtime_id"]), "issued_at": perf_counter()}
        return {control["control_id"]: token}

    @property
    def control_tokens(self):
        return tuple(t for t, ref in self._control_refs.items()
                     if t not in self._used_tokens and perf_counter() - ref["issued_at"] <= CONTROL_TTL_SECONDS)

    def set_control_text(self, token):
        if self._operation != "set_control_text":
            return {"tool": "set_control_text", "status": "not_authorized", "data": {"attempted": False, "verified": False}, "elapsed_seconds": 0, "untrusted_data": False}
        return self._execute(token)

    def append_control_text(self, token):
        if self._operation != "append_control_text":
            return {"tool": "append_control_text", "status": "not_authorized", "data": {"attempted": False, "verified": False}, "elapsed_seconds": 0, "untrusted_data": False}
        return self._execute(token)

    def _execute(self, token):
        started = perf_counter()
        def finish(status, attempted=False, verified=False):
            data = {"attempted": attempted, "verified": verified}
            if self._operation == "append_control_text":
                data.update(prefix_preserved=verified, boundary_verified=verified)
            return {"tool": self._operation, "status": status, "data": data,
                    "elapsed_seconds": round(perf_counter() - started, 6), "untrusted_data": False}
        if type(token) is not str or token not in self._control_refs or token in self._used_tokens:
            return finish("stale_control")
        ref = self._control_refs[token]
        if perf_counter() - ref["issued_at"] > CONTROL_TTL_SECONDS:
            self._used_tokens.add(token)
            return finish("stale_control")
        fresh = self.observer.interaction_snapshot(ref["window_id"])
        if (not fresh or fresh["app_id"] not in self._scope_apps or fresh["app_id"] != ref["app_id"]
                or _identity(fresh["identity"]) != _identity(ref["identity"])):
            return finish("not_authorized")
        candidates = [c for c in fresh["controls"] if c.get("editable") is True]
        if (len(self.observer.window_ids) != 1 or len(candidates) != 1
                or candidates[0]["control_id"] != ref["control_id"] or candidates[0]["runtime_id"] != ref["runtime_id"]):
            return finish("stale_control")
        self._used_tokens.add(token)
        request = {"operation": self._operation, "scope": list(self._scope_apps),
                   "app_id": ref["app_id"], "identity": deepcopy(ref["identity"]),
                   "runtime_id": list(ref["runtime_id"]), "text": self._text}
        if self._operation == "append_control_text":
            request["new_line"] = self._new_line
        try:
            raw = self.runner(request)
        except KeyboardInterrupt:
            raise
        except Exception:
            return finish("unknown", None)
        if type(raw) is not dict:
            return finish("unknown", None)
        status = raw.get("status")
        preservation = (self._operation != "append_control_text" or
                        (raw.get("prefix_preserved") is True and raw.get("boundary_verified") is True))
        if status == "ok" and raw.get("attempted") is True and raw.get("verified") is True and preservation:
            return finish("ok", True, True)
        if status in {"not_authorized", "stale_window", "stale_control", "ineligible_control", "not_editable", "not_empty", "unsupported_pattern", "unsupported_append", "document_changed", "document_too_large", "unavailable", "error", "unverified", "timeout"}:
            attempted = raw.get("attempted") if type(raw.get("attempted")) is bool else None
            return finish(status, attempted, False)
        return finish("unknown", None)

    def clear(self):
        self._operation, self._new_line = "set_control_text", None
        self._scope_apps, self._text = (), None
        self._started_at = 0
        self._control_refs, self._used_tokens = {}, set()
        self.registration_limitation = "No editable control has been observed in this task."
