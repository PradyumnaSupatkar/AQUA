# Aqua v0.8 detailed reference

> This is the complete former project README as of v0.8.0 (commit `14ee833`), kept unchanged except that
> its relative links were adjusted for this folder. It is the detailed reference for every command, option,
> model setup step, measurement and historical test count. For an overview, see the [main README](../README.md);
> for a version summary, see the [changelog](../CHANGELOG.md).

---


**Stage 2B.1 focused recovery:** optional blank-field text entry is now connected
to Qwen's existing tool loop. It is disabled by default and initially allowlisted
to Notepad. The mechanism uses generic UIA Edit/Document controls with writable ValuePattern, not a
Notepad script. [Implementation, security limits and manual verification](text-entry-v0.8-stage2b1.md).

For a manually authorized test, first open exactly one **empty** Notepad window
yourself. Then run one of these commands:

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -m aqua chat --allow-text-entry notepad --temperature 0.2 --diagnostics
# Type: write Hello from Aqua into my open Notepad window.
.\.venv\Scripts\python.exe -m aqua handsfree --allow-text-entry notepad --language en --hints --temperature 0.2 --diagnostics
# Say: Hey Aqua, write Hello from Aqua into my open Notepad window.
```

Do not edit the target during the request. A real write occurs only if UIA exposes
a unique eligible non-password Edit or Document supporting writable ValuePattern and its
value is empty at the final checks. Nonempty/unsupported/ambiguous destinations
are rejected; there is no coordinate, keyboard, clipboard, invoke, or replacement
fallback. A timeout or unverifiable result is reported as unknown, without retry.
The implementation has **974 passing tests**. The user verified a real typed-chat
Notepad entry with `set_control_text: ok; verified=True`. This hands-free
follow-up used simulated speech/UI results and performed no real desktop input.
Modern Notepad's Document qualifies through writable ValuePattern; TextPattern
is used only by the separate read-only diagnostic, never as a writing API.
[Verified diagnosis, timings and remaining uncertainties](notepad-diagnosis-v0.8-stage2b1.md).
[Hands-free authorization and continuation fixes](handsfree-text-entry-v0.8-stage2b1.md).
[Initial tool-selection/history reliability and repeated parity trials](text-entry-parity-v0.8-stage2b1.md).

An initial unsupported claim of no desktop access can receive one bounded
capability review. That recovery uses the exact current request and its complete
tool-result turn; older session history stays in memory and remains available to
normal conversation. Qwen must still propose each tool and pass every existing
authorization and UIA check. Final fixture trials completed 6/6 in chat and 6/6
in hands-free, including prior-refusal history; no real desktop input occurred.

Current desktop inventory questions such as **"What applications can you currently
see open on my desktop?"** now require fresh scoped window metadata. The report
covers approved apps only and never treats old conversation as current evidence.
Missing/failed reads explicitly leave the state unknown.

With `--allow-text-entry notepad`, **"Open Notepad and write Hello from Aqua into
it."** can authorize one same-app launch plus exact literal entry. Qwen chooses
every tool; the full chain fits five operations/six decisions. A private native
handle baseline inside the launch security check excludes pre-existing windows
(including minimized ones) from the subsequent entry target. App window reuse,
restored nonempty documents, slow startup or truncated discovery stop entry.
No save, new-document invocation or forced-window fallback exists.
[Verified causes, budget, tests and safe manual commands](desktop-tasks-v0.8.md).

Explicit spoken forms also include `write down TEXT into my open Notepad window`
and `So, can you write down TEXT into the open Notepad window?`. A single comma
may separate a supported command phrase from its literal, for example
`Write down, I am Aqua, your personal assistant into the open Notepad window.`
The separator is optional; commas/case inside the literal must match exactly.
The command boundary is fixed before matching text, so an incomplete
`Write down into my open Notepad window` cannot reinterpret `down` as the payload.
Quote a literal such as `"down"` explicitly when that word is the intended text.
Literal text
must match the current transcript exactly. Incomplete/ambiguous speech such as
`Right` provides no entry authorization. Earlier conversation cannot fill in
missing text or a destination. Prepare a new empty document manually before
each separate writing test; an existing nonempty document is protected.

Repeat the read-only pattern/empty-state probe without Qwen or any UI input:

```powershell
.\.venv\Scripts\python.exe -B tools\diagnose_notepad.py --live --trials 3
```

Aqua is a local, privacy-first Windows 11 assistant for Python 3.12. **V0.8 Stage 2A adds scoped, read-only Windows UI Automation observation to the bounded Stage 1 agent.** Qwen chooses each tool step using real previous results; trusted Python validates permission and reports evidence. A user task permits at most five tool operations and six model decisions. The default remains `qwen3:4b-instruct-2507-q4_K_M`, with Whisper Small on CPU INT8 and offline "Hey Aqua" activation. Ordinary replies stream as text, and session history stays in bounded process memory. Aqua saves no conversations, transcripts, microphone audio, or UI snapshots.

## Read-only desktop observation (Stage 2A)

Name an approved application in an explicit observation request, such as **"What can you see in my VS Code window?"** Aqua lets Qwen choose `list_windows()` and `inspect_window(window_id)`, then describes actual returned evidence. Only Notepad, Calculator, VS Code, and Chrome are eligible. A bare app mention or unrelated conversation grants no observation permission. An unscoped "what can you see?" asks for clarification without scanning UIA.

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -m aqua chat --temperature 0.2 --diagnostics
# Type: What can you see in my VS Code window?
.\.venv\Scripts\python.exe -m aqua handsfree --language en --hints --temperature 0.2 --diagnostics
# Say: Hey Aqua, what can you see in my VS Code window?
# Explicit real-model/UIA check with launches blocked (the app must already be open):
.\.venv\Scripts\python.exe -B tools\smoke_observation.py --live --app "vs code"
```

`List my VS Code windows` authorizes window metadata only. Interface/control requests additionally authorize a bounded accessibility-tree read. Permission ends with the task; opaque window references can survive within the session but always require new consent and identity validation. Minimized, cloaked, unrelated and identity-unverified windows are excluded. Inspection never reads password/input values or document/editor body text.

Stage 2A uses optional **pywinauto 0.6.9** with its native UIA interface in a hidden, killable Python worker. The dependencies were installed here with approval. Another Windows installation can add the optional extra after dependency approval:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]" --disable-pip-version-check
```

Stage 2A exposes no clicking, typing, keyboard, screenshot, screen-vision, focus, or window-manipulation tools. Its read-only behavior remains unchanged. The separate Stage 2B.1 opt-in above enables only guarded empty-value entry. Observation cannot authorize an application launch. UI text stays untrusted tool data, and Qwen selects evidence IDs rather than supplying unverifiable UI claims. Facts are rendered from the actual returned properties. All processing remains local; missing dependencies never install themselves.

The final live VS Code check discovered and inspected its window in **0.616s / 0.619s**. The bounded tree exposed window chrome and buttons, not a full editor interface. Qwen can still stop early: if an interface task only obtains a window title, Aqua reports it as incomplete. See the [Stage 2A implementation, actual results, bounds, files and limitations](desktop-observation-v0.8.md).

V0.3.1 diagnosed conversational latency and added detailed stage timings. The retained `qwen3:4b` was verified as Thinking-2507, not the original hybrid variant. The separate official Instruct-2507 model was installed with explicit approval and subsequently passed live conversational and recall tests. **V0.3.2 changes the default configuration only; it does not download, modify, or delete installed models.** The [V0.3.1 investigation](latency-v0.3.1.md) remains a historical report: its statement that Thinking was still the configured default describes that earlier stage.

The speech-recognition milestone remains finalized from V0.2.2: multilingual **Whisper Small** is the default, Base remains available, and recognition uses **CPU INT8**, real 16 kHz preprocessing, and in-memory audio. Vocabulary hints remain opt-in. Historical V0.2.2 validation passed **136 tests**; those tests are retained alongside V0.3 coverage.

V0.7 replaces V0.5's separate classifier request with one shared conversation controller. Native streaming tool calls were verified against this installed Ollama 0.40.16/Instruct combination. Ordinary conversation uses one model request; approved execution results are recorded before a final explanation. The existing V0.6 wake, VAD, and microphone calibration paths are preserved. See the [V0.7 architecture, verification, and performance report](tools-v0.7.md).

Stage 1 reuses only the four existing approved app launches. It adds no project inspection, test execution, terminal/filesystem access, TTS, or new tools. See the [Stage 1 implementation and verification](agent-v0.8-stage1.md). Use the benchmark's recommended temperature explicitly; generation defaults and installed models have not been changed:

```powershell
.\.venv\Scripts\python.exe -m aqua chat --temperature 0.2 --diagnostics
# Example task: Open Notepad and Calculator.
.\.venv\Scripts\python.exe -m aqua voice-chat --language en --temperature 0.2 --diagnostics
.\.venv\Scripts\python.exe -m aqua handsfree --language en --temperature 0.2 --diagnostics
```

## Hands-free mode (V0.6)

The approved compact wake model and sherpa-onnx dependency are installed in this workspace. Run:

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -m aqua handsfree --language en --diagnostics
```

Wait for `Listening for "Hey Aqua"...`, then say **"Hey Aqua, open Chrome"** or say **"Hey Aqua"**, pause briefly, and speak a command within five seconds. Speak normally; no Enter is required. Wait for Aqua to return to listening before starting another turn. Typed `/clear`, `/help`, and `/quit` remain available; Ctrl+C cancels a capture or exits while idle. `voice-chat` remains the Enter-to-record fallback.

The CPU keyword detector runs acoustically while idle, without Whisper or Qwen. A single microphone stream is continuously resampled from its native rate to 16 kHz. After wake, the bundled Silero VAD ends a turn after 800 ms of silence, waits up to five seconds for speech, and discards an utterance that reaches the 15-second cap. Pre-roll defaults to 500 ms. Audio during transcription/response processing is discarded; stale audio cannot become a later turn. Models are loaded once per session, and queues/audio buffers are bounded.

Supported tuning includes `--wake-threshold`, `--wake-score`, `--wake-cooldown`, `--end-silence`, `--initial-timeout`, `--max-utterance`, `--pre-roll`, and `--vad-threshold`. Existing microphone, hints, speech-model, and generation options remain available. Hints and gain remain opt-in. Use `handsfree --help` for defaults.

Exact **"Hey Aqua"** was detected using the actual keyword model on in-memory Windows voice fixtures, including continuous and paused speech. The separate 10-second ambient microphone checks had no wake events. These checks **do not establish human microphone wake accuracy or a long-term false-alarm rate**. See [V0.6 setup, verification, measurements, and limitations](handsfree-v0.6.md) for the full testing sequence and the observed synthetic Whisper transcription error.

On another installation, add the optional `handsfree` extra only after dependency approval, then explicitly approve/install the five pinned wake assets. Normal hands-free execution never downloads anything:

```powershell
# Explicit installation operations, only when separately approved:
.\.venv\Scripts\python.exe -m pip install -e ".[handsfree]" --disable-pip-version-check
.\.venv\Scripts\python.exe -m aqua setup-wake --download
# Local verification only (no download):
.\.venv\Scripts\python.exe -m aqua setup-wake
```

## Start a local conversation

Start the existing Ollama Windows application or local service first, then use the current virtual environment:

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -m aqua chat --diagnostics
.\.venv\Scripts\python.exe -m aqua voice-chat --language en --diagnostics
```

Both commands now select Instruct-2507 automatically. To select the retained Thinking model explicitly:

```powershell
.\.venv\Scripts\python.exe -m aqua chat --model qwen3:4b --diagnostics
.\.venv\Scripts\python.exe -m aqua voice-chat --model qwen3:4b --language en --diagnostics
```

The Thinking model remains available for deliberate reasoning and comparison; it is no longer the default. CLI output uses UTF-8, including redirected Windows output, so emoji and non-English replies do not abort a turn or lose its history.

All conversational inference connects only to **`http://127.0.0.1:11434`**. Text/manual voice check that the model is installed before starting; hands-free checks lazily after an awakened, reliable transcript. There is no remote-host option, proxy routing, redirect following, model pull, or cloud fallback. If Ollama is unavailable, start it and retry. If the selected model is missing, select another already-installed model explicitly with `--model`; Aqua does not download or silently switch models. Native tool compatibility has been checked with the default Instruct model; a different installed model can have different protocol behavior.

In `chat`, type a message and press Enter. Aqua streams the reply, then accepts the next message without restarting. These controls are typed at the input prompt in either chat mode:

- `/help` shows the controls.
- `/clear` removes conversation/tool-result history and pending confirmations for the current session.
- `/quit` exits. EOF or Ctrl+C at the input prompt also exits cleanly.

Ctrl+C during generation closes the stream. Incomplete ordinary replies are not committed; an already displayed fragment can remain in terminal scrollback. If a tool has already run, its verified result remains recorded even if the final explanation is interrupted or fails. Cancellation cannot undo a process-creation attempt.

## Voice-to-text chat

`voice-chat` loads Whisper Small once per session and reuses it. At each turn prompt, **press Enter to record five seconds**, speak a short sentence, and wait for recognition. Aqua shows the recognized text, sends acceptable speech to the local LLM, streams a text reply, and returns to the next-turn prompt. It does not speak the response aloud. Type `/quit` at the prompt to leave.

```powershell
# Current Windows default microphone, Small on CPU, English recognition:
.\.venv\Scripts\python.exe -m aqua voice-chat --language en
# Optional spelling hints; they remain off unless requested:
.\.venv\Scripts\python.exe -m aqua voice-chat --language en --hints
.\.venv\Scripts\python.exe -m aqua voice-chat --hints --hotwords "Aqua, Python, CTranslate2"
# Separate LLM selection from speech-model selection:
.\.venv\Scripts\python.exe -m aqua voice-chat --model qwen3:4b --speech-model base --language en
```

`--model` names the installed **Ollama** model in both chat commands. For `voice-chat`, use `--speech-model {base,small}` or `--speech-model-dir PATH` for the local **Whisper** model; these two speech selection options are alternatives. The older `transcribe --model base` command continues to refer to Whisper, as documented below.

Voice chat also supports `--device ID`, `--sample-rate HZ`, `--gain-db DB`, `--language CODE`, `--diagnostics`, `--hints`/`--no-hints`, and `--hotwords TEXT`. Custom hotwords require `--hints`. With no `--device`, it uses the current Windows default input rather than a hardcoded index. Gain stays at **0 dB** unless explicitly changed.

Empty or unreliable recognition is skipped instead of sent to Ollama. Checks consider available VAD speech, lost or overflowed capture samples, excessive clipping, and available recognition confidence/compression measurements. They are conservative heuristics: they can reject valid speech or accept a transcription error. Repeat a skipped turn and use diagnostics to investigate; no recognition-confidence value proves the text is correct.

## Natural application tools in every conversation mode

Text, manual voice, and hands-free input share the same model, authorization, and history implementation:

```powershell
.\.venv\Scripts\python.exe -m aqua chat --diagnostics
.\.venv\Scripts\python.exe -m aqua voice-chat --language en --diagnostics
.\.venv\Scripts\python.exe -m aqua handsfree --language en --diagnostics
```

For launch tasks, Qwen receives the native tool definition **`open_application(app_id)`**. Its canonical IDs and capability description come directly from the launcher's authoritative `ALLOWED_APPS` registry: `notepad`, `calculator`, `vs code`, and `chrome`. The model never chooses an executable path. The executor rejects unknown tools, noncanonical IDs, extra parameters, malformed arguments, and model-generated paths or commands; only the existing fixed launcher uses `shell=False`. Explicit observation tasks instead expose only the scoped read-only tool definitions described above.

Natural explicit requests include "Well. Open VS Code", "Can you open VS Code for me?", "I'd like to work on Aqua. Please launch VS Code", and "Could you bring up my browser?". "Open Notepad and Calculator" now authorizes both named approved apps. Qwen chooses one app per decision and receives its actual result before choosing another; Python never generates a conversational workflow. Independent checks validate consent against the original request before each effect.

For an activity such as "I feel like programming", Qwen proposes the relevant app through the same tool interface and the host asks for confirmation without launching. "Yes, please" or "Yes, go ahead" consumes only that pending app, once. "No thanks" cancels it. Unrelated conversation, rejected/failed speech, cancellation, controls, or `/clear` disarm a suggestion. A late or repeated confirmation cannot execute it, and confirming a different app is rejected. Speech confirmations on a new hands-free turn still require the wake phrase.

Negation, quoted/reported instructions, hypothetical or conditional launches, and unsupported operations cannot authorize execution. "Don't open Chrome", "Open PowerShell", "Open Chrome and delete my files", and "Open Chrome to delete my files" execute no partial subset. Multiple approved app launches are permitted when explicitly authorized. Tool calls are buffered until the entire response completes; a malformed, truncated, interrupted, or parallel multiple-call proposal executes nothing from that decision.

When execution occurs, a structured host-authored tool result records the app, success/failure, whether process creation was reported, and that application readiness was not checked. Every result enters the same model-visible user-turn bundle before another decision. Follow-up questions receive those facts, including failures. Available tool arguments shrink to authorized, unattempted apps. Duplicate attempts, including retries after failure or an unknown outcome, are blocked. A successful launch attempt is not proof that a window is ready or responding.

For explicitly authorized action tasks, Aqua buffers model prose and displays host-authored outcomes based on recorded effects. An invented "all done" cannot replace a failed, unknown, or unattempted operation. The model can stop early; Aqua reports the task as incomplete. A failed single-app launch stops with the host result. After a failure in a multi-app task, Qwen can choose another still-authorized app, without retrying the failed one.

Typical logical model-decision counts after preflight are:

| Turn | Requests |
| --- | ---: |
| Ordinary conversation/capability question | 1 |
| Explicit app request, including its result explanation | 2 |
| Two sequential authorized app launches, then stop | 3 |
| Failed explicit app launch, authoritative host reply | 1 |
| Ambiguous app proposal and confirmation question | 1 |
| Accepted confirmation, including its result explanation | 1 |
| Exact cancellation or stale confirmation | 0 |

An accepted confirmation whose launch fails needs no model request. A task stops after at most five tool operations or six model decisions. There are currently only four approved apps and each can be attempted once per task. Compatibility transport retries are included in the separate HTTP request count, not counted as new semantic decisions.

If a model stops while an explicitly authorized task still has unattempted work,
Aqua permits one bounded reconsideration within those same limits. It supplies
the real progress and remaining scope; every effect still requires a new valid
model tool call and authorization. Refusals, cancellation, rejected requests, and
reached limits do not trigger recovery. A second stop remains incomplete. See
the [premature-stop correction](agent-v0.8-stage1.md#premature-stop-reliability-correction).

There is no separate intent-classifier call. `--diagnostics` reports model request count, task decisions/operations/status, outcome, failure stage, visible-token/total response timing, and available server metrics. An action summary has no first streamed model token; its reported value can be `n/a`. Model errors or malformed proposals produce a safe stop/clarification rather than an invented application result. The default context, generation settings, and five-minute keep-alive remain unchanged. Aqua reuses the session client and installed models without downloads; Ollama can still reload a model after eviction or keep-alive expiry.

An optional native-tool smoke check exercises 16 fixed conversation turns against local Qwen **without launching applications**:

```powershell
.\.venv\Scripts\python.exe -B tools\smoke_tools.py --live --diagnostics
```

It requires `--live` before any request, checks the installed model once, and uses the real controller with a recording executor stub plus an independent process-launch guard. It checks direct/polite requests, confirmations and cancellation, unsafe proposals, capabilities, successful-action recall, and failed-launch recall. Actual replies, request counts, and timings are printed; unexpected proposals fail the check even if the policy safely rejects them. No microphone, audio files, persistent history, or downloads are involved. The old standalone semantic classifier is retired from all conversation input paths.

The separate Stage 1 check demonstrates model-chosen steps, including an optional simulated failure. These commands contact only local Qwen and **never launch applications**:

```powershell
.\.venv\Scripts\python.exe -B tools\smoke_agent.py --live
.\.venv\Scripts\python.exe -B tools\smoke_agent.py --live --fail-first
# Repeated fresh-session fixture with actual native-call diagnostics:
.\.venv\Scripts\python.exe -B tools\smoke_agent.py --live --request "Open VS Code and Calculator" --repeat 10 --diagnostics
```

Speech and model interpretation can still be wrong; conservative authorization checks can require clarification. These tests do not prove every paraphrase, accent, or background-media case safe. The fixed execution boundary remains authoritative regardless of a model's prose.

## Context and generation settings

All three input modes maintain the same bounded in-memory conversation format. A retained turn contains the original user request, every paired assistant tool proposal/verified result, and the final answer. At most eight user turns are retained by default, and pruning removes whole bundles so calls/results cannot become orphaned. The complete active task must fit before the next effect; its earlier steps are never dropped to make room for another operation. Nested messages are copied rather than shared with callers. Ordinary incomplete answers are not committed; an oversized plain exchange can be omitted. Verified results survive cancellation or a failed later decision. `/clear` forgets conversation, pending authorization, task progress, and current-session action results without modifying model files or Ollama configuration.

Each model request receives a fresh value from the Windows local system clock, including date, time, timezone name, and UTC offset. Qwen uses that trusted context for current date/time questions rather than guessing; no clock tool or internet lookup is needed. The value reflects the Windows clock and timezone settings.

| Option | Default | Purpose |
| --- | --- | --- |
| `--model` | `qwen3:4b-instruct-2507-q4_K_M` | Finalized non-thinking conversational model; `qwen3:4b` remains optional |
| `--temperature` | `0.5` | Response sampling temperature |
| `--max-tokens` | `1024` | Per-response token budget, including any internal thinking |
| `--context-size` | `4096` | Ollama context-window setting for these requests |
| `--max-turns` | `8` | Maximum retained user-turn bundles, including paired tool messages |
| `--history-chars` | `12000` | Character budget including system context, clock/capabilities, messages, and tool data |
| `--timeout` | `120` seconds | Local request/stream timeout |
| `--diagnostics` | off | Display available stage timings |

```powershell
.\.venv\Scripts\python.exe -m aqua chat --temperature 0.5 --context-size 4096 --diagnostics
.\.venv\Scripts\python.exe -m aqua chat --max-turns 4 --history-chars 6000
.\.venv\Scripts\python.exe -m aqua chat --help
.\.venv\Scripts\python.exe -m aqua voice-chat --help
```

The finalized request defaults are **4096 context tokens**, **temperature 0.5**, and **five-minute keep-alive (`5m`)**. They are defined centrally with the conversational model default in `aqua.brain.ollama`; keep-alive is a request setting rather than a new CLI option. The character limit bounds retained text and serialized tool data; it is a heuristic rather than an exact tokenizer budget and can differ from Ollama's context-token limit. Generation settings do not rewrite Ollama configuration. Whisper remains on CPU INT8; Ollama uses its existing device configuration, without Aqua forcing GPU inference or duplicating Whisper models per turn.

## Thinking compatibility and responsiveness

The installed Ollama reports version `0.40.16`. V0.3.1 matched the retained `qwen3:4b` manifest digest to the official **Qwen3-4B-Thinking-2507** variant, which [supports thinking mode only](https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507). This corrected the earlier suspicion that the problem was only an older hybrid-model template. Passing `think:false` could move reasoning into unmarked answer content, while `/no_think` and assistant prefill did not establish supported non-thinking operation for these weights. When that optional model is selected, Aqua keeps its separate answer/thinking channels and never displays or retains `message.thinking`.

For models that genuinely support disabling through a recognized generation template, Aqua can request `think:false`; it does not rewrite model-produced prose. The separately approved Instruct-2507 variant is [non-thinking by design](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) and emitted zero thinking chunks in the local checks. This Ollama version incorrectly advertises a thinking capability for that variant's architecture, so Aqua recognizes its verified immutable official manifest digest rather than guessing from its name. No installed model or template is modified.

The retained `qwen3:4b` therefore **still thinks internally**. Internal reasoning consumes part of its token budget and delays the first visible answer; hiding it cannot remove that work. A token-limit exit is reported as incomplete: an ordinary partial answer is not committed, while an already verified tool result remains recorded. Aqua does not remove guessed reasoning from prose or delete tags inside legitimate explanations and code. A structural guard rejects an answer that begins with a raw thinking-channel marker before displaying it. Unknown models remain conservative. Aqua requests concise answers and never executes generated command text.

With `--diagnostics`, chat reports time to the **first visible response text** and stream completion, plus Ollama's loading, prompt-evaluation and generation durations, token counts and generation throughput. Channel timing distinguishes first generated/thinking chunks from the first answer; the observed thinking-to-answer interval is not an exact server-side thinking duration. Thinking chunk/character counts contain no reasoning text and are not hidden-token counts. Voice chat also reports capture wall time, audio duration, transcription, and the one-time Whisper load. The keep-alive remains `5m`, the context remains 4096 tokens, and the sampling defaults are unchanged. Cold loads and longer output still cost time; see the report for measured comparisons and caveats.

For manual checks, try two related text turns, ask a follow-up that depends on the first, run `/clear`, and ask again. In voice chat, repeat the same pattern with a deliberate Enter-to-record turn, then test silence and `/quit`. Use the displayed timings and actual replies rather than assuming accuracy or latency improvements.

The user's live microphone tests confirmed correct conversational answers and multi-turn recall with Instruct-2507. Reported local timings were:

| Model state | First visible token | Complete response |
| --- | ---: | ---: |
| Warm | 0.222 s | 0.678 s |
| Cold | 3.756 s | 5.036 s |

These are user-reported live observations, not new automated measurements or guaranteed latencies. Cold model loading, prompt length, answer length, and system load still affect responsiveness. Whisper Small remains the speech-recognition default.

Historical V0.3 validation passed **227 automated tests**, including all **136 original speech tests**, with Python network connections blocked. Dependency checks also passed. A real two-turn text session with the installed `qwen3:4b` retained a fact and recalled it on the follow-up. A separate end-to-end voice fixture used actual Whisper Small and actual Ollama inference, loaded Whisper once, and kept the fixture in memory. It did not substitute a mocked LLM response.

Historical V0.3 timings on this laptop (`temperature=0`, 4096-token context, 1024-token generation budget; separate checks rather than a controlled benchmark):

| Check | First visible Ollama text | Complete response |
| --- | ---: | ---: |
| Text turn 1 | 20.997 s | 21.176 s |
| Text turn 2, with context | 18.036 s | 18.118 s |
| Voice fixture turn | 10.069 s | 10.106 s |

The in-memory voice fixture took **2.256 s** to transcribe and **2.158 s** to load Whisper once. Its capture stage was simulated and is not a microphone wall-time measurement. A separate capture-only test on the Windows default Realtek input measured **5.606 s wall time for 5.000 s of 44,100 Hz audio**, with no overflow; those samples were discarded without transcription or an LLM request. Real spoken utterances, system load, model residency, and the amount of private model thinking will change these numbers. No transcripts, conversations, or raw audio were persisted by the application or validation harness.

Historical V0.3.1 paired warm checks at context 4096 measured **3.624 s vs 0.054 s** to the first arithmetic answer for Thinking vs Instruct, and **7.518 s vs 0.161 s** for a context-recall answer. Both models answered correctly. Instruct generated 9 and 3 total tokens in those cases rather than 99 and 198; the improvement came mainly from avoiding hidden generation, not additional GPU offload. Brief real conversational checks at the existing sampling default passed recall, `/clear`, a two-sentence technical explanation, and action-limit honesty. They are not a broad accuracy benchmark. A real local voice-fixture check with Instruct measured 2.156 s transcription, 0.143 s first LLM text, and 0.467 s LLM completion. **That version passed all 248 automated tests**, including the original 227; no dependencies were added.

## Install dependencies

From PowerShell, use the existing virtual environment:

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -m pip install -e . --disable-pip-version-check
```

The direct dependencies remain `sounddevice 0.5.6`, `faster-whisper 1.2.1`, `numpy 2.5.4`, and `av 19.0.2`, with V0.6's optional `handsfree` extra described above. **V0.7 and V0.8 Stage 1 add no dependencies**: their tool transport, controller, task ledger, and local clock use the Python standard library. Installation also installs the existing speech dependencies, including CTranslate2 and ONNX Runtime. It does not install PyTorch, CUDA, Ollama, or a model. Dependency installation may require internet access; normal operation uses only local inference and the fixed loopback Ollama service.

## Existing local models and Base setup

Both Small and Base are installed in this workspace. Ollama retains the original `qwen3:4b` and the separate, explicitly approved Instruct-2507 model added for the V0.3.1 benchmark (approximately 2.50 GB). No pre-existing model was modified or replaced. Inference stays local; Aqua itself never pulls a model. A clean installation must provide the chosen models before starting recognition or chat.

The legacy `setup-model` command remains **Base-only**, separate from the Small recognition default. Check the retained Base model without making network requests:

```powershell
.\.venv\Scripts\python.exe -m aqua setup-model
```

If Base is missing, this reports the setup command. Only for a separately approved Base installation, the existing command is:

```powershell
.\.venv\Scripts\python.exe -m aqua setup-model --download
```

This explicit setup step downloads only the four runtime files for [Systran/faster-whisper-base](https://huggingface.co/Systran/faster-whisper-base/tree/ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66), pinned to revision `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`:

- `model.bin`
- `config.json`
- `tokenizer.json`
- `vocabulary.txt`

The default recognition directory is `D:\Aqua\models\faster-whisper-small`; the Base-only setup target remains `D:\Aqua\models\faster-whisper-base`. Hugging Face's shared cache is configured under `D:\Aqua\.cache\huggingface`, and download metadata stays inside each model directory's `.cache` subdirectory. A complete model is reused without another download. These paths are excluded from Git. Aqua accesses the installed conversational model through Ollama's loopback API, without editing its model files.

On this installation, V0.2 added about **428 MB on D:**: 148 MB for the model and 280 MB for added environment files. These are logical file sizes (about 408 MiB total); filesystem allocation and pip's separate download cache can add overhead. No second model copy is required.

To use a different local Base directory with that legacy installer, supply the same path to both commands:

```powershell
.\.venv\Scripts\python.exe -m aqua setup-model --download --model-dir D:\Aqua\models\base-copy
.\.venv\Scripts\python.exe -m aqua transcribe --model-dir D:\Aqua\models\base-copy
```

Use an existing model path instead of creating another copy when conserving disk space. `transcribe` never downloads a model automatically: it checks the local files, uses a local path with `local_files_only=True`, and enables offline operation. A missing or incomplete model produces an error before recording. If Small is unavailable, select the installed Base explicitly with `transcribe --model base`; Aqua never silently switches models.

## Select a local speech model

Small is the default for speech recognition and hint comparisons. Named models map to these directories:

| Option | Local directory |
| --- | --- |
| `--model base` | `D:\Aqua\models\faster-whisper-base` |
| `--model small` (default) | `D:\Aqua\models\faster-whisper-small` |

```powershell
# Use the default multilingual Small model:
.\.venv\Scripts\python.exe -m aqua transcribe
# Select the retained Base model explicitly:
.\.venv\Scripts\python.exe -m aqua transcribe --model base --language en
```

`--model` and `--model-dir` are alternatives; use one at a time. Both use strictly local files and CPU INT8 inference. A missing Small model produces a clear error before the microphone opens or any network request is made, explaining how to use `--model base`. It does not trigger a download or fallback. `setup-model --download` remains a **Base-only** setup command; do not point it at a Small directory.

Small was installed separately in this workspace after explicit approval, using [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small/tree/536b0662742c02347bc0e980a01041f333bce120), pinned to revision `536b0662742c02347bc0e980a01041f333bce120`. Its four runtime files were verified against the official sizes and content hashes, and actual local fixture inference succeeded with Python DNS/connect calls blocked. That earlier installation added **486,213,875 bytes** (about 486 MB / 464 MiB), including download metadata. At that installation step, no packages or source code changed, all 11 Base files retained identical SHA-256 hashes, and Base was still the default. V0.2.2 now selects Small by default without changing either model's files.

## Choose and test a microphone

```powershell
.\.venv\Scripts\python.exe -m aqua devices
.\.venv\Scripts\python.exe -m aqua mic-test
```

The device list shows each input's index, name, host API, channel counts, and default sample rate. `*` marks the configured default input. Add `--all` to include output devices. With no `--device`, Aqua uses the current Windows default microphone. The recording command identifies the selected input.

The working laptop input is `Microphone Array (Realtek(R) Audio)`, reported as the Windows default at device 0 during the live V0.2 test. Aqua does not hardcode that index: rerun `devices` whenever inputs change.

Choose an input by its current index, enable diagnostics, or override its sample rate:

```powershell
.\.venv\Scripts\python.exe -m aqua mic-test --device 1 --diagnostics
.\.venv\Scripts\python.exe -m aqua mic-test --device 1 --sample-rate 48000
```

Replace `1` with an input index shown on your computer. IDs can change when devices are connected or disconnected. Without an override, Aqua uses the selected device's default rate. It captures five seconds of mono signed 16-bit PCM in memory.

Peak and RMS levels appear live in an interactive terminal and in the final report, as percentages of digital full scale and dBFS. Redirected output contains the final report only. Digital silence is `-inf` dBFS. Aqua warns about silence, clipping, and input overflow. With overflow, five seconds of captured samples may span a longer period because some input was lost. Press Ctrl+C to cancel.

## Transcribe five seconds offline

With the installed Small model, disconnect from the internet if desired and run:

```powershell
.\.venv\Scripts\python.exe -m aqua transcribe
.\.venv\Scripts\python.exe -m aqua transcribe --device 1 --language en --diagnostics
```

Speak a short sentence during the five-second recording. Aqua prints the recognized text. The multilingual model detects the language by default; `--language en` fixes English and can help with very short input. `--sample-rate HZ` is also supported.

## Try vocabulary hints

Hints remain **off by default**. Enable the default vocabulary with `--hints`, or explicitly disable it with `--no-hints`; both commands below use Small:

```powershell
.\.venv\Scripts\python.exe -m aqua transcribe --language en --no-hints
.\.venv\Scripts\python.exe -m aqua transcribe --language en --hints
```

The default vocabulary is Aqua, Ollama, Qwen, Python, VS Code, GitHub, Whisper, and ChatGPT. To substitute your own hint text, use `--hotwords` with `--hints`:

```powershell
.\.venv\Scripts\python.exe -m aqua transcribe --language en --hints --hotwords "Aqua, FastAPI, CTranslate2"
```

Hints are context for the recognizer, not a list of words it must output. Aqua returns the model's transcript without replacing names or inserting vocabulary terms. Hints can help or harm a result, can bias unrelated speech, and do not guarantee a spelling or an accuracy improvement. Include unrelated sentences when evaluating them.

The installed Faster-Whisper 1.2.1 API puts `hotwords` into prior prompt context. A nonempty `prefix` prevents hotwords from taking effect, and `initial_prompt` separately consumes previous-context tokens. Aqua supplies only `hotwords`, without a prefix or initial prompt. Faster-Whisper retains at most the first 223 hotword tokens for this decoder context, so keep custom lists short. Whitespace is normalized; blank text and non-whitespace control characters are rejected.

## Compare the same recording

Use `compare` to capture five seconds once and run Small without hints, then Small with hints, on exactly the same in-memory PCM audio:

```powershell
.\.venv\Scripts\python.exe -m aqua compare --language en
.\.venv\Scripts\python.exe -m aqua compare --language en --diagnostics
.\.venv\Scripts\python.exe -m aqua compare --language en --hotwords "Aqua, FastAPI, CTranslate2"
# Compare hint settings on Base instead:
.\.venv\Scripts\python.exe -m aqua compare --model base --language en
```

Comparison accepts the same `--device`, `--sample-rate`, and `--gain-db` capture/input options as transcription. It has both hint conditions built in, so a custom `--hotwords` value does not need `--hints`. Read a sentence containing names or technical terms, then repeat with ordinary unrelated speech. The output shows both actual transcripts; Aqua does not label either one more accurate automatically.

The report separates model-loading time from each transcription pass, shows the captured audio duration, and reports **real-time factor (RTF)** as transcription seconds divided by captured audio seconds. An RTF below 1 means the pass processed audio faster than its recorded duration. Transcription timing includes preprocessing, VAD, decoding, and complete consumption of the text generator; model loading and recording are excluded. Each model is loaded once and reused for its passes. This is a small comparison tool, not a controlled benchmark: first-pass caches, run order, and other system activity can affect timing.

For the preserved four-pass Base-versus-Small comparison, use `--include-small`:

```powershell
.\.venv\Scripts\python.exe -m aqua compare --include-small --language en --diagnostics
```

This runs Base without/with hints and Small without/with hints on the same recording. `--model` and `--include-small` are alternatives; the latter retains the existing four-pass workflow. Aqua checks every requested local model before recording. If Small is missing, it exits without capturing a sentence, downloading anything, or silently falling back. Use `compare --model base` for Base-only hint comparison when needed. Small is installed in this workspace; other installations must provide the model locally.

A historical manual live verification on 2026-11-12, before the Small default was finalized, used one five-second Realtek recording at 44,100 Hz, zero software gain, and CPU INT8 (`int8_float32`). An observer confirmed that all four decoding calls received the identical PCM object; no audio was saved. Python DNS/connect calls were blocked throughout inference. The planned speaking window was missed, so this run verifies the pipeline and provides latency observations, not an accuracy result. VAD retained 1.264 seconds including padding.

| Model | Load time | Without hints | With hints | RTF without / with hints |
| --- | ---: | ---: | ---: | ---: |
| Base | 0.852 s | 0.658 s | 0.662 s | 0.132 / 0.132 |
| Small | 1.007 s | 1.977 s | 2.011 s | 0.395 / 0.402 |

Small took about three times Base's transcription time in this single run. Both processed the five-second clip faster than real time. These timings include quiet portions in the RTF denominator and remain subject to the fixed pass order, cold imports, and cache effects described above. The separately generated in-memory fixture "Hello Aqua. Can you hear me?" was recognized by Small as "Hello Aqua, can you hear me?"; this is a smoke test, not evidence of improved live accuracy. The existing **130 tests** and dependency checks passed after that installation. Subsequent successful real-world testing motivated the V0.2.2 Small default; these historical timings are not a quantitative accuracy measurement.

## Audio processing and gain

Microphone audio is resampled from its capture rate, such as 44,100 Hz or 48,000 Hz, to mono float32 audio at **16,000 Hz** using PyAV's anti-alias filtering. Samples are not relabeled with a different rate. Recognition uses the Silero speech-activity model bundled with `faster-whisper`; it needs no separate model download.

For an intentionally chosen software gain, use:

```powershell
.\.venv\Scripts\python.exe -m aqua transcribe --device 1 --language en --gain-db 12 --diagnostics
```

`--gain-db` accepts values from -30 to +30 dB and affects only recognition input. It does not change Windows settings or automatically normalize recordings. It amplifies background noise along with speech and can cause clipping; a warning reports saturation. Original-capture diagnostics remain unchanged.

Gain remains **0 dB by default**. A live recording has already peaked at 99.6% of full scale, so do not add gain automatically. Compare microphone selection and diagnostics before choosing any gain adjustment.

Short, quiet, noisy, or accented speech can be mistranscribed, and speech activity filtering can miss quiet speech. Silence may produce no text. CPU latency varies with system load; this version records first and then transcribes rather than streaming.

For command help or the installed console entry point:

```powershell
.\.venv\Scripts\python.exe -m aqua --help
.\.venv\Scripts\python.exe -m aqua transcribe --help
.\.venv\Scripts\aqua.exe transcribe
```

## Diagnose low microphone levels

For the same samples measured at native capture, normalized float, resampled float, and the wake-input boundary, use the explicit read-only calibration command:

```powershell
.\.venv\Scripts\python.exe -m aqua mic-levels --compare-backends --check-wake
```

For each eight-second sample, stay quiet for the first two seconds, then repeat **Hey Aqua** at normal distance/volume for six seconds. This compares MME and WASAPI sequentially only when their exact Windows endpoint IDs match. It prints Windows endpoint volume/mute/channel metadata and counts local wake events, without Whisper, Qwen, application actions, saved audio, or gain/settings changes. Omit both optional flags for a single level-only capture without loading a model. `--device ID` selects a specific input; `--stereo` separately checks channel levels/correlation and a hypothetical mix that production Aqua never performs.

The diagnostic callbacks run in the consumer thread, receive immutable PCM or copied arrays, and disable themselves if they fail. A failed observer invalidates the calibration report, rather than changing captured audio or presenting partial measurements as valid. Normal hands-free mode has no observers enabled.

The [microphone investigation](microphone-levels-v0.6.md) records measured stages, exact endpoint mapping, backend differences, safe configuration, and remaining uncertainties. No wake-path gain or automatic normalization was enabled; `--gain-db` still applies only to Whisper after activation.

Peak readings around -36 to -39 dBFS and whole-recording RMS around -59 to -61 dBFS are quiet, but do not establish that a microphone is defective. Silence between words lowers whole-recording RMS. A different selected microphone, Windows input gain, distance, or a Bluetooth hands-free input can also affect the result.

1. Run `devices` again and check the selected input's name. The current Windows default is the Realtek microphone array; compare other inputs using their current explicit IDs only when needed. Entries for the same microphone may use different host APIs. Let each use its native default rate first.
2. Run `mic-test --device ID --diagnostics`, speak at a normal distance across the five-second interval, and compare results under similar conditions. WASAPI and MME entries can have different defaults, such as 48,000 Hz and 44,100 Hz; Aqua handles either through proper resampling for recognition.
3. In **Settings > System > Sound > Input**, check the selected input, volume, and input test. If the device exposes microphone boost in its additional sound properties, adjust it manually in small steps and retest for clipping. Aqua does not modify gain settings.
4. Leave recognition gain at 0 dB when peaks are already high. If a selected input remains quiet after checking its settings, use a deliberate small gain adjustment and retest for clipping. Software gain cannot recover speech that was not captured or improve its signal-to-noise ratio.

`--diagnostics` adds original-capture clipping counts, 20 ms frame measurements, quiet-frame RMS (20th percentile), louder-frame RMS (90th percentile), and RMS over energy-active frames. The activity threshold is shown. These energy measurements help distinguish quiet gaps from louder input; they are **not proof of speech**, a calibrated noise-floor measurement, or sound-pressure readings. Background noise can count as activity, while quiet speech can fall below the threshold. Transcription diagnostics also report the speech duration retained by Silero VAD, which is a separate estimate.

If recording fails:

- In **Settings > Privacy & security > Microphone**, enable microphone access and access for desktop apps.
- Reconnect the input, rerun `devices`, and select its current ID.
- Close applications that may hold the device exclusively, try another host API entry, and omit a sample-rate override.
- For overflow, reduce competing system load and repeat the test. Failed or interrupted recordings are discarded.

Hardware and setup errors produce an explanatory message and a nonzero exit status. The automated tests do not depend on microphone access.

## Privacy and scope

Microphone samples, recognized text, and Aqua's conversation history stay in process memory. Aqua has no audio/conversation-saving option, audio uploads, or cloud AI calls. It does not create a conversational memory database. Hugging Face and ONNX Runtime telemetry are disabled for recognition; the separate sherpa runtime is also disabled before wake-model creation. Wake detection and speech recognition make no network requests; chat sends text only to `http://127.0.0.1:11434`, using direct loopback connections without proxies or redirects. Hands-free mode makes no Ollama request until an awakened, reliable command is recognized. No external requests are needed during operation.

The legacy explicit dependency installation and separately approved `setup-model --download` remain online setup operations, outside chat execution. Python may create normal import caches, which contain no audio or conversation text. Replies and recognized text appear in terminal scrollback; redirecting terminal output can save them at the user's direction. Ollama is a separate local service with independently configured logging; Aqua does not change that configuration or make claims about its logs.

V0.3 added session conversation; V0.5 introduced approved app launches; V0.6 added the acoustic wake front end; V0.7 shared conversational tool calling and verified action history across all three modes; V0.8 Stage 1 adds bounded sequential decisions. Aqua does not implement Stage 2 project/test tools, TTS, a system tray, automatic startup, persistent conversational memory, file deletion, or general desktop automation. Conversational replies are displayed as text and never executed.

## Project layout

```text
src/aqua/
    audio/       # Capture/streaming, resampling, levels, Silero endpoints, console controls
    wake/        # Local CPU Hey Aqua keyword detector, verified assets, explicit setup
    speech/      # Local models, vocabulary hints, recognition, comparison timing
    brain/       # Native Ollama tools, shared controller, atomic bounded history, answer validation
    actions/     # Registered tool schema, narrow authorization vetoes, fixed launcher allowlist
    memory/      # Reserved for future local memory
    security/    # Reserved for future permission enforcement
    handsfree.py # Acoustic wake/capture states feeding the existing voice/chat path
tests/           # Synthetic audio and mocked device/model checks
models/          # Local speech model, excluded from Git
.cache/          # Local dependency/model cache, excluded from Git
```

## Model intelligence benchmark

The completed [model comparison report](../benchmarks/model_intelligence/REPORT.md)
covers all **300 observations** and **685 passing regression tests**. The 4B
Instruct preset at temperature 0.2 and Gemma thinking tied at **42/50 overall**;
4B was much faster, while Gemma led reasoning. The configured default remains
unchanged. Exact evaluated presets, category results, and six pending review
cases are documented in the report.

The installed models can be evaluated with a frozen, 50-case local suite covering
knowledge, reasoning, memory, instructions, tool safety, and uncertainty. This
command explicitly saves synthetic benchmark responses and results under
`benchmarks/model_intelligence/`; ordinary chat still saves no conversation.
Tools are replaced by recording stubs and process launches are independently
blocked. No models or dependencies are installed by the benchmark.

```powershell
# Representative pilot, before committing to the slow full suite:
.\.venv\Scripts\python.exe -m aqua benchmark --live --pilot --configs A C B E D
# Full suite for selected configurations:
.\.venv\Scripts\python.exe -m aqua benchmark --live --configs A C E
# See output/resume/report-only options:
.\.venv\Scripts\python.exe -m aqua benchmark --help
```

`--thinking auto|on|off` and optional `--seed INTEGER` are available in `chat`,
`voice-chat`, and `handsfree`. `--temperature` continues to control sampling.
Optional `--top-p`, `--top-k`, `--presence-penalty`, and `--repeat-penalty`
allow the exact benchmark preset to be used interactively. Omitting them keeps
the installed model's sampling defaults; no model file is changed.
`auto` preserves conservative model-appropriate behavior. Explicit settings are
accepted only for inspected supported models; they never silently fall back.
The verified Instruct model has no thinking mode to enable. The installed
Qwen3.5 and Gemma 4 support Ollama's top-level Boolean `think` control. A response
with a reasoning channel despite `off` fails rather than claiming it was disabled.
Ollama's reasoning-channel text is never displayed or written to benchmark records; counts and
timings are recorded separately. Buffered responses do not invent token-arrival
timestamps.

```powershell
.\.venv\Scripts\python.exe -m aqua chat --model qwen3.5:9b-q4_K_M --thinking off --temperature 0.2 --seed 42 --diagnostics
```

Each result records the model identity, settings, dataset/prompt/source hashes,
structured tool proposals and stub outcomes, raw visible answer, latency, and
scoring criteria. Seeded runs can still differ with backend/device state.
Reference answers are never sent to the candidate. Ambiguous grading remains
flagged for review; an exit status of zero means execution completed, not that
every answer was correct. The benchmark does not add a task-agent loop.

## Tests

Semantic-intent development keeps **Stage 2B.2 paused** and preserves its unfinished
edits. Qwen remains the native-tool interpreter; host consent and verified UIA
execution remain independent. See the [architecture and repeatable paraphrase
benchmark](semantic-intent-architecture.md) and its [measured results](../benchmarks/semantic-intent-v1/report.md).
The current regression suite passes **990 tests**, with `pip check` clean.

```powershell
# Real local Qwen; synthetic UIA and execution only. Choose a new output filename.
.\.venv\Scripts\python.exe -B tools\benchmark_intent.py --live --trials 2 --output .cache\intent-manual-01.jsonl
```

Stage 2B.1 passes **974 tests**: the previous 948 plus 26 fresh-observation and
combined-consent/security regressions. `pip check` passes. No new
dependencies or models were installed. The user confirmed real typed-chat entry;
the latest 12 repeated local-Qwen checks used synthetic UI/write results and
simulated hands-free transcriptions. None performed real desktop input.

Stage 2A passes **863 offline tests**, retaining the prior 760 tests. `pip check`
passes. The separate real-Qwen/UIA smoke check passed against an already-open
VS Code window, without launching or manipulating an application. No intelligence
benchmark observations or model files were changed.

V0.8 Stage 1 with the continuation corrections passes **760 offline regression
tests**. `pip check` reports no broken requirements. The latest read-only local
probe completed **15/15 natural clean-session tasks** and **6/6 injected-stop
recovery tasks**, with all execution stubbed. The natural baseline also completed
15/15, so this does not establish an improved spontaneous completion rate. See the
[continuation investigation](continuation-v0.8.md) for actual native outputs,
stop/refusal fixes, test results, and remaining uncertainty. The [initial Stage 1 report](agent-v0.8-stage1.md)
remains available with its earlier observations.

Historical V0.7 tool-calling verification passed **617 automated tests in 2.466 seconds**, including the preserved
speech and microphone-calibration coverage. The local native-tool smoke check
passed **16/16 turns** with application execution stubbed. See the
[V0.7 report](tools-v0.7.md) for measured latency and limitations.

```powershell
.\.venv\Scripts\python.exe -B tools\test_offline.py
.\.venv\Scripts\python.exe -m pip check
```

The test runner blocks real sockets, DNS lookups, and process launches throughout
discovery and execution. Add `--verbose` for individual test names or
`--pattern test_controller.py` for a focused run.

Tests cover the preserved speech/calibration behavior plus native Ollama streaming/tool framing, strict arguments, authorization, one-use confirmation, atomic tool-result history, capability/clock context, errors, cancellation, and all three session modes. Model responses and execution effects are mocked, with fail-fast network/process guards; ordinary turns are checked for a single conversational request and no separate classifier. Tool proposals are checked only after complete responses, and late/multiple/malformed calls cannot execute a partial subset. Tests require no physical microphone, Ollama service, installed inference models, download, or internet connection. `tools/smoke_tools.py --live --diagnostics` is a separate execution-disabled real-model check; the V0.6 wake and microphone probes remain separate explicit diagnostics.

Initial V0.5 safety and test-isolation validation passed **344 tests in 1.144 seconds**, including the original speech coverage. The **105 affected tests** passed first; the full run additionally blocked Python DNS and socket connections. The previously reported five voice-test failures were reproduced with seven attempts to reach an unmocked semantic HTTP dependency. Fixing that test boundary removes the real Ollama waits behind the earlier 211-second suite; the original per-request timings were not recorded. These are test-suite timings, not live recognition or model latency measurements.

Subsequent live semantic testing exposed a transport bug hidden by persistent mock sockets: Ollama returned HTTP 200 and valid decision JSON, but completing a `Connection: close` response closed the socket before Aqua's next timeout update, causing Windows error 10038. The client now checks its wall deadline after EOF without touching the closed socket; it still updates timeouts while the response remains open. Offline regressions use the real standard-library HTTP parser over an in-memory socket for Content-Length, chunked, and close-delimited responses, and verify that EOF does not disable the wall deadline.

After this fix, **365 offline tests passed in 1.106 seconds**, retaining all 344 previous tests. The full run blocked Python DNS/socket connections and unexpected process launches. The separate live smoke check on November 13, 2026 matched **7/7** expected classifications and policy outcomes with the installed Instruct model, without launching an app. Individual classification wall times were **0.651–0.757 seconds**. These sequential checks exclude audio capture, transcription, and conversational generation; they are not a controlled cold-load benchmark or a broad language-accuracy evaluation.

V0.6 passed **467 offline tests in 1.798 seconds**, including all previous 365 tests, with network and unexpected process creation blocked. `pip check` passed. Wake/VAD/streaming/CLI/session tests need no microphone or installed wake assets. The opt-in `tools/smoke_wake.py --synthetic` and `--microphone --seconds 10` checks are separate from unit tests and never dispatch application actions. See the V0.6 report for actual acoustic and resource measurements.

Historical V0.2 validation passed **88 tests**, including the original 34, and `pip check`. A manual smoke test recognized an in-memory speech fixture with Python DNS/connect calls blocked. The first live Realtek microphone test later recognized "Hello, Ako. Can you hear me?" V0.2.1 preserves those 88 tests and adds coverage for the new behavior; evaluate name spelling and unrelated speech using the comparison commands rather than assuming hints improve recognition.

V0.2.1 validation passed **130 tests**: the unchanged 88 tests plus 42 new tests. Manual offline comparison checks used actual Base inference on in-memory speech fixtures with Python DNS/connect calls blocked. Both hint conditions recognized "Hello Aqua, can you hear me?" with punctuation differences; both returned "Please close the window after lunch." unchanged for unrelated speech. These fixtures do not establish a recognition-accuracy improvement or replace a live microphone comparison. Small was installed and verified later through the separately approved process described above.
