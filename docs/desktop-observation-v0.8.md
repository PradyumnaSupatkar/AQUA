# Aqua V0.8 Stage 2A — read-only desktop observation

Implemented and verified November 15, 2026. This stage adds scoped Windows UI
Automation discovery and control inspection. Stage 1 launches, ordinary chat,
voice-chat, hands-free, history, cancellations, continuation fixes and benchmark
artifacts are retained. No Stage 2B interaction was implemented.

## Tools and permission

`list_windows()` takes no model-supplied scope or process arguments. It identifies
up to eight visible top-level windows belonging to applications approved by the
original user request. Public results contain canonical application identity,
bounded caption, active-window snapshot and an opaque per-session `window_id`.
Native handles, PIDs, process paths and runtime identities remain private.

`inspect_window(window_id)` accepts only a syntactically valid, issued session
reference within fresh current-task scope. It returns a bounded flat tree with
control IDs, parent IDs, depth, standard control types, enabled/visible flags and
permitted accessible labels. Closed/recycled windows are rejected by rechecking
handle, PID, process creation time, executable/package identity and UIA runtime
ID. An existing valid reference can be inspected without another desktop scan.

Eligible applications come from the existing approved launcher registry:
Notepad, Calculator, VS Code and Chrome. Process identity uses fixed executable
paths, with OS-verified package-family identities for modern Windows Notepad and
Calculator. Titles and basenames cannot establish application identity.

Explicit task-local consent is separate from eligibility. **What can you see in
my VS Code window?** approves VS Code only; unrelated mentioned apps are not
included. Broad observation without an app asks for clarification before UIA.
Window-list questions approve metadata only; interface/control questions permit
the redacted tree. Mixed effect requests, unknown targets, sensitive-content
requests, negation, quotations and hypothetical instructions fail closed.
Permission is revoked at task completion and on a new utterance. References
survive only as bounded in-memory identities; `/clear` removes them and history.

## Native backend, privacy and limits

The optional backend uses [pywinauto 0.6.9](https://pypi.org/project/pywinauto/)
and the native UI Automation interface. The interactive process does not import
the UIA client. Each real query runs in a fixed hidden Python worker with MTA COM
initialization, consistent with [Microsoft's UIA threading guidance](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading).
The worker has only two read-only JSON operations and uses `shell=False`.
No model-generated executable, command, path or arbitrary Python is executed.

Discovery enumerates native top-level handles, applies visibility and application
identity checks first, then binds UIA only for the approved windows. Minimized,
cloaked/other-desktop, invalid and visibility-unknown windows are omitted. It does
not traverse the desktop accessibility tree or reveal unrelated processes.

Inspection incrementally walks structural controls. Password status is checked
before names; password or unverifiable-password subtrees are suppressed. Edit and
Text subtrees are not read. Document, Custom and container names are omitted;
structural descendants can still expose menus/buttons/tabs. No ValuePattern,
TextPattern, rich-text, screenshot or input APIs are used. Even a malformed worker
result cannot supply protected field names through the session sanitizer.

| Limit | Value |
| --- | ---: |
| Visible scoped windows per listing | 8 |
| Native top-level handles considered | 512 |
| Tree depth | 8 |
| Visited tree elements, including rejected nodes | 32 |
| Accessible label / window caption | 80 / 120 characters |
| Public result | 4,200 JSON characters |
| Grounded final report | 2,200 characters, plus a short task limitation if needed |
| Soft provider budget | 3.5 seconds |
| Worker hard deadline | 5 seconds, with bounded 0.5-second cleanup |
| Session reference cache | 64 identities |
| Agent limits, unchanged | 5 tool operations / 6 model decisions |

Limits and truncation are reported. Provider hangs cannot be cancelled by a
Python thread alone, so the parent kills/reaps only its own worker on timeout or
Ctrl+C. No target application is terminated. Discovery is cached within a task;
duplicate model reads are rejected. Revalidation remains mandatory for inspection.

No elevation or UIAccess setting is requested. Windows protects higher-integrity
interfaces; see [Microsoft's UIA security guidance](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-securityoverview).
Inaccessible or unsupported windows can yield unavailable, empty, stale, timeout
or bounded error results instead of an application interaction.

## Agent integration and factual reporting

Qwen independently chooses each native tool call. Discovery supplies references;
inspection supplies control evidence. Complete assistant-call/tool-result pairs
remain in the original user-turn bundle before the next decision. Every read
counts toward the existing limits, and the whole active task must fit the history
budget before another read. The historical launch schema and validator defaults
remain unchanged, preserving the frozen benchmark's tool manifest.

Application text remains explicitly untrusted tool data. It is never copied into
the trusted task ledger or treated as authorization. Observation tasks expose no
launch tool; an injected launch proposal is denied without arming confirmation.
Later tasks need fresh original-user consent, even for a valid old window ID.

To avoid fabricated UI claims, Qwen interprets relevance by selecting returned
window/control IDs in a small final JSON structure. Aqua renders those selections
using the actual properties. Unknown IDs or arbitrary descriptive claims are not
accepted as evidence; a bounded host report of real facts is the fallback. Excess
valid selections are bounded, preferring actual named controls. This deliberately
limits free-form UI narration while retaining model-driven observation choices.

A window title does not establish its controls. The controller tracks whether
the requested observation goal actually has evidence. One bounded model-driven
reconsideration is available; if controls remain uninspected, the task is marked
incomplete. The host never executes pending reads itself. Metadata-only questions
complete after discovery and cannot authorize control inspection.

## Dependencies installed with approval

| Package | Installed version |
| --- | --- |
| pywinauto | 0.6.9 |
| comtypes | 1.5.0 |
| pywin32 | 312 |
| six | 1.17.0 |

Wheels totaled approximately **7.6 MB**. Installed package-file inventory is
approximately **24.8 MiB**, excluding pip/generated caches. No inference models,
CUDA, PyTorch or agent framework was installed. The `desktop` extra is optional
and Windows-only. Aqua never installs a missing dependency at runtime.

## Automated verification

**863 tests passed**, retaining all prior 760 tests. `pip check` reported no broken
requirements. New deterministic tests cover discovery, scopes, package/path
identity, stale references, privacy getters/redaction, bounded trees/results,
timeouts and worker cleanup, untrusted UI text, strict schemas, truthful evidence,
goal completeness, cancellation, cached references, full tool history and both
agent caps. Five reads across four windows are covered at exactly six model
decisions. Generic Windows/UI knowledge questions remain normal conversation.

The offline runner blocks real sockets, DNS and process launches throughout
discovery/execution. Tests require neither Windows UI access nor installed models,
microphones, network, or real application manipulation. Provider/process results
are mocked; the explicitly invoked live helper is separate.

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern test_observation*.py
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern test_desktop*.py
.\.venv\Scripts\python.exe -B tools\test_offline.py
.\.venv\Scripts\python.exe -m pip check
```

## Actual read-only demonstration

The already-open VS Code window was used; no application was launched, focused,
clicked or typed into. Actual local Qwen Instruct at temperature 0.2 chose:

1. `list_windows()` — one approved visible VS Code window.
2. `inspect_window(issued_window_id)` — 15 bounded control records.
3. Evidence selection — the host rendered real UIA properties.

Final live helper result:

| Measurement | Observed |
| --- | ---: |
| Discovery, including worker overhead | 0.616 seconds |
| Inspection, including worker overhead | 0.619 seconds |
| Entire task, including three Qwen decisions | 9.507 seconds |
| Tool operations / model decisions | 2 / 3 |

The snapshot contained a Window, Panes and enabled visible **Minimize**,
**Maximize** and **Close** buttons. It was marked truncated. A prior provider-only
probe returned 14 controls without truncation, illustrating snapshot variability.
The bounded observations did not supply a full editor/menu interface or source
code text. The active-window flag was sampled rather than inferred.

Earlier development probes sometimes stopped after discovery, including after
reconsideration. That exposed and corrected a completion-status bug: interface
questions can no longer be marked complete solely because a title was listed.
The final helper passed, but these few development probes are **not a completion
rate or reliability benchmark**. Qwen can still stop early; this is reported
honestly and no automatic inspection follows. No real UI snapshot or conversation
was saved by the helper; only this bounded report records generic observations.

Repeat only with an already-open approved app and explicit scope:

```powershell
.\.venv\Scripts\python.exe -B tools\smoke_observation.py --live --app "vs code"
```

## Chat and hands-free commands

```powershell
.\.venv\Scripts\python.exe -m aqua chat --temperature 0.2 --diagnostics
# You: What can you see in my VS Code window?
# You: List my VS Code windows.
.\.venv\Scripts\python.exe -m aqua handsfree --language en --hints --temperature 0.2 --diagnostics
# Say: Hey Aqua, what can you see in my VS Code window?
```

Manual voice uses the same controller:

```powershell
.\.venv\Scripts\python.exe -m aqua voice-chat --language en --temperature 0.2 --diagnostics
```

On another Windows installation, install the optional extra only with dependency
approval: `python -m pip install -e ".[desktop]"`. No model download is involved.

## Known limitations

- UIA coverage varies with application accessibility support, privileges and the
  configured bounds. The tested VS Code exposed primarily window chrome.
- Password/input values and document/editor body text are intentionally omitted.
  Approved captions, tabs and structural labels can still contain filenames or
  other application-provided text; there is no general secret-content classifier.
- Scope consent and explicit refusal checks are conservative English heuristics.
  Unfamiliar phrasing can require a clearer full request. Old IDs are not consent.
- Qwen may stop early, choose an invalid reference, or return malformed evidence
  selection. The host rejects/falls back rather than inventing observations.
- Results are bounded snapshots, not continuous monitoring or guaranteed current
  readiness. Cached references are revalidated and may become stale or be evicted.
- Observation results remain in bounded process-memory history until pruning or
  `/clear`. They are not written to a database, uploaded, or sent to cloud AI.
- No fresh live microphone capture was needed; existing text/voice/hands-free
  regressions passed. No Stage 2B interaction, vision, TTS or project/test tools
  were added.

## Files changed

- `src/aqua/desktop/__init__.py`
- `src/aqua/desktop/authorization.py`
- `src/aqua/desktop/backend.py`
- `src/aqua/desktop/session.py`
- `src/aqua/desktop/worker.py`
- `src/aqua/desktop/reporting.py`
- `src/aqua/actions/tools.py`
- `src/aqua/brain/controller.py`
- `src/aqua/brain/conversation.py`
- `src/aqua/brain/task.py`
- `pyproject.toml`
- `README.md`
- `docs/desktop-observation-v0.8.md`
- `tools/smoke_observation.py`
- `tests/test_desktop_backend.py`
- `tests/test_desktop_session.py`
- `tests/test_observation_authorization.py`
- `tests/test_observation_agent.py`
- `tests/test_observation_registry.py`
- `tests/test_observation_smoke.py`
