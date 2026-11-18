# Modern Notepad text-entry diagnosis

November 15, 2026. Scope: Stage 2B.1 only. No real desktop input, focus change,
application launch, model request/download or dependency installation occurred.
Only bounded read-only UIA/native queries ran against the user's open Notepad.

## Verified cause and targeted correction

The live production inspector returned a **Document at depth 2**, but zero
editable candidates. Three independent type gates accepted only `Edit`:
backend discovery, the observation session's private-reference filter, and the
interaction worker's execution gate. The controller prompt/tool description
also described only Edit controls. Consequently, the real Document could not
receive a `ci_*` reference even when it had the required ValuePattern. It was
present in the bounded model-visible tree; truncation did not remove this editor.

Fresh read-only probes contradict the earlier claim that ValuePattern was
unavailable on this editor. Using installed pywinauto 0.6.9's
`get_elem_interface(element, "Value")` (pattern 10002 plus QueryInterface), the
current Document consistently exposed a ValuePattern with
`CurrentIsReadOnly=False`. Both Value and Text independently reported empty.
The original probe source/state is unavailable, so the earlier absence cannot
be explained conclusively. No assumption was made that all Notepad versions or
states provide the same interface.

The minimum correction shares an `Edit`/`Document` type allowlist across those
three gates and updates the model's descriptions. A Document **still must expose
the real writable ValuePattern**. TextPattern is not a writing API and is not
used by the production writer. No keyboard, focus, clipboard, invocation,
coordinate or message-injection fallback was added.

## Actual read-only observations

The user's single approved Notepad editor exposed:

| Property | Observed |
| --- | --- |
| UIA control type/depth | Document / 2 |
| Enabled / onscreen / password | true / true / false |
| Keyboard focusable / currently focused | true / false |
| Private runtime identity available | true; never printed or sent to the model |
| Available installed pattern interfaces | Value, Text, TextV2, TextEdit, Scroll, LegacyIAccessible |
| Value read-only flag | false |
| Text empty-state check | empty; collapsed DocumentRange and GetText(1) == empty string |
| Value empty-state check | empty; checked only after bounded Text check reported empty |
| Production inspection before correction | 27 returned controls; Document present; 0 eligible controls |
| Production inspection after correction | 27 returned controls; Document present; 1 eligible control |

The diagnostic visited 38 nodes with no truncation. It prunes document/input/text
subtrees and prints no accessible names or document contents; it is not a
full-document extractor. Production inspection kept its existing 32-visited-node,
depth-8 and response-size limits and reported truncation. Its private runtime
binding now permits the normal authorized task to register the Document as a
short-lived opaque `ci_*` reference. The deterministic controller regression
verifies registration, model selection of that reference and paired tool history.
No live registration was used to dispatch a write.

Measured elapsed times (not promises of future latency):

| Check | Time / outcome |
| --- | --- |
| Initial native probe before user opened Notepad | 0.230s total; zero eligible native matches |
| Fresh worker discovery after user opened Notepad | 3/3 ok; 0.585, 0.549, 0.527s |
| Fresh session discovery in those same trials | 3/3 ok; 0.504, 0.535, 0.524s |
| Detailed UIA probe, three successful samples | 0.395, 0.430, 0.537s internal; 0.659, 0.702, 0.907s including child startup |
| Production inspection, before/after correction | 0.616s / 0.751s; after sample overlapped the full regression run |

## Discovery and warning-only uncertainty

The initial current sample found zero eligible native windows; after the user
opened one, all three direct-worker and three fresh-session trials found it.
This does **not** establish the cause of the earlier intermittent full-agent
`list_windows: empty`. No failing full-agent trace was reproduced or supplied.
The current controller resets the task listing at begin_task; within-task
listing caching is intentional. Backend UIA binding failures can be skipped,
so an `empty` status alone cannot distinguish every provider/access failure
from a genuinely empty eligible set. No discovery workaround or silent retry
was introduced based on speculation.

The new probes produced complete JSON despite a pywinauto initialization
warning. The installed source explicitly warns when using Aqua's external MTA
`coinit_flags`. A warning alone is not evidence that TextPattern is unsupported.
The cause of the old warning-only diagnostic remains unknown without its
source/exit status. The new helper reports progress stages, return code,
timeout, stdout/stderr byte counts and warning presence. It reports exception
categories without provider messages, and returns failure for missing results.

## Safety and verification

Current-task literal consent and CLI opt-in remain required. App scope is still
Notepad only. The worker independently revalidates native app/window/process
identity, integrity, runtime identity, password state, visibility and enabled
state. Writable ValuePattern and exact empty-string value are required; blankness
is rechecked immediately before SetValue. Nonempty/whitespace fields fail closed.
Readback and post-write identity checks must match before success. References
are private, one-use, task-bound and expiring. Timeout/cancellation after a
possible effect remains unknown, without retry. Agent limits remain 5 operations
and 6 decisions. Default Stage 2A observation still reads no document values.

Focused tests passed: 43 text-entry, 12 diagnostic, 27 desktop backend/session
tests. Full offline regression: **918 passed**, zero failures/errors.
`pip check`: **No broken requirements found.** The offline runner blocks real
sockets and subprocess launches; mock objects exercise the UIA writer.

The 18 added regressions cover Document discovery/entry, absent/read-only Value,
sensitive and wrong-target rejection, nonempty/concurrent content, failed
readback, opaque controller history, bounded/no-content diagnostic reads,
identity checks, tree bounds, timeout/progress/error reporting and opt-in.
The former Document-rejection fixture now represents a TextPattern-only Document
without the backend's writable-value eligibility marker; its fail-closed
assertions remain intact.

**Real SetValue remains untested**, as requested. Interface availability and
empty-state readings do not prove that Notepad will accept a write. Provider
errors or failed readback must remain unverified. ValuePattern has no atomic
compare-and-set; concurrent user edits can still race the final blankness check.
No substantial keyboard capability was needed or enabled.

## Exact manual retest

Open exactly one empty, visible, unminimized Notepad window yourself. Do not edit
it during the test. First run the read-only diagnostic:

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -B tools\diagnose_notepad.py --live --trials 3
```

Then, when you want to authorize actual entry:

```powershell
.\.venv\Scripts\python.exe -B -m aqua chat --allow-text-entry notepad --temperature 0.2 --diagnostics
```

Type: `write Hello from Aqua into my open Notepad window.`

Alternatively:

```powershell
.\.venv\Scripts\python.exe -B -m aqua handsfree --allow-text-entry notepad --language en --hints --temperature 0.2 --diagnostics
```

Say: `Hey Aqua, write Hello from Aqua into my open Notepad window.`

Look for `set_control_text: ok; verified=True` and visually check the destination.
A missing pattern/reference must produce no entry. For timeout/unknown outcomes,
inspect the actual document before making another request; do not blindly retry.

Regression commands:

```powershell
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern test_text_entry.py
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern test_notepad_diagnostic.py
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern 'test_desktop*.py'
.\.venv\Scripts\python.exe -B tools\test_offline.py
.\.venv\Scripts\python.exe -B -m pip check
```

## Files changed in this follow-up

- `src/aqua/desktop/backend.py`, `session.py`, `interaction.py`: shared, guarded
  Edit/Document eligibility.
- `src/aqua/desktop/interaction_session.py`: accurate missing-pattern limitation.
- `src/aqua/brain/controller.py`, `src/aqua/actions/tools.py`: accurate model
  descriptions; native tool arguments and authorization unchanged.
- `src/aqua/desktop/worker.py`: docstring clarification only.
- `tools/diagnose_notepad.py`: explicit, isolated read-only diagnostic.
- `tests/test_text_entry.py`, `tests/test_notepad_diagnostic.py`: regressions.
- `README.md`, `docs/text-entry-v0.8-stage2b1.md`, this report: evidence and manual commands.

No Stage 2B.2, models, benchmark data, backup, Qwen configuration or allowlisted
application identities were changed.
