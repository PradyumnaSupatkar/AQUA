# Fresh observation and combined desktop consent

November 15, 2026. Focused fixes for the two reported V0.8 issues. No new tools,
models, dependencies, keyboard/coordinate automation or Stage 2B.2 behavior was
added. The guarded ValuePattern writer is unchanged.

## Verified causes

The exact inventory question, `What applications can you currently see open on
my desktop?`, produced observation permission `none` in the old grammar. It
recognized `what can you see` and named-window queries, but not the intervening
application noun and `currently`. The controller consequently allowed a normal
plain answer with zero reads, including remembered desktop claims.

The exact combined request, `Open Notepad and write Hello from Aqua into it.`,
failed the launch-only scope parser: after splitting coordinated targets, its
writing clause was not an approved app target. Independently, the entry validator
accepted only already-open destinations; the controller prohibited accepting
entry authorization after other operations. Relaxing only the launch parser
would have executed a partial task without validating literal entry consent.

The supplied VS Code no-tool failure establishes that no successful fresh read
was obtained, not whether VS Code was open. The old final-report reason could
incorrectly say "only window metadata was observed" even when no read ran.

Deterministic tests reproduced the inventory false claim and combined denial
before production changes. All existing tests remain in the regression suite.

## Observation correction

- Explicit inventory on the user's desktop/screen grants metadata scope for
  approved Notepad, Calculator, VS Code and Chrome only. It does not reveal
  arbitrary apps, background processes, document values or screenshots.
- The model must choose a fresh native read. A zero-tool reply is not factual
  desktop evidence. Failed/missing reads report unknown state; successful empty
  reads report no approved visible windows observed, with scope/limits explicit.
- Each new task clears its listing cache. Current observation decisions use the
  current task's context; full session memory remains retained for normal chat.
- Named current-state questions such as `Is VS Code open?` also use metadata
  observation. Unsupported broad descriptions ask for scope rather than claim
  remembered facts. Ordinary questions about Windows/UI concepts remain chat.
- Approved-window UIA binding failures are no longer silently classified as a
  complete empty list: all-failed binding returns error; partial failure marks
  discovery truncated. No provider messages or sensitive text are exposed.
- Existing-window inspection failures no longer imply any earlier successful
  listing or that the window existed. Host reports render current tool evidence
  only, ignoring unsupported model prose.

## Combined consent and target verification

Qwen proposes `request_text_entry(app_id,text)` for either supported existing-
window entry or a combined single-app launch/entry. Python validates the complete
original current request, selected app and exact literal. Combined forms allow
`open/launch/start APP and/then write/type/... TEXT into it` (or `into its new
[empty] document/window`). `it` refers only to that single explicitly named app.
Unrelated apps, unresolved pronouns/literals, negation, hypotheticals, quoted
instructions and unsupported trailing effects fail before launch. Ambiguous
literal/extra-action wording requires quoted literal content.

The launch-only parser remains strict. If Qwen proposes launch first for a
supported combined request, that launch is rejected without execution. A single
bounded review may offer the existing entry-authorization tool. A candidate used
to check review eligibility is discarded: Qwen must independently propose the
app/literal and pass full validation before any launch. Unsupported requests do
not gain permission through review. There is no automatic operation sequence.

After accepted consent, the launch step independently rechecks it. Before any
process creation, its host-side security precondition records native top-level
window handles, including minimized/cloaked windows. This uses the fixed bounded
worker; incomplete/unavailable enumeration prevents launch. Only private handle
numbers are retained in RAM, not unrelated titles, process identities or UI text.
They are never sent to Qwen or added to conversation/tool history.

Qwen must then choose a new post-launch `list_windows` and `inspect_window`.
Previously existing handles are excluded from target references; old opaque
references cannot inspect/register those excluded windows. Post-launch listing
must be complete, and one unambiguous new approved window/control must be
observed. Native app/process/window/runtime identity, integrity/password checks,
empty value checks, single-use expiring refs and verified readback remain in
force. Launching a process alone never establishes readiness or entry success.

## Budget

| Agent operation | Model decision in normal flow |
| --- | --- |
| Validate full app/literal authorization | 1 |
| Launch same app, including private baseline security check | 2 |
| Fresh post-launch window listing | 3 |
| Inspect actual selected window | 4 |
| Guarded entry and readback | 5 |
| Final evidence-based answer | 6 |

The baseline is an internal read-only launch precondition, not a sixth
model-callable operation. It is an additional fixed worker metadata request;
it performs no launch or entry. OS identity/security checks remain inside their
respective existing tool boundaries.

A rejected early launch consumes one decision but zero executed operations,
matching existing diagnostics semantics. Recovery can use decisions 2–6 for the
five validated operations; after a verified write on decision 6, Python reports
the real result without requiring a seventh model call. The same budget covers
one ordinary premature no-call answer. No second review or discovery retry is
available if the five operations/six decisions are exhausted. Insufficient
budget is rejected before a partial launch.

## Verification

- Focused new tests: **26 passed**. Existing observation checks also passed.
- Full offline regression: **974 passed in 5.297s**, zero failures/errors
  (previous 948 plus 26). `pip check`: **No broken requirements found.**
- Coverage includes fresh versus remembered facts, repeated tasks, failed UIA
  binding, unsupported scope, literal consent, both first-tool orderings,
  baseline failure/truncation, pre-existing/hidden handles and stale refs,
  new-target ambiguity, launch/startup failure, nonempty-field protection,
  wrong apps, duplicate launches, cancellation/unknown effects and hard limits.
- Unit tests block real sockets/process execution and use synthetic workers.
  No physical microphone or real desktop input was used.

Additional local checks used the unchanged Qwen Instruct at temperature 0.2:

| Check | Actual result |
| --- | --- |
| Combined task, real Qwen with synthetic UI/launch/write executors | completed; 5 operations, 6 decisions; one stub launch/write; 23.526s |
| Injected premature launch proposal, followed by real Qwen recovery | premature launch rejected; completed within 5 operations/6 decisions; one stub launch/write; 20.500s |
| Real VS Code, read-only Qwen/UIA | list and inspect both ok; one window, 14 controls; 3 decisions; 11.036s total |
| Real VS Code read timings in that check | listing 0.575s; inspection 0.621s; neither truncated |
| Real private baseline, no launch | ok; 335 handles; not truncated; 0.437s |

The forced-first check injected only the first invalid native proposal; subsequent
decisions used the real model. Its native history retains the rejected call and
real rejection result. The natural combined check used Qwen for every decision.
These are smoke checks, not a statistical completion benchmark. Timing overlapped
some regression work and is not a latency optimization claim. Real VS Code was
verified open **at check time**; its state during the earlier failure is unknown.
Actual combined Windows launch-and-write remains for the user's manual test.

## Safe manual retest

Fresh observation needs no text-entry opt-in:

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -B -m aqua chat --temperature 0.2 --diagnostics
```

Ask `What applications can you currently see open on my desktop?` Expect a fresh
`list_windows` result and an approved-scope report, or explicit uncertainty.
Then ask `What can you currently see in my VS Code window?` A successful interface
answer requires a successful inspection; do not assume the app is open.

For combined entry, manually save/manage existing documents and close Notepad
windows yourself first. Aqua does not close them or discard their contents.

```powershell
.\.venv\Scripts\python.exe -B -m aqua chat --allow-text-entry notepad --temperature 0.2 --diagnostics
```

Enter `Open Notepad and write Hello from Aqua into it.` Confirm actual launch,
fresh listing/inspection and `set_control_text: ok; verified=True`, then visually
check the destination. Hands-free uses the same controller:

```powershell
.\.venv\Scripts\python.exe -B -m aqua handsfree --allow-text-entry notepad --language en --hints --temperature 0.2 --diagnostics
```

Say `Hey Aqua, open Notepad and write Hello from Aqua into it.` Check the printed
transcript before treating the test as equivalent.

If Notepad reuses a pre-existing window or opens a new tab within it, no new
window identity is established and entry is refused. Restored nonempty documents,
slow startup, multiple new windows, inaccessible UIA or truncation also stop
entry. The current tools do not force a new document, change application settings
or poll/retry beyond the budget. A process launch may therefore succeed while
entry remains unattempted. Use a separate explicit existing-window request only
after manually checking/preparing an empty destination; do not blindly retry an
unknown write outcome. The five/six limit and no-overwrite rule take priority.

Regression commands:

```powershell
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern test_desktop_tasks.py
.\.venv\Scripts\python.exe -B tools\test_offline.py
.\.venv\Scripts\python.exe -B -m pip check
```

## Files changed

- `src/aqua/desktop/authorization.py` — fresh inventory/current-state consent.
- `src/aqua/desktop/reporting.py` — scope and unknown-state evidence reporting.
- `src/aqua/desktop/backend.py` — bounded private native baseline; binding errors.
- `src/aqua/desktop/session.py` — task-local baseline, excluded stale targets.
- `src/aqua/desktop/interaction_session.py` — exact combined consent/review check.
- `src/aqua/actions/tools.py` — updated existing authorization tool description.
- `src/aqua/brain/controller.py` — model-driven combined steps, one review, limits.
- `src/aqua/brain/task.py` — combined task state and truthful launch/entry report.
- `tests/test_desktop_tasks.py` — 26 deterministic regressions.
- `README.md` and this report.

The allowlist, fixed launcher commands, value writer, speech models, Ollama model
storage, benchmark results and unrelated features are unchanged.
