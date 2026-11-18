# Aqua V0.6: offline wake and automatic speech endpoints

V0.6 adds a genuine acoustic **Hey Aqua** gate before the existing voice pipeline. The exact phrase has passed synthetic acoustic checks with the actual installed model. Human microphone wake accuracy and end-to-end spoken action accuracy still need the interactive checks below; this report does not claim they were completed.

## Run

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -m aqua handsfree --language en --diagnostics
```

The current Windows default microphone is selected automatically. `--device ID` and `--sample-rate HZ` remain available. Vocabulary hints remain off; add `--hints` to enable them. No automatic gain is applied. `voice-chat` is unchanged as the manual fallback.

## Engine and pipeline

[Sherpa's configurable keyword spotter](https://k2-fsa.github.io/sherpa/onnx/kws/index.html) supports phrases without training a separate custom model. Aqua uses the compact English GigaSpeech Zipformer 3.3M INT8 model on CPU, one thread and four active decoding paths. Only the fixed label `HEY_AQUA` is accepted. Its verified token sequence is `▁HE Y ▁A QU A`; no other wake phrase is substituted.

One PortAudio callback stream captures native mono int16 audio. A persistent PyAV resampler maintains its anti-alias filter across blocks and emits 512-sample float32 frames at 16 kHz. While idle, only the keyword model runs. A bounded pre-roll buffer preserves words around activation. After wake, incremental Silero V6 uses the ONNX graph already bundled with Faster-Whisper, with separate recurrent state. No VAD asset or PyTorch download was needed.

Completed speech goes through the shared `chat._transcribe_recording` helper, the existing recognition reliability gate, and `chat._respond_or_launch`. Qwen's semantic interpreter, strict JSON validation, four-app allowlist, fixed executable paths, `shell=False`, and one-use confirmation policy are preserved. A fixed leading wake greeting is removed from recognized text; that cleanup does not choose an action.

Pending confirmation survives idle listening and the next valid wake event. No speech, rejected/oversized audio, cancellation, errors, unrelated conversation, and typed controls disarm it. `/clear` clears both conversation history and pending action state. Console input uses nonblocking Windows keyboard polling; no blocking input thread is left behind.

Typed controls are handled while listening, between audio blocks. During a blocking transcription or model response, use Ctrl+C to cancel; a typed line is handled when listening resumes.

Whisper, wake weights, and VAD load once per session. Ollama is contacted lazily only after a reliable awakened command, including its initial model availability checks. During processing, the same microphone callback discards incoming audio. Resume clears queued frames, resampler history, and acoustic state, so background speech cannot become an overlapping turn.

## Defaults and bounds

| Setting | Default | Behavior |
| --- | ---: | --- |
| `--wake-threshold` | 0.25 | Acoustic keyword probability threshold; raising it can reduce false triggers and increase misses |
| `--wake-score` | 1.0 | Keyword boost; raising it can also increase false triggers |
| `--wake-cooldown` | 2 s | Minimum wall interval between accepted wake events |
| `--vad-threshold` | 0.5 | Speech probability; active speech uses 0.35 hysteresis |
| `--end-silence` | 0.8 s | Sustained silence to finish speech |
| `--initial-timeout` | 5 s | Wait for meaningful post-wake speech |
| `--max-utterance` | 15 s | Reject the entire turn at the cap; never execute a truncated command |
| `--pre-roll` | 0.5 s | Context retained before activation; does not itself count as command speech |
| Minimum voiced audio | 0.16 s | Reject a brief VAD/noise burst; the existing Whisper reliability checks also apply |

Timeouts and capture limits count audio samples. The capture allocation is bounded by pre-roll plus the post-wake limit (15.5 seconds by default). The native queue holds at most 32 callback blocks, normally about 640 ms. Overflow, incomplete audio, disconnection, or two seconds of stalled input fails closed. Sherpa removes consumed feature history while decoding; idle audio is not accumulated indefinitely.

## Installation and privacy

Approved additions:

| Item | Download bytes | Installed logical bytes |
| --- | ---: | ---: |
| `sherpa-onnx==2.0.1` | 2,178,304 | 5,932,407 |
| `sherpa-onnx-core==2.0.1` | 16,663,244 | 45,548,560 |
| Five verified model/tokenizer assets | 5,498,367 | 5,498,367 |
| Total | **24,339,915** | **56,979,334 (~54.3 MiB)** |

The original archive would download unused floating-point weights; setup instead obtains only the five needed files from the maintainer's pinned [ModelScope revision](https://modelscope.cn/models/pkufool/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01/files), `d4664d22a51cefcb2232047e72a21cff36797c53`. Exact filenames, byte counts, and SHA-256 hashes are in `wake/models.py`. Files live in `D:\Aqua\models\sherpa-kws-en-int8`, excluded from Git. Temporary wheel copies were removed. Existing Ollama and Whisper model files were not changed.

`setup-wake` validates locally. Only `setup-wake --download` can fetch missing assets; normal inference never downloads or falls back. Existing invalid final files are not overwritten. A fresh installation can use the optional `.[handsfree]` dependency extra after approval; the old commands do not require sherpa.

Audio, transcripts, and session history remain in memory. Diagnostics are printed rather than saved. The wake-only diagnostic never calls Whisper, Qwen, or the application router. Its synthetic mode uses installed Windows System.Speech solely as a test-fixture generator, with WAV data in a memory stream; this is not an Aqua TTS feature.

Sherpa bundles its own ONNX Runtime DLL. Disabling the separate Python ONNX Runtime would not cover it, so Aqua calls the bundled runtime's public telemetry-disable API before model creation and retains one process-lifetime environment. Failure to disable it stops startup. ONNX Runtime documents that a minimal environment-initialization ETW event can precede API disablement when an external Windows trace session is collecting; this is not an audio/transcript upload. No registry or Windows telemetry settings are changed.

## Verification performed

**467 deterministic offline tests passed in 1.798 seconds**, including all original 365. The full run blocked Python DNS/socket access and unexpected process creation. `pip check` reported no broken requirements. Coverage includes wake events/non-events, cooldown, pre-roll, paused/immediate speech, natural pauses, timeout/cap, input loss/stall, Ctrl+C, console controls, conversational context, confirmations across wake turns, safe mocked launches, and existing manual voice behavior.

Separate real model checks, November 13, 2026:

| In-memory Windows voice fixture | Actual keyword events |
| --- | ---: |
| Hey Aqua | 1 |
| Hey Aqua, open Chrome (continuous) | 1 |
| Hey Aqua, 1.5-second pause, Open Chrome | 1 |
| What is Formula One? | 0 |
| I feel like programming | 0 |
| Hey Jarvis, open Chrome | 0 |

The positive events occurred at 1.760 seconds in these fixtures, including 0.5 seconds of leading silence. This is an audio position, **not measured latency after the human wake phrase ends**. Processing used 0.094–0.172 CPU seconds for the positive fixtures, faster than real time. Keyword loading took 1.231 seconds in an earlier standalone check.

Actual keyword + Silero + Whisper checks used the same in-memory audio, with network and action dispatch blocked:

- Wake only: event at 1.760 s, no-speech timeout at 6.784 s; no transcription.
- Continuous command: endpoint at 3.168 s; Whisper returned `Open Chrome.` in 2.154 s.
- Paused command: endpoint at 5.120 s; Whisper returned `Open crow` in 2.037 s. Every nonzero command sample was preserved exactly, followed by 0.720 s of silence. Transcribing the full isolated synthetic command also returned `OpenCrow`, confirming a recognizer/fixture error rather than endpoint truncation. Aqua does not replace this with a guessed application name.
- Ordinary Formula One speech: no wake and no captured turn.

Actual ambient Realtek capture used native **44,100 Hz**, resampled to 16,000 Hz:

| 10-second check | CPU, one logical core | CPU, all 16 logical cores | Working set | Wake events |
| --- | ---: | ---: | ---: | ---: |
| Wake-only diagnostic | 7.02% | 0.44% | 106.7 MiB | 0 |
| Full hands-free session, Whisper resident | 4.21% | 0.26% | 398.7 MiB | 0 |

The full idle session explicitly blocked transcription, Ollama access, network connections, and application creation, then exited through its console control path. Its Whisper load took 1.053 seconds. Memory figures exclude the separate Ollama service. These are short separate observations with normal scheduling variation, not comparative CPU benchmarks, long-term memory soak tests, or a calibrated false-alarm rate.

An additional accelerated **10-minute silence** check fed the actual keyword model 18,750 frames without network access. It produced zero events, took 17.366 seconds to process (17.109 CPU seconds), and its working set was **91.1 MiB at 1, 5, and 10 minutes of simulated audio**. This checks retained acoustic state; it is not a ten-minute microphone/background-speech trial.

## Files added or changed

- Session/integration: `src/aqua/handsfree.py`, `src/aqua/chat.py`, `src/aqua/cli.py`.
- Audio: `src/aqua/audio/streaming.py`, `vad.py`, and `console.py`.
- Wake: `src/aqua/wake/__init__.py`, `models.py`, `keyword.py`, `privacy.py`, and `hey_aqua.keywords`.
- Packaging/documentation: `src/aqua/__init__.py`, `pyproject.toml`, `README.md`, and this report.
- Tests: `tests/test_streaming.py`, `test_streaming_vad.py`, `test_wake.py`, `test_handsfree.py`, `test_handsfree_cli.py`, `test_console_commands.py`, and `test_wake_smoke.py`.
- Explicit diagnostic tool: `tools/smoke_wake.py`.

## Interactive checks still required

First run the wake-only microphone check. It cannot launch applications:

```powershell
.\.venv\Scripts\python.exe -B tools\smoke_wake.py --microphone --seconds 30
```

Say `Hey Aqua` several times, then speak ordinary background sentences without that phrase. Observe the event count and repeat at normal distance/noise levels. A longer negative-only run gives more useful evidence than the short ambient check above.

Then run `handsfree --language en --diagnostics`. This mode can launch the four approved apps. Wait for the listening state between turns:

1. Say `Hey Aqua, open Chrome.`
2. Say `Hey Aqua`, pause one or two seconds, then `Bring up my coding environment.`
3. Say `Hey Aqua, what is Formula One?`, followed on a new wake turn by a related question.
4. Say `Hey Aqua, I feel like programming.` Expect clarification; confirm with `Hey Aqua, yes, go ahead.`
5. Say `Hey Aqua, don't open Chrome.` Expect no launch.
6. Repeat the ambiguous programming request, type `/clear` then Enter, and say `Hey Aqua, yes, go ahead.` The old suggestion must not execute.
7. Try wake-only silence, a natural pause shorter than 800 ms, and ordinary speech without the wake phrase. Finish with `/quit` or Ctrl+C while idle.

Use `/clear` to cancel a suggestion before moving to an unrelated test. Say a fuller confirmation such as `Yes, go ahead` if a very short `yes` fails the existing speech-confidence gate.

## Remaining limits

No trained custom classifier was needed, but an open-vocabulary spotter is not infallible. Synthetic successes and three negative fixtures do not establish accent/noise robustness. Human positive microphone wake tests and end-to-end spoken app tests have not been performed in this session. Adjust threshold/boost conservatively based on real positive and negative trials.

The pre-roll reduces boundary loss but cannot guarantee every word if a detector fires very late. Whisper can still misrecognize short speech, as observed above. Speaking during model processing is intentionally discarded; wait for the listening state. A microphone loss exits safely and requires restarting the command. Ctrl+C while idle exits; during capture it cancels that turn. There is no TTS, tray UI, startup task, new desktop action, persistent memory, or model replacement.
