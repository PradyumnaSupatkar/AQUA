# agent-v1 guard ablation

Each run turns off one host guard (`tools/benchmark_agent.py --disable-guard NAME --runs 1`) on the
frozen suite (114 cases, SHA-256 `22503a5d…`), same model, fake desktop and real Wikipedia API as the
baseline. Baseline counts are per run (its 2-run totals halved). Raw results: `no-<guard>.jsonl` and
`no-<guard>-summary.json`.

| Run | Case success | Unwanted actions | Unwanted on no-action requests | Wrong refusals | Unnecessary confirmations | Median latency |
|---|---|---|---|---|---|---|
| Baseline, all guards on (per run of 2) | 86.4% | 7.5 | 2.0 | 0 | 2.0 | 1.78 s |
| No negation_guard | 87.7% | 7 | 2 | 0 | 1 | 1.77 s |
| No claim_retry | 86.8% | 8 | 3 | 0 | 1 | 1.77 s |
| **No cant_retry** | **92.1%** | **4** | **0** | 0 | 0 | 1.79 s |
| No citation_strip | 89.5% | 6 | 2 | 0 | 1 | 1.80 s |
| No knowledge_cutoff | 87.7% | 7 | 2 | 0 | 1 | 1.77 s |
| No restate_request | 89.5% | 6 | 1 | 0 | 1 | 1.99 s |

## How much is noise

The two baseline runs differ on 5 cases (98 vs 99 passing), and `force-quit-other` failed in both
baseline runs but passed in all six ablations. With one run per ablation, a difference of 1-3 cases
(1-3 points) is within run-to-run variation. Only one ablation broke a case that passed in both
baseline runs: `si-entry-ambiguous-reference` with `restate_request` off.

## Verdicts

| Guard | Verdict | Why |
|---|---|---|
| negation_guard | **Keep** | No measurable change (+1 case, noise). With it off the model still took no action on any negation, but the guard costs nothing and removes tools before the model can act on "don't ...". Re-test with 3+ runs. |
| claim_retry | **Keep** | No measurable change here (one more unwanted action, noise). It catches "done" claims with no tool behind them, which the suite rarely provokes now that action turns keep their tool calls in history. |
| cant_retry | **Remove as is; replace** | It caused the harm. Every baseline unwanted action on a question or hypothetical (`q-how-lock`, `q-spotify-good`, `hyp-volume`, `url-file-blocked`) took 3 model requests: the model answered or asked "Would you like me to ...?", this guard told it "you have a tool for this request, call it now", and it acted. With it off, all of them took 1 request and no action. Replace it with a host confirmation (see fix 1 in `fixes.md`): ask the user "Lock the screen now? Say yes or no." and act only on yes. |
| citation_strip | **Keep** | +3 cases is within noise and none of them involve a citation; the look-up categories pass either way. It is the only thing stopping "According to Wikipedia" on an answer from memory. |
| knowledge_cutoff | **Keep** | No measurable change on the 2 recent-event cases in this run (noise). The earlier live tests showed the model refusing to look up 2025/2026 events without it. Re-test with more recent-event cases. |
| restate_request | **Keep, re-test** | +3 cases (noise) but the only stable-pass regression (`si-entry-ambiguous-reference`), and median latency rose 0.2 s with it off. Multi-step cases passed either way in this run; the guard was added because multi-step requests stopped after step 1. |

Next: run the baseline and the two ablations closest to the line (`restate_request`, `negation_guard`)
with `--runs 3` before removing anything else.
