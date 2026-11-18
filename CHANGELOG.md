# Changelog

A summary of each development stage. Every stage has a detailed report in [`docs/`](docs/), and the
complete former README is kept as the [v0.8 detailed reference](docs/reference-v0.8.md).
Test counts are the offline regression suite at the end of each stage.

## Unreleased

- Project moved to Git and GitHub, with the offline test suite running on every push (GitHub Actions, Windows).
- README rewritten as a short overview; the full former README moved to `docs/reference-v0.8.md`.
- Licensed under Apache-2.0.
- Tests: temporary paths are resolved so Windows short (8.3) paths match on CI.
- **Spoken replies, part A (code only; no voices installed yet).** New `aqua.voice` package: a streaming sentence
  splitter, speakable-text cleanup (markdown, code, links, emoji, and the "Hey Aqua" phrase removed), a spoken-length
  limit (three sentences, then "The rest is on screen."; a final question is always spoken), Kokoro, Piper and Kitten
  engines on sherpa-onnx, and a two-thread playback pipeline that stops instantly and falls back to text after one
  warning if anything fails. Hands-free speaks by default (`--no-speak` to disable); `chat` and `voice-chat` take
  `--speak`. In hands-free, "Hey Aqua" or Ctrl+C interrupts playback, and after Aqua asks a question it listens
  for 3 seconds without the wake word (`--follow-up`). Developed and tested with a fake engine only (1071 tests).
  Voice downloads (`aqua setup-voice`) and the voice benchmark are part B; until then Aqua prints one note and stays
  text-only.
- **Experimental, unfinished:** source-grounded entry objectives (`brain/entry_objective.py`). The model proposes a
  literal writing objective tied to the exact request, and the user confirms it once. Its tests pass (1014 total),
  but its final development evaluation stopped partway and the held-out set was never run. It is expected to be
  replaced by the planned risk-tier policy engine.

## v0.8 — Bounded desktop agent

**Semantic intent v1** (990 tests). A frozen 30-scenario paraphrase benchmark with the real local model and
stubbed desktop effects. A host-side ambiguity veto removed every measured false authorization (4/68 → 0/68);
task handling improved from 80.9% to 85.3%. The model's own interpretation accuracy did not improve.
[Report](benchmarks/semantic-intent-v1/report.md) · [Architecture](docs/semantic-intent-architecture.md)

**Stage 2B.1 — Text entry** (974 tests). Optional, opt-in writing of exact literal text into an empty editable
control through Windows UI Automation (initially Notepad only), with fresh discovery, identity checks and read-back
verification. "Open Notepad and write …" works as one task. Verified with a real typed-chat entry.
[Text entry](docs/text-entry-v0.8-stage2b1.md) · [Notepad diagnosis](docs/notepad-diagnosis-v0.8-stage2b1.md) ·
[Hands-free entry](docs/handsfree-text-entry-v0.8-stage2b1.md) · [Parity trials](docs/text-entry-parity-v0.8-stage2b1.md) ·
[Desktop tasks](docs/desktop-tasks-v0.8.md)

**Stage 2A — Read-only observation** (863 tests). The model can list windows and inspect approved application windows
(Notepad, Calculator, VS Code, Chrome) and describe what is actually there.
[Report](docs/desktop-observation-v0.8.md)

**Stage 1 — Bounded multi-step decisions** (760 tests). The model chooses each tool step from real previous results,
up to five operations and six decisions per task.
[Report](docs/agent-v0.8-stage1.md) · [Continuation fixes](docs/continuation-v0.8.md)

**Model intelligence benchmark.** A frozen 50-case local suite (300 observations) across knowledge, reasoning,
memory, instructions, tool safety and uncertainty. Qwen3 4B Instruct at temperature 0.2 tied Gemma 4 (thinking)
at 42/50 and was much faster, so it remains the default. [Report](benchmarks/model_intelligence/REPORT.md)

## v0.7 — Shared tool calling (617 tests)

One conversation controller with native Ollama tool calls, shared by text, push-to-talk voice and hands-free modes.
Verified action results are recorded in history. [Report](docs/tools-v0.7.md)

## v0.6 — Hands-free mode (467 tests)

Offline "Hey Aqua" wake word (sherpa-onnx keyword spotting on CPU), streaming capture, Silero voice activity
detection to end turns, and microphone level diagnostics.
[Hands-free](docs/handsfree-v0.6.md) · [Microphone levels](docs/microphone-levels-v0.6.md)

## v0.5 — Approved application launches (344 tests)

Launches a fixed allow-list of applications without a shell or model-supplied arguments, with one-use confirmation.

## v0.3 — Local conversation (227 → 248 tests)

Streaming chat with the local Ollama model and bounded in-memory session history.
v0.3.1 diagnosed latency: the original Thinking model spent most of its time on hidden reasoning.
v0.3.2 made Qwen3 4B Instruct-2507 the default. [Latency report](docs/latency-v0.3.1.md)

## v0.2 — Offline speech recognition (88 → 136 tests)

Microphone selection, offline transcription with faster-whisper on CPU (INT8), opt-in vocabulary hints (v0.2.1),
and multilingual Whisper Small as the default model (v0.2.2).
