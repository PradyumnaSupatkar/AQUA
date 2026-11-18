# Benchmark progress and recovery

Status: completed. All 300 observations were collected; uniform grading repairs,
reference audits, corrected reports, 685 regression tests, and pip check are
finished. Read REPORT.md and full/rescored/ for final results. The entries below
are retained as the interruption/recovery log, not pending work instructions.

- Existing work was inspected and continued after the interruption.
- Original 617 tests passed before changes. The current regression run passed
  675 tests before live collection. Final regression run and pip check remain.
- Thinking transport and non-streaming support are implemented and tested with
  offline fixtures. Live streaming on/off and native tools passed the pilot;
  a final live buffered-response check remains.
- No dependencies or models were installed, edited, deleted, or downloaded.
- Frozen dataset exact-file SHA-256:
  `59f2a33ca1c1f48ee1d7dd93c612931f73b8b0e5e25b54e2007ff51f24d1c5d1`.
- Runner manifests use canonical JSON hashes, so their dataset hash differs
  from the exact-file hash. References/scoring were fixed before scored runs.
- `pilot/` completed 56 observations: eight fixed cases across A, A05, C, C05,
  B, E, D. No transport errors or unauthorized executions. Wrong answers and
  conservative review flags are preserved.
- `full/` is collecting 50 cases each for A, A05, C, B, E, D, sequentially.
  The process was started with:

```powershell
.\.venv\Scripts\python.exe -u -B -m aqua benchmark --live --configs A A05 C B E D --output benchmarks\model_intelligence\full
```

Use the existing running process if available; do not start a competing model
run. Its tool session ID was `39862`. Completed cases are immediately appended
to `full/raw.jsonl`; summaries and reports are refreshed after each case. If
the process really stopped, resume without changing frozen inputs/sources:

```powershell
.\.venv\Scripts\python.exe -u -B -m aqua benchmark --live --resume benchmarks\model_intelligence\full
```

## Confirmed diagnostic observations

`compatibility.json` records actual on/off API probes. Qwen3.5 off returned 323
with no thinking channel in 0.886 s. Its thinking-enabled 512-token probe ran out
without an answer. `budget_investigation.json` then recorded correct final answers
at fixed context 4096: common settings at 1024 used 791 generated tokens and
151.787 s; the installed presence-penalty setting 1.5 used 551 tokens, finishing
at both 1024 and 2048 limits. This demonstrates budget exhaustion, not evidence
that all thinking failures are loops. Reasoning text was never saved.

Runtime observations: 4B Instruct 34% CPU / 66% GPU; Qwen3.5 73% / 27%; Gemma
84% / 16%. See per-case `/api/ps` records and `resource_observations.json`.

All scored executions use a recording stub and an independent Popen guard.
Reference audits are separate files labeled Codex-assisted, not human
validation; frozen automated grades and raw responses have not been rewritten.

## After collection

1. Finish reference auditing uniformly across models; keep genuinely ambiguous
   cases flagged. Never use only a reference keyword to accept contradictory
   prose (both 4B Git answers illustrate that problem).
2. Fix a reporting-only denominator issue in runner.summarize: its
   `unauthorized_tool_calls` numerator covers all categories, while the existing
   `...rate_per_tool_turn` denominator covers only tool-category turns. Preserve
   total counts; add matching within-tool counts and explicit all/tool turn
   denominators. Apply analogous execution-rate correction. Add regression
   tests. This is an aggregation correction, not a prompt/scoring/model change.
3. Record old/new report-source hashes and regenerate summaries from immutable
   raw records. Do not rerun models merely to repair reporting arithmetic.
4. Complete live `.chat(stream:false)` checks without application launches.
5. Run the complete offline test suite and pip check after final code changes.
6. Write the final cross-model report with actual category scores, review flags,
   proposal/execute counts, latency distributions, thinking overhead, limitations,
   recommendations, changed files, and exact commands. Leave Aqua's defaults
   unchanged unless evidence justifies a deliberate documented change.

No V0.8 agent loop, new desktop tools, TTS, or wake/audio changes are part of this
task. Do not infer native execution from model-emitted tool-shaped prose.
