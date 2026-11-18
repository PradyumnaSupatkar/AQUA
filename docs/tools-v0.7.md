# Aqua V0.7: one conversation with approved tools

Implemented and checked on Windows 11 with the existing Ollama 0.40.16 and
`qwen3:4b-instruct-2507-q4_K_M`. No dependencies or models were downloaded,
replaced, deleted, or edited. Whisper Small remains CPU INT8. The V0.6 audio,
wake-word, VAD, resampling, calibration, and gain implementations were preserved.

## Findings and architecture

The old policy treated punctuation and conjunctions as evidence of multiple
actions. Consequently, harmless introductions such as “Well. Open VS Code.”
could be rejected after Qwen had correctly identified VS Code. The old voice
route also returned after printing the launcher result, without putting that
result into conversational history. The conversational system prompt claimed
that Aqua had no tools. Finally, ordinary voice conversation first made a
classifier request and then a separate conversational request.

All three input modes now use the same `ConversationController`:

```text
text, or wake / VAD / Whisper
    -> Qwen /api/chat with native tool definitions
    -> streamed conversation OR complete tool proposal
    -> strict validation and independent authorization veto
    -> existing allowlisted launcher
    -> commit host tool result to bounded history
    -> Qwen explanation with further tools disabled
```

Native streaming tool calling was tested against the actual installed server
and model before choosing this implementation. The server produced structured
`message.tool_calls`, accepted paired `assistant`/`tool` history, and answered
follow-up questions using the result. A second structured classifier or JSON
fallback is therefore unnecessary. Native call batches are exposed to the
executor only after a completed stream; conflicting/incomplete fragments fail
closed. Duplicate chunks do not duplicate execution.

The old `semantic.py`, `router.py`, `intents.py`, and `smoke_semantic.py` are marked
retired. Their isolated historical regression tests remain, but no current text,
manual voice, or hands-free session routes through them. The existing voice
helper is a thin compatibility entry into the shared controller, not a second
interpreter.

## Tool contract and authorization

The only exposed tool is `open_application(app_id)`. Its argument enum and the
model's capabilities come from `actions.launcher.ALLOWED_APPS`:

- `notepad`
- `calculator`
- `vs code`
- `chrome`

The tool boundary checks the exact function name, complete framing, canonical
string ID, and an argument object containing only `app_id`. Unknown tools, extra
parameters, aliases in model arguments, arbitrary commands, executable paths,
wrong types, and multiple calls are never passed to the launcher. The existing
fixed executable registry and `shell=False` launcher are unchanged.

Qwen chooses the application. Trusted code vetoes clear contradictions such as
negated, quoted, hypothetical, conditional, unsupported, or genuinely multiple
operations. It also checks that a named target agrees with the proposed app.
Sentence count, harmless commas, polite wording, and descriptive introductions
are not blanket reasons to reject a request. Describing a program that can
“run” something does not count as launch authorization. Uncertain authorization
requires clarification rather than execution.

An implied activity registers a structured offer, without launching anything.
The next exact confirmation authorizes only that pending approved app, once.
“No thanks,” unrelated conversation, recording/transcription failure, session
controls, or cancellation invalidate the offer. A repeated or late “yes” cannot
revive it. A prose offer without a structured proposal never arms confirmation.

## Truthful state and recovery

Each retained turn is an atomic bundle: user, assistant call, matching host tool
result, and optional final assistant answer. Pruning removes whole bundles;
request/result pairs cannot become orphaned. History remains bounded by eight
turns and the configured character budget by default. The budget includes fresh
system context when preparing requests, but remains a character heuristic, not
an exact tokenizer calculation. Ollama still receives `num_ctx=4096`.

Before launching, the controller reserves enough history space for the action
record. Immediately after launching it commits the verified result, before
requesting the explanation. A subsequent timeout, protocol error, or cancelled
explanation cannot erase the action. Cancellation inside process creation is
recorded as an unknown outcome; Aqua never automatically retries it.

The terminal always displays the host's authoritative result. Success means the
launcher reported process creation, **not** that a window is visible or ready.
Failed launch results are also model-visible. Recent retained actual results
are reiterated in trusted session context. `/clear` removes conversation,
pending confirmation, and session action records together. Nothing persists
across restarts.

Failed launches use the authoritative host reply directly, skipping a generative
paraphrase. A late live check caught Qwen saying “launch attempt succeeded” after
a correctly recorded failure. An unconditional success-phrasing instruction was
removed, and failures now need no such model explanation at all. Regression
coverage proves that a contradictory queued model response is never consumed,
and the smoke checker separately rejects contradictory failure replies. No
production text-removal filter is involved.

Each model request receives the current local Windows clock in ISO format with
UTC offset and timezone name. This is trusted local context, not another model
call or an internet time lookup. A live check used
`2026-11-13T22:13:53+05:30 (India Standard Time)` and Qwen returned that value.

## Verification

The workspace initially contained 509 discoverable tests, including the recent
microphone diagnostics, rather than the older 486-test baseline in the request.
Existing cases were retained; tests describing classifier routing were migrated
to native tool events and explicitly reject calls to the retired interpreter.
New coverage checks transport framing, strict arguments, narrow safety vetoes,
one-use confirmations, all input modes, tool-result memory, cancellation,
history capacity, fresh clock context, and actual request counters.

The final complete run passed **617 tests in 2.466 seconds**, with real socket
connections, DNS lookups, and process creation blocked throughout discovery and
execution. `pip check` also passed with no broken requirements.

Run the deterministic suite with sockets and process launches blocked:

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -B tools\test_offline.py
```

Run the separate live local-model check, with a recording executor stub and an
independent process-launch guard:

```powershell
.\.venv\Scripts\python.exe -u -B tools\smoke_tools.py --live --diagnostics
```

The live check passed **16/16 turns**: direct Notepad; action recall; “Well. Open
VS Code”; a multi-sentence programming request; ambiguous programming followed
by one-use confirmation; repeated confirmation; cancellation and stale
confirmation; negation; hypothetical; destructive compound request; registry
capabilities; a simulated failed Chrome launch; and accurate failure recall.
No Windows applications were actually launched.

An earlier live pass exposed two model-behavior issues: an unregistered prose
offer instead of a structured offer, and a tool proposal for a hypothetical.
The host executed neither. Explicit protocol examples corrected the observed
behavior; three independent programming-offer checks and the subsequent full
16-turn check passed. This was a prompt/protocol correction, not authorization
inferred from prose or a weakened safety boundary. Windows console encoding in
the diagnostic script was also fixed so Unicode replies cannot abort the test.

Additional live conversation retained Carlos Sainz and Max Verstappen across
the Formula One question, preference statement, and “Tell me more about them.”
No classifier or application tool was used for those ordinary turns.
This verifies conversational recall, not factual accuracy: the longer answer
made unreliable driver-history/team claims. It took 12.403 s to complete,
illustrating both the model's knowledge limitations and the cost of long replies.

## Measurements on this laptop

These are observed local wall times, not universal performance guarantees.
Per-turn counts mean actual `/api/chat` POST attempts, including a compatibility
retry if one occurs; one-time model-availability metadata is excluded. Tool
launches below used immediate recording stubs, so actual Windows application
startup time was not measured.

| Turn / stage | Generation requests | First visible model token | Complete turn |
| --- | ---: | ---: | ---: |
| Warm text: Python in one sentence | 1 | 0.452 s | 1.781 s |
| Open Notepad, including result explanation | 2 | 1.856 s | 2.163 s |
| “Well. Open VS Code.” | 2 | 1.810 s | 2.204 s |
| Programming introduction plus explicit request | 2 | 1.730 s | 2.264 s |
| Ambiguous programming offer | 1 | n/a: host clarification | 1.327 s |
| Confirm that offer | 1 | 0.456 s | 0.787 s |
| Follow-up: what did you just open? | 1 | 0.603 s | 0.869 s |
| Simulated failed Chrome launch, host reply | 1 | n/a: host result | 1.376 s |
| Follow-up: did Chrome open? | 1 | 0.669 s | 1.457 s |
| Cancel / repeat stale confirmation | 0 | n/a | <0.001 s |

The first text measurement after a longer idle interval took 6.280 s, with its
first token at 4.883 s. No deliberate unload/cold-state control was performed;
this must not be described as a controlled cold benchmark.

For hands-free verification, an existing Windows System.Speech test helper
created a fixed “Hey Aqua, what is Formula One?” waveform **in memory**. This
helper is not a TTS product feature. It replaced only the microphone source and
fed paced frames through actual sherpa-onnx, Silero VAD, Whisper Small, the shared
controller, and local Qwen. No human microphone was recorded.

- Whisper loaded once: 1.806 s, CPU INT8.
- Collected audio: 2.324 s, including pre-roll; post-wake capture wall: 1.880 s.
- Whisper: 2.207 s; recognized “What is formula 1?”
- Qwen: **one** request; first visible token 0.411 s; complete reply 3.607 s.
- Total from wake detection through response: **7.727 s**.
- Server generation: 74 tokens in 3.195 s; zero thinking-channel chunks.

The V0.6 observations supplied with the request were 0.7–0.9 s for classification
plus 0.7–2 s for a short answer. V0.7 removes the classification request from
ordinary conversation: **two generation requests become one**. This is a clear
request-count reduction, not a controlled claim that every response is faster.
Whisper timing remains within the earlier 2.1–2.3 s observations. Tool turns
intentionally spend an additional request on explaining the verified result;
confirmed pending actions skip reclassification.

Diagnostics report stage, outcome, actual request count, wall time, first visible
model token, and available server load/prompt/generation metrics. They do not
dump raw tool arguments, hidden reasoning, or private launcher exception paths.
Server metrics describe the last completed stream; turn wall time and request
count include all generation attempts for that turn.

## Changed files

| Area | Files |
| --- | --- |
| Shared controller / history / transport | `src/aqua/brain/controller.py` (new), `conversation.py`, `ollama.py` |
| Trusted tool boundary | `src/aqua/actions/tools.py` (new) |
| Session integration | `src/aqua/chat.py`, `src/aqua/handsfree.py` |
| Retired legacy entry points | `src/aqua/actions/semantic.py`, `router.py`, `intents.py`, `tools/smoke_semantic.py` (retirement notes) |
| Version and documentation | `src/aqua/__init__.py`, `pyproject.toml`, `README.md`, this report |
| Explicit verification tools | `tools/smoke_tools.py`, `tools/test_offline.py` (new) |
| New tests | `test_controller.py`, `test_tool_history.py`, `test_tool_transport.py`, `test_tools.py`, `test_tools_smoke.py` |
| Migrated integration tests | `test_chat.py`, `test_voice_semantic_integration.py`, `test_handsfree.py`, `test_action_router.py`, `test_v032_defaults.py` |

## Limits and running Aqua

Qwen and speech recognition can still make semantic errors. The independent
guards are conservative English checks, not a proof of intent: unfamiliar
phrasing can require clarification, and model prose can still describe a
capability imprecisely. Prose never grants execution permission. The fixed
registry limits effects even when the model errs. Audio playing near an armed
microphone is not authenticated to a particular speaker. Application readiness
is not checked, history is session-only, and human wake sensitivity still needs
the existing real-microphone calibration. No TTS or new desktop tools were added.
Unresolved coordinated launch targets, including an approved app plus an unknown
one, require choosing a single app; no partial subset is launched. Harmless
conjunctions in preceding discussion do not trigger that rule.

```powershell
D:\Aqua\.venv\Scripts\python.exe -m aqua handsfree --language en --diagnostics --wake-threshold 0.15
```

Text and manual voice use the same engine:

```powershell
D:\Aqua\.venv\Scripts\python.exe -m aqua chat --diagnostics
D:\Aqua\.venv\Scripts\python.exe -m aqua voice-chat --language en --hints --diagnostics
```
