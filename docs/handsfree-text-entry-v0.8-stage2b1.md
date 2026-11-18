# Hands-free text-entry authorization and continuation

November 15, 2026. Stage 2B.1 follow-up only. The user has verified real
typed-chat Notepad entry (`set_control_text: ok; verified=True`). This follow-up
did not modify the UIA writer or perform any real desktop input.

Later follow-up: [initial tool-selection parity investigation](text-entry-parity-v0.8-stage2b1.md)
addresses the distinct one-decision/zero-operation refusal. It retains this
report's historical results and brings the current suite to 941 tests.

## Findings and reproduction

The text and hands-free paths share `chat._respond` and
`ConversationController.respond`. Hands-free's `command_text` removes only a
leading wake greeting and surrounding whitespace, then forwards the remaining
transcript. Its session opt-in is correctly reapplied after lazy Ollama startup.
No input-mode-specific tool registry, gain setting, authorization override or
continuation engine was found.

Three implementation defects were confirmed:

1. The current-user literal-consent validator required an anchored
   `write/enter/type/insert TEXT into my/the open APP window` form. It did not
   accept the harmless introductory `So,` or the explicit verb `write down`.
   The supplied clear spoken variation failed this validator even when Qwen
   proposed the exact correct text. It now also accepts `write down`/`note down`
   and optional `So,`, while matching the payload exactly and still requiring
   the explicit open-window destination. This validates an LLM proposal; it
   does not choose an intent, tool, app or control.
2. The authorized-entry prompt said to stop if no `ci_*` token was exposed,
   without restricting that rule to a completed inspection. Immediately after
   authorization there can be no fresh token: discovery/inspection have not run.
   Reconsideration then added a **Read-only review** instruction to the writing
   task because writing also uses observation mode. These contradictory prompts
   encouraged premature no-call answers. The context now includes a trusted
   next-stage/available-tool ledger, treats missing preinspection references as
   expected, and supplies a writing-specific review.
3. `TaskProgress.summary` still described only Edit controls. Both success and
   no-entry summaries now describe Edit or Document with writable ValuePattern.
   The existing backend's Document support remains unchanged.

Before production changes, deterministic prompt-sensitive model doubles
reproduced the supplied failure through the real hands-free pipeline:
**3 decisions, 1 authorization operation, 0 completed operations, no worker
request, final stage `task_reconsideration`**. The controller had not exhausted
its limits: two consecutive model replies contained no native tool call, so its
single review was consumed and the task ended. This was not a UIA setter failure.

Original live raw Qwen replies were not available from the supplied host summary,
so their exact prose/native serialization cannot be reconstructed. The prompt
defects and matching deterministic reproduction are established; they do not
prove an exclusive internal cause for every possible Qwen early stop.

## Bounded behavior and security

The fix does not execute a pending stage automatically. Qwen must still choose
each native tool call, with host validation at every boundary. One reconsideration
remains the maximum. Genuine refusal stops it. Tool/decision exhaustion is checked
before another review; two no-call replies report the actual unperformed stage
instead of implying the UIA editor was inspected and unsupported.

The original 5-operation/6-decision limits are unchanged. Normal success uses
four operations and five decisions. A single initial no-call reply can recover
within four operations and six decisions. Further no-call replies fail closed.

Current-request consent, case-sensitive literal matching, Notepad capability
scope, empty-document protection, independent window/control identity checks,
one-use opaque references, cancellation, duplicate prevention and readback-based
reporting remain intact. Earlier history stays conversational context but cannot
authorize current entry or supply missing literal text. Tests force the model to
reuse old text on a new `Right` utterance and verify that no new entry occurs.

The transcript fragments `Right`, `...in Tube Notepad`, or an incomplete
`Note down ...` without an explicit destination do not authorize entry. They are
not automatically rewritten to a command. The model is instructed to ask for
the missing literal/destination when unclear; any invalid entry proposal receives
the host's concise complete-request guidance. Speech recognition itself was not
changed; the investigation does not justify replacing Whisper Small.

## Verification

14 new deterministic tests cover the supplied spoken forms and adversarial
variations, exact typed/real hands-free controller paths with simulated
transcription, a recovery after one no-call reply, honest stopping after two,
genuine refusal, hard limits, prior launch/conversation, old-payload rejection,
single dispatch, unverified readback and Document-capable summaries.

Focused checks passed for the new tests, existing text-entry tests, hands-free
tests and the Stage 1 agent loop. An initial full run caught a launch-ledger
compatibility regression introduced during this fix; its original format was
restored without changing the assertion. Final full offline regression:
**932 passed in 5.547 seconds, zero failures/errors** (previous 918 plus 14).
`pip check`: **No broken requirements found.** Unit tests block real sockets and
process launches and require no microphone or application.

Two completed additional local-model checks used the installed
`qwen3:4b-instruct-2507-q4_K_M`, Ollama 0.40.16 and temperature 0.2:

| Live model check | Outcome | Decisions / operations | Stub writes | Elapsed |
| --- | --- | --- | --- | --- |
| Exact typed `Write Hello from Aqua into my open Notepad window.` | completed | 5 / 4 | 1 | 13.952s |
| Hands-free pipeline with simulated `Hey Aqua, So, can you write down I am Aqua, your personal assistant into the open notepad window?` | completed | 5 / 4 | 1 | 13.825s |

Both produced native `request_text_entry`, `list_windows`, `inspect_window`,
`set_control_text` decisions, consumed the stubbed verified result and reported
completion. Synthetic window/control observations and a stub writer were used;
all real application executors and worker process launches were blocked. These
are local-Qwen integration checks, **not real UIA writes or microphone/Whisper
latency measurements**. One earlier live hands-free harness attempt stopped at
model metadata setup because it reused the unit fixture's socket guard. The
subsequent explicitly live harness kept desktop effects blocked while allowing
only the production client's loopback Ollama connection. Offline tests retain
their network guards.

These two successes are a small smoke sample, not a guaranteed completion rate.
Qwen can still refuse or fail to make a valid native call; it then stops safely.
Actual hands-free microphone recognition/entry remains for the manual retest.
No model, dependency, temperature default, UIA backend, wake/VAD/audio behavior,
application allowlist or benchmark artifacts were changed.

## Fresh manual test

Manually prepare exactly one visible Notepad window with an empty document.
Do not edit it during the task. Start a fresh Aqua session:

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -B -m aqua handsfree --allow-text-entry notepad --language en --hints --temperature 0.2 --diagnostics
```

Say: **Hey Aqua, write Hello from Aqua into my open Notepad window.**

For the variation, manually prepare an empty document again, then say:
**Hey Aqua, so, can you write down I am Aqua, your personal assistant into the open Notepad window?**

Check the printed recognized text, the native tool results, and the actual
document. Success requires `set_control_text: ok; verified=True`. A nonempty
document must be refused. For an unknown outcome, inspect it manually before
issuing a new request. Do not use automatic retries or treat plain model prose
as verification. A bare `Right` must never reuse a previous entry instruction.

Typed comparison, with a separately prepared empty document:

```powershell
.\.venv\Scripts\python.exe -B -m aqua chat --allow-text-entry notepad --temperature 0.2 --diagnostics
```

Enter: `Write Hello from Aqua into my open Notepad window.`

Regression commands:

```powershell
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern 'test_text_entry*.py'
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern 'test_handsfree*.py'
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern test_agent_loop.py
.\.venv\Scripts\python.exe -B tools\test_offline.py
.\.venv\Scripts\python.exe -B -m pip check
```

## Files changed

- `src/aqua/desktop/interaction_session.py`: narrow explicit spoken consent forms.
- `src/aqua/brain/controller.py`: phase-aware continuation, writing-specific
  reconsideration, clearer no-call/refusal/limit stop reasons and clarification context.
- `src/aqua/brain/task.py`: truthful Edit/Document-capable summaries.
- `tests/test_text_entry_handsfree.py`: 14 offline deterministic regressions.
- `README.md`, `docs/text-entry-v0.8-stage2b1.md`, this report: current evidence,
  test results and manual commands.

This follow-up stops at Stage 2B.1.
