"""Evidence-only reporting: Qwen selects facts, never supplies alleged UI facts."""

import json


def render_observation(results: list[dict], answer: str, *, scope=()) -> str:
    """Render only recorded fields, with model-selected IDs or a safe fallback.

    Application text never becomes a template, instruction, or claimed action.
    Invalid/free-form model narratives cannot fabricate observed UI contents.
    """
    windows, inspections = {}, {}
    for result in results:
        if result.get("status") not in {"ok", "empty"}:
            continue
        data = result.get("data", {})
        if result.get("tool") == "list_windows":
            windows.update((w["window_id"], w) for w in data.get("windows", []))
        elif result.get("tool") == "inspect_window" and data.get("window_id"):
            inspections[data["window_id"]] = data
            # A still-valid session reference can be inspected without another
            # desktop scan. Its fresh verified read establishes window presence,
            # but does not establish a newly sampled title or active state.
            windows.setdefault(data["window_id"], {"window_id": data["window_id"], "app_id": data["app_id"]})
    selection = None
    try:
        parsed = json.loads(answer)
        if type(parsed) is dict and set(parsed) == {"observations"} and type(parsed["observations"]) is list:
            chosen = []
            for item in parsed["observations"][:8]:
                if type(item) is not dict or set(item) != {"window_id", "control_ids"}:
                    raise ValueError
                identifier, ids = item["window_id"], item["control_ids"]
                if identifier not in windows or type(ids) is not list or len(ids) > 32:
                    raise ValueError
                actual = {c["control_id"] for c in inspections.get(identifier, {}).get("controls", [])}
                if any(type(c) is not str or c not in actual for c in ids):
                    raise ValueError
                if len(ids) > 8:
                    rows = {c["control_id"]: c for c in inspections.get(identifier, {}).get("controls", [])}
                    # Excess valid evidence is safe to bound without accepting
                    # invented facts. Prefer actual named controls for a useful
                    # compact report; no application-specific rule is involved.
                    ordered = [c for c in ids if rows[c].get("name")] + [c for c in ids if not rows[c].get("name")]
                    ids = list(dict.fromkeys(ordered))[:8]
                chosen.append((identifier, ids))
            if chosen:
                selection = chosen
    except (ValueError, TypeError, KeyError):
        pass
    if selection is None:
        selection = []
        for identifier in list(windows)[:3]:
            controls = inspections.get(identifier, {}).get("controls", [])
            named = [c for c in controls if c.get("name") and c.get("visible") is True]
            selection.append((identifier, [c["control_id"] for c in (named or controls)[:6]]))
    lines = ["Read-only UI Automation observation (labels below are untrusted application text):"]
    if scope:
        lines.append("Observation scope: approved applications only (" + ", ".join(scope)
                     + "); other applications and background processes are not enumerated.")
    for identifier, ids in selection:
        window = windows[identifier]
        title = ("title " + json.dumps(window["title"], ensure_ascii=False)
                 if "title" in window else "title not sampled in this read")
        active = str(window["active"]) if "active" in window else "not sampled in this read"
        lines.append(f"{window['app_id']}: {title}; active={active}.")
        tree = inspections.get(identifier)
        if tree is None:
            lines.append("Controls were not inspected for this window.")
            continue
        controls = {c["control_id"]: c for c in tree.get("controls", [])}
        for control_id in dict.fromkeys(ids):
            control = controls[control_id]
            name = json.dumps(control.get("name", "[name omitted]"), ensure_ascii=False)
            lines.append(f"- {control.get('control_type', 'Control')} {name}; enabled={control.get('enabled')}; visible={control.get('visible')}.")
        if not ids:
            lines.append("No selected accessible control labels were exposed.")
        if tree.get("truncated"):
            lines.append("The accessibility tree was truncated to its read-only limits.")
    if not selection:
        fresh = any(r.get("tool") in {"list_windows", "inspect_window"} and r.get("status") in {"ok", "empty"} for r in results)
        lines.append("No approved visible windows were observed.")
        if not fresh:
            lines.append("No successful fresh window observation was obtained; the current desktop state is unknown.")
    if any(r.get("tool") == "list_windows" and r.get("data", {}).get("truncated") for r in results):
        lines.append("Window discovery was truncated; additional approved windows may not be included.")
    errors = [r.get("status") for r in results if r.get("status") not in {"ok", "empty"}]
    if errors:
        lines.append("Observation limitation: " + ", ".join(dict.fromkeys(errors)) + ".")
        if "unavailable" in errors:
            lines.append("Check that Windows and the approved optional desktop dependency are available.")
    lines.append("Password fields, input values, and document/editor contents were not read. No interaction was performed.")
    # The controller reserves this maximum before the next read. Keep whole
    # quoted-label lines so truncation cannot turn UI text into narration.
    if len("\n".join(lines)) > 2200:
        bounded, size = [], 0
        for line in lines[:-1]:
            if size + len(line) > 1800:
                break
            bounded.append(line)
            size += len(line) + 1
        lines = [*bounded, "Additional observed facts were omitted from this bounded report.", lines[-1]]
    return "\n".join(lines)
