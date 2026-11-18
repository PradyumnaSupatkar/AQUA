# Aqua V0.8 Stage 1: bounded agent loop

Implemented and verified on November 14, 2026. This stage extends the V0.7
controller used by text chat, manual voice, and hands-free mode. It adds no tools,
dependencies, model changes, microphone processing changes, or persistent state.
The completed intelligence benchmark and its saved observations are unchanged.

## Execution and authorization

For each user task, Qwen chooses a native `open_application(app_id)` proposal.
Python validates the completed stream, exact tool/arguments, and permission in
the original request before executing the existing fixed launcher. The actual
result is committed before Qwen decides the next step. There is one call per
decision; parallel batches are rejected without executing any part of the batch.
The host does not generate a workflow or choose an execution order.

An explicit request such as **Open Notepad and Calculator** authorizes those
two approved apps. Host consent checks allow this scope without selecting the
model's next action. Unknown extra targets, unsupported operations (including
effects hidden in launch purposes), negation, quoted instructions, hypothetical
requests, and conditional execution fail closed. Executable paths, arguments,
shell commands, and new tools are never accepted from the model.

The tool schema narrows to remaining authorized apps after each result. An app
can be attempted only once per task, including after failure or an uncertain
outcome. Replayed call identifiers cannot execute a different app. An explicit
new request starts a new task and may request the same app again.

The hard limits are **five tool operations and six model decisions per task**.
Internal callers can reduce these limits, never increase them. With four current
approved apps and duplicate suppression, normally at most four launches can be
attempted. Logical decisions are counted separately from HTTP compatibility
retries; diagnostics display both counts. No automatic retry of an operation
occurs.

### Premature-stop reliability correction

The controller previously finalized every completed no-tool response, even when
the original explicit task still had authorized, unattempted apps and spare
budget. Text, voice-chat, and hands-free share this branch and pass the same
conversation/settings; hands-free does not drop continuation context. Model
responses sometimes asked for consent again rather than issuing the next call.
Stopped model prose is not persisted, so an earlier user's exact hands-free reply
cannot be recovered from disk.

An otherwise eligible incomplete task now permits **one model-driven
reconsideration per task**, consuming an existing model decision. Its temporary
context includes the original request, actual completed/failed results, remaining
authorized scope, and the prior reply quoted as untrusted model output. It does
not invent a new user request or enlarge permission. The normal tool validation,
original-request authorization, duplicate checks, and history reservation still
apply to every proposed operation. A second no-tool reply ends the task as
incomplete; the host never executes a remaining app itself.

Cancellation, errors, rejected/unsupported requests, reached limits, and
recognized refusal/stop language bypass reconsideration. The refusal veto is
conservative, includes common curly-apostrophe forms, and does not route actions
or filter answer text. The review also explicitly permits upholding a refusal or
dependency reason to stop. This remains an opportunity for the model to correct
premature stopping, not a guarantee that it will continue.

Focused verification passed **61 agent tests and 23 hands-free tests**. Updated
full regression passed **750 tests in 3.250s**, and `pip check` passed. Changed for
this correction: `src/aqua/brain/controller.py`, `tests/test_agent_loop.py`,
`tests/test_agent_smoke.py`, `tests/test_handsfree.py`, `README.md`, and this report. Speech,
VAD, generation settings, models, and tools were unchanged.

The short local recovery smoke test used temperature 0.2/seed 42 and recording
executor stubs. One no-tool reply was deliberately injected after Qwen's first
valid proposal to exercise the exact failure branch. On reconsideration, the
actual installed Qwen returned a valid Calculator call. The task completed with
**four logical decisions, two stubbed operations, three real HTTP requests, and
5.286s total**. History retained the original user request and both real result
pairs. No Windows application was launched. The injected stop is not presented
as a spontaneous model response. An earlier review wording still elicited a
second consent question; the final wording clarifies that existing consent need
not be renewed. No broad reliability rate was measured.

A final ordinary `tools/smoke_agent.py --live` run, with no injected model reply,
also passed: Notepad then Calculator, three real model decisions, two stubbed
operations, **3.501s total**. No applications were launched.

An implied activity can arm one canonical pending app and ask for confirmation.
An exact affirmative consumes that offer once; it authorizes only that app.
Ambiguous multi-app goals do not arm broad approval. New utterances expire old
offers. Cancellation, rejected speech, and session controls preserve their
existing disarming behavior. `/clear` clears history, action state, and task
progress together.

## History and truthful progress

`Conversation.last_task` is an immutable in-memory snapshot of authorized apps,
decisions, attempted operations, successes, failures, unknown outcomes, remaining
apps, task status, and stop reason. It is evidence, never permission.

One retained user-turn bundle contains the original request, **all** paired
assistant calls and real tool results, and the final answer. Subsequent requests
receive the full active bundle plus trusted progress. Each result also carries
the remaining authorized scope. Before each external effect, the complete active
bundle and space for the final summary must fit the history budget. Earlier
steps of that task cannot be pruned to fit another operation. Older whole turns
remain subject to the existing bounded history limits.

For known authorized action tasks, model prose is buffered and replaced with a
host-authored result summary. A fabricated model completion cannot hide a failed,
unknown, or unattempted launch. Normal tool-free conversation continues to stream
and retain context. The controller does not verify all factual statements in
ordinary conversation.

Cancellation after an attempt retains its recorded result. An interrupted or
unexpected launcher outcome is recorded as unknown and never retried. Process
creation is the strongest existing launcher evidence; window readiness is not
checked. Cancellation cannot undo an already created process.

## Verification

The affected authorization/history/controller/session tests ran first. Initial
Stage 1 regression verification passed **739 tests in 3.138 seconds**, including the
existing speech, chat, wake, microphone calibration, and benchmark-grading tests.
The offline runner blocked DNS, real sockets, and process launches during test
discovery and execution. `pip check` reported **No broken requirements found**.

New coverage exercises both model-chosen orders, multiple real-result exchanges,
failure followed by a different authorized operation, duplicate/replayed calls,
unknown tools, unsafe mixed requests, history reservation, cancellation/unknown
outcomes, stale and retargeted confirmations, early/fabricated completion, lower
host limits, the fixed hard-limit constants, ordinary streaming, and clearing.
Wrapper and hands-free tests cover multiple steps in one user turn/wake.

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern test_agent*.py
.\.venv\Scripts\python.exe -B tools\test_offline.py
.\.venv\Scripts\python.exe -m pip check
```

### Demonstrated multi-step task with stubbed executors

`tools/smoke_agent.py` uses the installed Qwen model and production controller,
with a recording executor stub and an independent process-launch guard. It
requires `--live`, uses temperature 0.2/seed 42, makes only local Ollama requests,
and saves no conversation. It is a functional check, not a benchmark rerun.

```powershell
.\.venv\Scripts\python.exe -B tools\smoke_agent.py --live
```

Final observed result with Ollama 0.40.16 and
`qwen3:4b-instruct-2507-q4_K_M`:

```text
Fixture: Open Notepad and Calculator.
Stub execution order: ['notepad', 'calculator']
Task: completed; decisions=3; operations=2; requests=3; total=4.673s
History roles: user, assistant, tool, assistant, tool, assistant
PASS: effects were stubbed; no Windows applications were launched.
```

Each recorded launch reported simulated process creation, with readiness
explicitly unchecked. A result was present in history before the next choice.
Both model-chosen orders are also covered deterministically.

The separate failure probe is deliberately stricter: it checks whether Qwen
continues to the second independent app after the stub fails the first:

```powershell
.\.venv\Scripts\python.exe -B tools\smoke_agent.py --live --fail-first
```

**Observed limitation:** this probe returned exit status 1. Qwen chose Notepad,
received its simulated failure, then stopped. Aqua reported one failure and
Calculator unattempted: `incomplete`, two decisions, one operation, **3.352s**.
It did not claim completion, retry Notepad, or launch anything. Deterministic
tests verify that if the model instead chooses Calculator after that failure,
it receives the genuine failure first and the final status is `partial_failure`.
Earlier success probes also exposed unnecessary re-confirmation; the final
context omits irrelevant ambiguous-offer examples for an already authorized task
and explicitly preserves consent for its remaining targets. These checks do not
establish a general multi-step reliability or latency rate.

## Running Aqua

```powershell
.\.venv\Scripts\python.exe -m aqua chat --temperature 0.2 --diagnostics
.\.venv\Scripts\python.exe -m aqua voice-chat --language en --temperature 0.2 --diagnostics
.\.venv\Scripts\python.exe -m aqua handsfree --language en --temperature 0.2 --diagnostics
```

The installed model, default model selection, context, and keep-alive remain
unchanged. The benchmark's recommended temperature is selected explicitly;
the pre-existing CLI sampling default remains 0.5. Whisper stays Small/CPU INT8.
No fresh live microphone session was necessary; existing voice/hands-free paths
were verified by the offline regression tests.

## Remaining limits

- Qwen can stop early, propose a batch, or make a bad tool choice. The host stops
  safely and reports real progress rather than forcing another operation.
- Multi-step permission checks remain conservative and English-oriented; an
  unfamiliar paraphrase can require clarification. Implied multi-app goals do
  not grant multi-app consent.
- Host-authored action summaries prioritize evidence over conversational prose.
  They report process creation rather than application readiness.
- No persistent task resume, automatic failed-action retry, or rollback exists.
  History remains bounded by complete user-turn bundles and a character budget,
  which is a heuristic rather than an exact token budget.
- No Stage 2 project inspection/test tools, unrestricted filesystem/terminal
  access, TTS, screen vision, or additional desktop actions were implemented.

Changed files: `src/aqua/actions/tools.py`, `src/aqua/brain/controller.py`,
`src/aqua/brain/conversation.py`, `src/aqua/brain/task.py`, `src/aqua/chat.py`,
`src/aqua/__init__.py`, `pyproject.toml`, `README.md`, this report,
`tools/smoke_agent.py`, `tests/test_agent_authorization.py`,
`tests/test_agent_history.py`, `tests/test_agent_loop.py`,
`tests/test_agent_smoke.py`, `tests/test_controller.py`,
`tests/test_action_router.py`, and `tests/test_handsfree.py`.
