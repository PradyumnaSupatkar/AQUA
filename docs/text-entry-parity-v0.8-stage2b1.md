# Initial text-entry tool selection: chat and hands-free parity

November 15, 2026. Focused Stage 2B.1 fix. Existing real Windows text entry was
already verified by the user; the UIA writer, Whisper, wake/VAD, models,
temperature and operation limits were not changed. No real desktop input,
application launch or microphone capture occurred during this investigation.

## Verified findings

The exact controlled request was:

> Can you write down I am Aqua, your personal assistant into the open notepad window?

Both real front ends were traced. Chat strips outer whitespace and calls the
shared responder. Hands-free removes only the leading wake greeting, then calls
that same responder. Its text-entry opt-in and settings survive lazy startup.
Both reach the same ConversationController with the same current text. Language
and hints affect transcription, not the tool controller. The initial native tools
are `open_application` and `request_text_entry`; scoped observation/writing tools
become available only after accepted entry consent.

SHA-256 fingerprints over effective messages, model, GenerationSettings and
native tool schemas matched between modes for every paired scenario/trial, both
before and after changes. The clock context was fixed within each trial batch
for this comparison; settings were temperature 0.2, context 4096, thinking auto,
output limit 1024, seed unset. The installed model and local client were shared.

Three interacting causes were verified:

1. Initial capability prose said `open_application` was the only desktop tool
   and **"There are no other desktop tools"**, despite the opted-in native
   entry-authorization tool. Later instructions contradicted this blanket claim.
2. Before `request_text_entry`, the controller had no action-mode entry grant.
   Any complete plain response was committed as ordinary conversation. Thus a
   false capability denial ended immediately with one decision/zero operations;
   the post-authorization reconsideration could never run.
3. A retained assistant denial strongly anchored Qwen's later tool decisions.
   Seeded history contained a paired successful Notepad launch/result, then this
   exact request answered with `I cannot access open windows or their UI from here.`
   Both modes reproduced that denial verbatim. Fresh sessions succeeded in both.
   This establishes a history-sensitive failure reproducible through either mode,
   rather than an observed difference in model parameters or missing tools.

The supplied original session's complete history was unavailable. These findings
identify a controlled reproduction and concrete defects; they do not claim every
possible hands-free refusal has this cause.

## Small, bounded correction

The capability description now reflects the actual opt-in registry and explains
that validated entry consent enables scoped UIA tools. Its default launch-only
text remains unchanged, preserving benchmark/default-session compatibility.

If an opted-in, otherwise non-action turn returns a zero-tool denial of an
available desktop capability, the controller can spend its existing single
reconsideration. The signal examines only the **model reply**, not user commands:
it selects no app, extracts no payload, grants no consent and removes no user
text. Performative safety refusal/cancellation remains a stopping condition.

The review asks Qwen to assess the unchanged current request against the actual
native tools. A valid `request_text_entry` proposal must still pass the original
current-request app/literal validator. Unsupported/ambiguous/negated/quoted/
hypothetical/compound requests receive no entry permission merely because a
review occurred. No operation is mandatory or dispatched automatically.

Repeating the entire older failed dialogue defeated the first review attempt.
The final fallback therefore uses a **current-task-only inference view** for the
review and its subsequent native decisions. Before authorization, that view is
the current request plus trusted capability/task context. After authorization,
it contains the complete current user/tool-call/tool-result turn. Earlier turns
are not replayed as active assistant examples during that recovery.

This view does not clear or rewrite Conversation.history. Earlier user facts,
assistant replies and tool results remain in bounded session memory; normal
conversation still receives them. It is used only after the initial capability
denial, before any current-task operation. Every later current-task result is
retained verbatim in the recovered model context. Tests check both the retained
memory and complete paired current-tool history.

There is still only one reconsideration, at most five operations and six model
decisions. Spending the review before authorization leaves no second review for
later hesitations. Two zero-tool replies produce an explicit incomplete outcome
with no claimed entry or fabricated desktop-access finding. Timeout/unknown
writes remain unverified and are never retried.

## Repeated live local-Qwen results

All trials used the installed `qwen3:4b-instruct-2507-q4_K_M` on local Ollama
0.40.16. Both entry points ran their actual controller paths. Hands-free used a
simulated wake/audio/transcription fixture returning the exact request; window
observations and the value writer were also synthetic. Real processes/executors
were blocked. These results measure native tool orchestration, not microphone
accuracy or real UIA/provider behavior.

No raw artifact was overwritten. Saved JSONL files contain only fixed synthetic
trial metadata, native function names, timings and bounded reasoning-filtered
visible failure replies. They contain no microphone recording or real session
transcript. All 32 saved trial observations are retained:

- `.cache/text-entry-parity-before-20261115.jsonl` — 8 baseline observations.
- `.cache/text-entry-parity-after-20261115.jsonl` — 12 intermediate observations.
- `.cache/text-entry-parity-final-20261115.jsonl` — 12 final observations.

| Version / input mode | Fresh | Prior launch + denial | Overall |
| --- | --- | --- | --- |
| Original / chat | 2/2 | 0/2 | 2/4 (50%) |
| Original / hands-free | 2/2 | 0/2 | 2/4 (50%) |
| Corrected capability prose + full-history review / chat | 3/3 | 0/3 | 3/6 (50%) |
| Corrected capability prose + full-history review / hands-free | 3/3 | 0/3 | 3/6 (50%) |
| Final task-local recovery / chat | 3/3 | 3/3 | 6/6 (100%) |
| Final task-local recovery / hands-free | 3/3 | 3/3 | 6/6 (100%) |

Completion requires host status completed **and exactly one stubbed write**.
Every final trial selected native `request_text_entry`, `list_windows`,
`inspect_window`, `set_control_text`. Fresh tasks used five decisions/four
operations; prior-denial tasks recovered within six decisions/four operations.
No duplicate or real write occurred.

| Final configuration | Median elapsed | Observed range |
| --- | --- | --- |
| Chat, fresh | 13.910s | 13.383–14.987s |
| Hands-free, fresh | 13.432s | 13.077–14.559s |
| Chat, prior denial | 15.699s | 15.528–18.152s |
| Hands-free, prior denial | 15.798s | 15.775–16.119s |

These are end-to-end fixture times, with no real speech inference. The regression
suite overlapped part of the final run; timings are observations, not a controlled
speed comparison. This small sample does not guarantee future model success.

Additional bounded probes confirmed ordinary multi-turn Ollama requests respect
the latest question. Model/template metadata was inspected read-only. Reworded
full-history reminders and quoted history did not fix the repeated denial;
isolating only the review recovered authorization, but replaying old history on
the next decision caused another refusal. Keeping the entire recovered task in
the task-local view then completed the native workflow. Those unsuccessful
interventions were not added to production or concealed as successful trials.

## Automated verification

Nine new deterministic tests cover exact front-end parity in fresh/existing
history, initial zero-tool refusal/recovery, truthful incomplete outcomes,
genuine safety refusal, unauthorized/ambiguous requests, cancellation, limits,
single dispatch and preserved memory/current-tool history. The initial tests
failed against the previous production implementation before it was changed.

Focused checks passed. Full regression: **941 tests passed in 18.020 seconds**,
zero failures/errors (all previous 932 plus nine). `pip check`:
**No broken requirements found.** Offline tests block network/process access.
No dependencies or models were installed, removed or modified.

```powershell
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern 'test_text_entry*.py'
.\.venv\Scripts\python.exe -B tools\test_offline.py --pattern 'test_handsfree*.py'
.\.venv\Scripts\python.exe -B tools\test_offline.py
.\.venv\Scripts\python.exe -B -m pip check
```

Repeat the explicitly live, execution-disabled trial batch from the repository:

```powershell
.\.venv\Scripts\python.exe -B tools\smoke_text_entry.py --live --trials 3
```

Optional `--output NEW_PATH.jsonl` refuses to overwrite an existing artifact.
The helper reuses repository test fixtures; it requires the working checkout.

## Manual hands-free retest and limitations

Manually prepare one visible Notepad window with an empty document and leave it
idle. Start a fresh session:

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -B -m aqua handsfree --allow-text-entry notepad --language en --hints --temperature 0.2 --diagnostics
```

Say: **Hey Aqua, can you write down I am Aqua, your personal assistant into the open Notepad window?**

Check the recognized text, native results and destination. Real success requires
`set_control_text: ok; verified=True`. Use a new empty document for each distinct
entry test. A nonempty document must be protected; manually inspect any unknown
outcome before another request. No automatic retry after a possible write exists.

Real microphone/desktop hands-free entry was not performed during this work.
Speech mistakes, unsupported UIA providers, inaccessible windows and model
hesitation can still fail safely. The review trigger covers capability-denial
language, not every conceivable unhelpful plain answer. Normal conversation and
genuine refusals are not universally retried. Recovery consumes the only spare
decision, so another later no-call response stops incomplete. No UIA, keyboard,
coordinate or unrestricted action fallback was added.

## Files changed

- `src/aqua/actions/tools.py`: accurate optional capability description; unchanged
  native tool schemas/default launch context.
- `src/aqua/brain/controller.py`: bounded initial capability review, scoped
  inference view and truthful incomplete reporting.
- `src/aqua/brain/conversation.py`: optional current-task view without memory
  deletion; full current tool-result turn retained.
- `tests/test_text_entry_initial_review.py`: nine offline regressions.
- `tools/smoke_text_entry.py`: explicit execution-disabled parity trials.
- `README.md`, `docs/handsfree-text-entry-v0.8-stage2b1.md`, this report.
- Three ignored `.cache` JSONL artifacts listed above preserve trial results.

No Stage 2B.2 work was started.
