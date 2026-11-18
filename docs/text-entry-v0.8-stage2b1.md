# Aqua V0.8 Stage 2B.1 — focused text-entry recovery

November 15, 2026. The user subsequently verified real typed-chat Notepad entry
with `set_control_text: ok; verified=True`. The initial recovery and subsequent
hands-free development performed no real desktop input. Historical test evidence
below is preserved; see the current follow-up reports for later verification.

Follow-up: [verified modern Notepad diagnosis](notepad-diagnosis-v0.8-stage2b1.md).
The current implementation accepts **Edit or Document with writable
ValuePattern**, with the same identity/empty-value/readback gates. The follow-up
read-only probe found a real eligible Document. Historical observations below
describe the initial recovery. The [hands-free follow-up](handsfree-text-entry-v0.8-stage2b1.md)
fixes literal-consent variations and continuation prompts; the current suite has
932 passing tests.

## What was recovered

The two unfinished interaction modules were inspected before editing. Their
bounded runtime-ID search, UIA security-gate approach and isolated-worker design
were retained and narrowed to this vertical slice. The unfinished focus, invoke,
selection and expand behaviors are not enabled or dispatched. The supplied
`D:\Aqua-backup-20261115-143604` backup was not modified.

The existing Qwen loop now supports:

1. `request_text_entry(app_id, text)` — Qwen proposes the current user's exact
   literal instruction. The host independently validates it; no UI action occurs.
2. `list_windows()` — fresh current-task discovery of the approved app.
3. `inspect_window(window_id)` — actual bounded inspection exposes eligible Edit/Document
   metadata and an opaque `ci_*` control reference, never its private runtime ID.
4. `set_control_text(control_id)` — Qwen selects a real observed reference. The
   host supplies the already-authorized text; the model cannot change it here.
5. UIA readback and a host-authored outcome — API return alone is not success.

This is not a scripted Notepad workflow. The engine uses generic window identity,
UIA traversal and writable ValuePattern operations. The initial **capability policy**
allows Notepad only; it does not use Notepad window classes, titles, menu paths,
coordinates or keystrokes. Browser fields, external submission, other application
writes and the rest of Stage 2B are not enabled.

The first authorization tool is part of the same native model-driven loop, not
another intent classifier. Its validator does not extract an intent, select an
app/control or schedule a workflow. It compares the model-proposed app and text
against the complete current-user literal instruction. Supported explicit wording
is deliberately narrow: `write/enter/type/insert [literal] into my/the open
[approved app] window`, with optional greeting/politeness and quoted payload.
Other wording needs a clearer full request. Payload case and text are preserved;
UI-provided text cannot grant permission or substitute the payload.

## Security corrections

- Writes require `--allow-text-entry notepad`; ordinary sessions and Stage 2A
  observation remain read-only. The CLI opt-in alone is not a per-task grant.
- Current-task app/literal consent, fresh discovery and fresh control inspection
  are required. One visible approved window and one eligible editable field are
  required; ambiguity fails closed instead of choosing an arbitrary target.
- Window references remain bound to ObservationSession's verified private
  identity. Registration no longer accepts arbitrary caller-supplied app/window
  identities or control lists.
- Native control runtime IDs stay private. `ci_*` references are random,
  task-local, one-use and expire after 60 seconds. New tasks, completion,
  cancellation and `/clear` remove grants and control references. Old inspection
  snapshots cannot register controls for a new grant.
- Before execution the worker independently verifies actual process image/package,
  window handle, PID, creation time and UIA window runtime ID. The selected control
  must still belong to that root/process and match its observed runtime ID.
- The target must be visible, enabled, non-password, an Edit or Document, and support a
  writable ValuePattern. Elevated/protected or integrity-unknown targets are
  rejected. No focus or keyboard/mouse action is used.
- `CurrentValue` must equal the empty string, including after a second final
  identity/security check. Whitespace is nonempty and is not discarded.
- Only a specifically authorized write checks that single field's value internally
  for blankness/readback. Existing contents are never returned, logged, put into
  conversation, or sent to Qwen. Default Stage 2A never reads these values.
- The installed pywinauto 0.6.9 source/API was checked: use
  `get_elem_interface(element, "Value")`, which resolves `pattern_ids` and performs
  `QueryInterface`. The actual installed mapping was verified as pattern **10002**,
  `IUIAutomationValuePattern`. The incorrect `.iuia.PatternIds` lookup and duck-typed
  interface fallback were removed.
- Only `set_control_text` is added to the isolated interaction dispatcher.
  Focus/invoke/select/shell operations are rejected. No coordinates, clipboard,
  screenshots, vision, text replacement or external-send fallback exists.
- The text bound is **1,024 UTF-8 bytes**, not characters. Both parent and worker
  enforce the **8,192-byte encoded request limit**; parent serialization now uses
  actual UTF-8 rather than ASCII escape expansion.
- A pending unknown outcome is journaled in paired tool history **before dispatch**.
  Verification finalizes that same result. Cancellation after a possible side
  effect can therefore never be reported as "no write was performed."
- Success requires matching UIA readback plus post-write identity/privacy checks.
  Errors after `SetValue`, mismatches, timeouts and interrupted calls remain
  unverified/unknown. Tokens are consumed before dispatch and never auto-retried.
- Application text stays untrusted data. Model prose cannot establish completion;
  the final reply is generated from the host's attempted/verified records.

The existing five-operation/six-decision caps, one bounded reconsideration,
history reservation and launch confirmations remain in place. The normal
successful path uses **four tool operations and five model decisions**.

## Verification actually performed

- Baseline reproduced: **863/863 tests passed** before changes.
- Focused suite: **37/37 passed** using synthetic UIA/provider/process results.
- Full regression: **900/900 passed**, including the original 863.
- `pip check`: **No broken requirements found**.
- No dependencies, models or benchmark artifacts were downloaded or changed.

Coverage includes field registration/expiry, stale/spoofed identities, current
task consent, private IDs, nonempty/whitespace preservation, password/unknown
privacy, disabled/hidden/non-Edit/read-only fields, unsupported patterns, wrong
apps/windows/processes, privileged rejection, API errors after effects, failed
readback, duplicate dispatch, cancellation, worker timeout/unknown handling,
UTF-8 boundaries, worker dispatch, CLI propagation, both loop limits, complete
paired history and pre-dispatch outcome journaling.

All automated tests block real sockets and process launches. No physical
microphone, application launch, focus, keyboard/mouse operation or real desktop
write occurred during automated tests.

A real **read-only** Notepad discovery returned `empty`, zero approved visible
windows, in **0.191s**. Thus no real Notepad Edit control or writable ValuePattern
was verified. No application was opened to manufacture a test target.

An actual local Qwen check used **synthetic window/control observations and a
synthetic verified write result**, with real process launches blocked. Initially
the model stopped after authorization; the authorization result/context was
clarified to distinguish accepted consent from task completion. The subsequent
check selected authorization, discovery, inspection and the actual fixture's
`ci_*` token, then consumed the write result. It completed in four operations,
five decisions, **15.388s**. This verifies native model/controller connectivity;
it is **not evidence of a real Windows write** or a general completion-rate test.

## Exact manual verification

Open one visible Notepad window yourself and ensure the intended editor is truly
empty. Leave it idle during the test. Do not use a document containing text you
want to preserve. No command below launches or clears Notepad.

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -m aqua chat --allow-text-entry notepad --temperature 0.2 --diagnostics
```

Type:

```text
write Hello from Aqua into my open Notepad window.
```

Hands-free:

```powershell
.\.venv\Scripts\python.exe -m aqua handsfree --allow-text-entry notepad --language en --hints --temperature 0.2 --diagnostics
```

Say:

```text
Hey Aqua, write Hello from Aqua into my open Notepad window.
```

Manual voice alternative:

```powershell
.\.venv\Scripts\python.exe -m aqua voice-chat --allow-text-entry notepad --language en --hints --temperature 0.2 --diagnostics
```

Expected successful evidence is `set_control_text: ok; verified=True` and the
host statement that UIA readback verified entry. Visually confirm the actual
destination. An unsupported or ambiguous field must instead report no entry;
an unknown/timeout result must be checked manually before making a new request.
Do not interpret a model's free-form "Done" as verification.

Regression commands:

```powershell
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern test_text_entry.py
.\.venv\Scripts\python.exe -B tools\test_offline.py
.\.venv\Scripts\python.exe -m pip check
```

## Remaining limitations

- **Actual Windows writing is not verified.** That part of the success criterion
  remains for the manual test, as requested by the user.
- A Document with TextPattern alone remains unsupported. The follow-up probe
  confirmed this Notepad's Document also exposes writable ValuePattern; it now
  qualifies for the same guarded value-entry path. No keyboard fallback exists.
  Actual provider acceptance of SetValue remains for the user's manual test.
- UIA `SetValue` has no atomic compare-and-set operation. Blankness is checked
  twice immediately around identity/security validation, but a concurrent user
  or application edit can race the final check. Leave the destination idle.
- Readback is a snapshot, not proof of saving or permanent application state.
  No save, invoke, keyboard or external transmission action is implemented.
- The current policy requires one visible app window and one eligible field.
  Selection/focus, ambiguous-target confirmations and additional applications
  are outside this slice; existing one-use launch confirmations are unchanged.
- Qwen or speech recognition can misidentify literal text or stop early. The
  candidate validator fails closed on mismatched text; the host never executes
  an unproposed pending operation or invents verification.
- Enabling the text-entry capability buffers the initial model answer until its
  tool decision is complete, preventing premature action claims. Default sessions
  retain their existing streaming behavior.

## Files changed

- `src/aqua/desktop/interaction.py` — recovered/narrowed native empty-Edit backend.
- `src/aqua/desktop/interaction_session.py` — literal grant, private bindings, expiry.
- `src/aqua/desktop/backend.py` — private eligible-Edit discovery, integrity checks.
- `src/aqua/desktop/session.py` — private inspection snapshots, UTF-8 request sizing.
- `src/aqua/desktop/worker.py` — narrowly scoped interaction dispatch.
- `src/aqua/actions/tools.py` — validated native preparation/write tool schemas.
- `src/aqua/brain/controller.py` — model-driven integration and truthful journaling.
- `src/aqua/brain/conversation.py` — interaction lifecycle and paired result finalization.
- `src/aqua/brain/task.py` — attempted/verified text-entry task outcomes.
- `src/aqua/cli.py`, `src/aqua/chat.py`, `src/aqua/handsfree.py` — explicit opt-in.
- `tests/test_text_entry.py` — 37 focused deterministic regressions.
- `README.md` and this report — instructions, results and limitations.

The implementation stops at Stage 2B.1. No Stage 2B.2, TTS, vision, internet tool,
new model, unrestricted terminal or general button automation was added.
