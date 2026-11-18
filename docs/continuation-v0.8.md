# V0.8 continuation investigation — November 15, 2026

This correction changes one production file, `src/aqua/brain/controller.py`.
It adds no application workflows, tools, dependencies, model changes, or speech
changes. Qwen still chooses every operation through a native tool call.

## Findings and scope of the evidence

The previous refusal guard searched anywhere in the model's prose for words
such as **cannot**, **won't**, **do not**, and **stop**. Consequently, a readiness
caveat (`I cannot verify the window is ready`) or a duplicate safeguard
(`I won't retry an already attempted application`) could veto reconsideration
without declining the remaining authorized work. Deterministic regressions
confirm this specific failure mechanism.

The final stage alone was also ambiguous. `tool_result_followup` could mean an
explicit refusal, exhausted decision budget, or ordinary completion.
`task_reconsideration` with unfinished work means the review returned no usable
native call or declined continuation. Neither stage proves that a second tool
call was lost. The controller cannot execute a prose promise or a repeated
consent question as though it were a native proposal.

The earlier live investigation captured a reconsidering answer that again asked
whether to open the remaining app, with no native call. The present investigation
inspected completed answer and structured `tool_calls` channels directly. All
spontaneous clean-session trials completed both apps, **including the baseline**.
Thus, the two reported hands-free incidents were not reproduced or retroactively
diagnosed from their exact model responses; those responses were not persisted.
The confirmed code defect should not be confused with proof that it caused both
particular incidents.

Hands-free, voice-chat, and text use the same conversation controller. Hands-free
removes the wake phrase before dispatch and retains the same conversation,
generation settings, model, and action state. No continuation-context difference
or dropped native call was found in the inspected paths or live traces.

## Minimal correction

- Narrowed the recovery veto to explicit task/action refusal or cancellation.
  Readiness uncertainty and no-retry safeguards alone no longer suppress the
  one allowed reconsideration. Common contracted safety refusals remain covered.
- Replaced the long review ending with an explicit host-authored continuation
  state: actual attempts/results, remaining authorized apps, existing consent,
  and remaining operation/decision budgets. The prior untrusted answer precedes
  the final instructions, reducing emphasis on an earlier consent question.
- Added distinct stop reasons for skipped refusal, refusal during review,
  exhausted review without a native call, and exhausted decision budget.

No executor behavior changed. A review is still limited to once per task and
consumes an existing model decision. A new proposal still passes the same strict
tool/argument validation, original-request authorization, duplicate/replay check,
history reservation, and **5-operation / 6-decision** hard limits. A plain reply
never executes a remaining operation. User cancellation and rejected/unsupported
requests keep their existing stop paths.

## Actual live results

Installed Ollama **0.40.16**, `qwen3:4b-instruct-2507-q4_K_M`, temperature **0.2**,
context **4096**, seed **unset**, existing keep-alive. Every trial created a fresh
`Conversation`; one checked client/server was reused, without unloading models.
Executors returned recorded stub results. An independent `Popen` guard prevented
real Windows launches. There was no microphone capture, transcription, download,
or intelligence-benchmark rerun.

| Trials | Before | After |
| --- | ---: | ---: |
| `Open VS Code and Calculator`, 10 clean sessions | 10/10 (100%) | 10/10 (100%) |
| Same request with a final period, 5 clean sessions | 5/5 (100%) | 5/5 (100%) |
| Total spontaneous clean sessions | **15/15 (100%)** | **15/15 (100%)** |

All natural trials used three model decisions and two stub operations. The final
unpunctuated group had a **5.014s median**, range **4.965–5.729s**. The final
punctuated group ranged **5.072–5.294s**. These are sequential functional
observations, not controlled latency or broad accuracy benchmarks. The equal
before/after completion rates do **not** establish a measured spontaneous
completion-rate improvement.

### Recovery branch, explicitly injected stops

Six additional clean sessions deliberately substituted one no-call answer after
the first actual Qwen proposal and successful executor-stub result:

1. `I cannot verify that the application window is ready.`
2. `I won't retry an already attempted application.`
3. `The first app is open. Would you like me to open the other requested app?`

Each was tried with both `Open VS Code and Calculator` and
`Open Calculator and VS Code`. **6/6 completed (100%)**. The initial and
reconsidering proposals came from actual local Qwen; only the labeled middle
no-call answer was injected. Every recovery used four logical decisions, two
stub operations, and three real HTTP generation requests. Times ranged
**4.569–12.907s**; cold/warm/load effects were not isolated.

Example structured trace:

```text
Decision 1 (Qwen): open_application {"app_id":"vs code"}
Tool result (stub): launch_attempt_succeeded, readiness_checked=false
Decision 2 (injected): "I cannot verify that the application window is ready."
Decision 3 (Qwen, reconsideration): open_application {"app_id":"calculator"}
Tool result (stub): launch_attempt_succeeded, readiness_checked=false
Decision 4 (Qwen): plain final answer, no further call
Host result: completed; 4 decisions; 2 operations
```

The inverse request produced actual Calculator-first, VS-Code-second proposals.
No host code selected or automatically executed the next app. No failures were
observed in the final 15 natural trials or six injected recovery trials.

Local synthetic-fixture evidence is retained outside Git:

- `.cache/continuation-baseline-20261115.json`: ten original baseline traces.
- `.cache/continuation-final-20261115.txt`: ten final natural traces.
- `.cache/continuation-recovery-20261115.json`: six injected recovery traces and
  five final punctuated natural traces, including paired histories.

These are diagnostic fixtures, not microphone recordings or saved user sessions.
Normal Aqua operation still persists neither speech nor conversations.

## Verification and repeat commands

**760 offline tests passed**, retaining existing speech, authorization, streaming,
history, cancellation and limit tests. `pip check` passed. New tests cover benign
negative caveats, genuine contracted refusals, explicit review state/budgets,
distinct stop reasons, repeated fresh sessions, and guarded native diagnostics.

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern test_agent*.py
.\.venv\Scripts\python.exe -B tools\test_offline.py
.\.venv\Scripts\python.exe -m pip check
# Actual local Qwen, fresh conversations, application execution disabled:
.\.venv\Scripts\python.exe -B tools\smoke_agent.py --live --request "Open VS Code and Calculator" --repeat 10 --diagnostics
```

The helper requires `--live`, bounds `--repeat` to 1–20, shows completed answer
and native-call channels, and prints the actual completion fraction. It never
prints the hidden reasoning channel. It saves no output unless the caller
explicitly redirects it.

Changed files: `src/aqua/brain/controller.py`, `tools/smoke_agent.py`,
`tests/test_agent_loop.py`, `tests/test_agent_smoke.py`, `README.md`, and this report.

## Remaining limitations

The original spontaneous hands-free failures remain unconfirmed in these fresh
trials. No new live microphone session was performed; the shared handoff is
covered by the full regression suite. Short, repeated fixtures cannot establish
general reliability across conversation histories, paraphrases, or system load.

Qwen may still return a second consent question, refuse, propose an invalid call,
or stop without a call. The host stops truthfully after the single review or a
limit. The refusal guard remains a conservative English heuristic, not a complete
semantic refusal parser. No finite word pattern covers every paraphrase; unknown
blockers can also be upheld by the model during review. A successful process
creation attempt still does not verify application readiness.

Stage 2, TTS, unrestricted filesystem/terminal access, and new desktop tools were
not implemented.
