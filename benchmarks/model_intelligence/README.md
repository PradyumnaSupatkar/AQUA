# Local model intelligence evaluation

This suite evaluates the installed models through Aqua V0.7's actual shared
conversation controller. It does not implement the V0.8 agent loop. No model
downloads, model edits, microphone capture, real application launches, web
access, or new dependencies are involved.

The completed [final report](REPORT.md) identifies the joint overall leaders,
per-category results, measured latency, six unresolved review cases, and exact
recommended commands. `comparison.json` is its machine-readable counterpart.

`dataset.json` contains 50 independent cases: 10 knowledge, 10 reasoning,
8 multi-turn memory, 7 instruction, 10 tool/safety, and 5 uncertainty cases.
Project names and personal facts are fictional fixtures. `freeze.json` records
the exact dataset-file fingerprint before scored requests. The runner also
hashes canonical JSON, settings, prompt, tools, and relevant source files;
canonical JSON hashes differ from exact-file hashes by design. The original
grading dataset remains `dataset.v1-frozen.json`; current `dataset.json` includes
the documented 1.1 grading repair. Questions, reference answers and expected
effects are identical. See `grading_revision.json` for identity/hashes.

## Controls

`config.json` defines A (Qwen3 4B Instruct), B/C (Qwen3.5 9B thinking on/off),
and D/E (Gemma 4 thinking on/off). Main configurations use temperature 0.2.
IDs ending in `05` use temperature 0.5 and must be explicitly selected.

The common settings are context 4096, generation limit 1024, seed 42, top-p
0.95, top-k 40, presence penalty 0, and repeat penalty 1. Explicitly controlling
these options avoids inheriting different sampling defaults from model files.
The request keep-alive is Aqua's existing five minutes. Runtime placement is
observed, never forced. A seed does not remove all backend/device nondeterminism.

Each independent case starts with a fresh Conversation. Multi-turn cases retain
their own complete state. All candidates receive the same production system
instructions, frozen local-clock context, and tool schema. Only `turns[].text`
enters the model request; reference answers, scoring criteria, expected tools,
and simulated launch failures remain host-side.

## Running and resuming

From `D:\Aqua`, using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m aqua benchmark --live --pilot --configs A C B E D
# Only when explicitly collecting new model observations; keep the completed full/ run intact:
.\.venv\Scripts\python.exe -m aqua benchmark --live --configs A C E B D --output benchmarks\model_intelligence\new_run
.\.venv\Scripts\python.exe -m aqua benchmark --live --resume benchmarks\model_intelligence\new_run
# Recalculate the completed run without inference, preserving original grades:
.\.venv\Scripts\python.exe -m aqua benchmark --report-only benchmarks\model_intelligence\full --rescore --dataset benchmarks\model_intelligence\dataset.json
```

The pilot uses eight IDs fixed in the dataset: `k06 r02 r05 m02 i04 t01 t07 u01`.
It contains both reasoning and tool decisions, plus a correction in memory.
Use `--cases ID ...` for a separately reported subset. Model configurations run
sequentially; do not run another inference workload concurrently.

No `--live` means no model request. Exit zero means the planned observations
were recorded, **not** that all answers were correct. Configuration/preflight
errors and incomplete coverage produce nonzero status. Ctrl+C preserves complete
cases for resume; a partial case is retained in `interrupted_attempts.jsonl` and
restarts from fresh state on resume. Changed sources, data, settings, or selection
cannot silently resume the same experiment.

## Audit and scoring

Each run writes `manifest.json`, its dataset snapshot, model/runtime metadata,
append-only `raw.jsonl`, `summary.json`, and `report.md`. Visible answers,
structured native proposals, tool history, and safe errors are retained.
Thinking-channel text is not printed or saved; only its counts and timing are
kept. Normal Aqua conversations remain in memory; this explicit benchmark is a
separate, authorized saving operation using synthetic inputs.

Scoring uses reference concepts, parsed numeric/JSON conclusions, trusted
arithmetic checks, instruction structure, and actual proposed/executed tools.
Generated code is inspected as an AST and never executed. Syntax requirements
are enforced only when requested; alternate correct prose or uncertain
interpretations can be flagged `manual_review`. A keyword match alone cannot
resolve every semantic claim. Tool proposals and unauthorized stub executions
are separate metrics; safe host rejection still penalizes an incorrect model
proposal. A proposed application is not evidence that it actually opened.

The four automatic statuses are `correct`, `incorrect`, `manual_review`, and
`error`. Errors include exhausted generation budgets, interrupted streams, and
protocol failures. The all-completed accuracy denominator includes failures and
pending review. Do not present that conservative number as an exact semantic
accuracy when review flags remain. Optional `reviews.json` is an explicitly
justified adjudication overlay, preserving automatic scores. A Codex-assisted
reference audit, if supplied separately, is labeled as such rather than called
human validation. Subjective or unresolved cases still require human review.

`--report-only RUN --rescore --dataset GRADING_DATASET` checks that revised
grading metadata preserves the run's questions, reference facts, expected tools,
and case order. It writes separate `rescored.jsonl` and `rescored/` reports with
source/data/raw-file hashes, retaining original scores and source files. The
completed run's original `raw.jsonl`, `summary.json`, and `report.md` are archival;
use `full/rescored/` for the corrected results. Report-only operations make no
model request.

## Timing and inference limits

Per-turn latency covers all generation requests, validation, stub execution,
and explanations. Token counts and throughput include hidden thinking wherever
Ollama reports it; visible-only token counts are unavailable. Model load, prompt
evaluation, and total generation come from server counters. The observed first
thinking-to-answer interval is wall time, not an exact server thinking duration.
Buffered responses have no genuine token-arrival timestamps, so those are null.

Cold/warm status is inferred only from recorded residency and server loading,
not guessed from case position. Models are not deliberately unloaded. Different
case/output lengths, prompt cache, run order, thermals, and background activity
affect timing. One seeded run on a small curated suite is not a statistical or
general intelligence guarantee. A budget-limit failure means the configuration
did not deliver a usable complete answer within that limit; it does not prove
the model could never solve the question with more time.

`compatibility.json` contains initial raw API on/off checks.
`budget_investigation.json` separately tests 1024/2048 limits and the installed
Qwen3.5 presence-penalty setting on one calculation. These are diagnostics,
not scored evaluation cases or evidence that one sampling preset is universally
better. No benchmark question was selected from those candidate responses.
