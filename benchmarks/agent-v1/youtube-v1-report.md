# agent-v1 baseline

New action engine, qwen3:4b-instruct-2507-q4_K_M, fake desktop, real Wikipedia API. 15 cases x 2 runs (32 turns). Suite SHA-256 `22503a5d7cc7cd2743164c7d83f3c007c9ff1570260359e51ecf834e8740cc7d + youtube 3ee06a4e0e811ea05419775e50c504a27f9b591c18f2393585da3f004d455be5`; guards: all on. Run 2026-11-24 15:28 India Standard Time, 151 s.

| Metric | Value |
|---|---|
| Task success (case runs) | 100.0% |
| Cases passing in every run | 15 / 15 |
| Turn success | 100.0% |
| Unwanted actions (all turns) | 0 |
| Unwanted actions on no-action requests | 0 |
| Unnecessary confirmations | 0 |
| Wrong refusals | 0 |
| Errors | 0 |
| Latency per request, median / p90 / max | 3.9 s / 6.2 s / 10.7 s |
| Model requests per turn | 1.19 |

## By category (case runs passed)

| Category | Passed |
|---|---|
| youtube | 30 / 30 |

## Failures grouped by cause

