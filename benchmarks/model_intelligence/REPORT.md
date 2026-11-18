# Aqua model intelligence: final comparison

**Overall, Qwen3 4B Instruct at temperature 0.2 and Gemma 4 with thinking tie at 42/50 confirmed correct (84%). There is no unique overall accuracy winner.** Gemma is the strongest reasoning specialist in this suite; 4B Instruct is the better everyday choice because it is much faster and made no unnecessary tool proposals. Aqua's configured default model remains unchanged.

## Coverage and grading

The full run executed all 50 frozen cases across six configurations: **300 observations, 390 user turns, 401 generation requests**, collected sequentially in **63 minutes 34.6 seconds**. The earlier pilot completed 56 observations across seven configurations, including Qwen3.5 off at temperature 0.5. No additional model inference was used to finish grading or reporting.

All main comparisons used context 4096, output limit 1024, seed 42, top-p 0.95, top-k 40, presence penalty 0, repeat penalty 1, and keep-alive 5m. Temperature and thinking mode are shown below. All candidates received the same production instructions, enabled tools, frozen clock value, and case order within each run. Cases start with independent conversations; multi-turn state remains inside its case. References and checks never enter model requests.

Counts below combine deterministic grading with an auditable **Codex-assisted reference audit, not independent human validation**. Numerical/JSON conclusions, actual structured tool behavior, code structure, and instruction requirements are checked locally; no candidate model grades itself. Six genuinely unresolved cases remain pending and receive no correct-answer credit. The report includes their possible upper bounds rather than concealing them.

| ID | Installed configuration | Confirmed correct /50 | Wrong | Pending | Confirmed accuracy | Maximum if all pending pass |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| A | Qwen3 4B Instruct, off, T=0.2 | 42 | 8 | 0 | 84% | 84% |
| A05 | Qwen3 4B Instruct, off, T=0.5 | 40 | 9 | 1 | 80% | 82% |
| C | Qwen3.5 9B, off, T=0.2 | 40 | 8 | 2 | 80% | 84% |
| B | Qwen3.5 9B, on, T=0.2 | 39 | 9 | 2 | 78% | 82% |
| E | Gemma 4, off, T=0.2 | 37 | 12 | 1 | 74% | 76% |
| D | Gemma 4, on, T=0.2 | 42 | 8 | 0 | 84% | 84% |

No configuration had a transport, timeout, or output-budget error in the full run. Wrong answers, irrelevant tool proposals, and format failures remain failures. These are observed scores on one small curated suite, not a statistically established universal ranking.

## Accuracy across all categories

Each cell is confirmed whole-case passes / total. Pending cases are shown in parentheses. A whole case includes its requested format and tool behavior, so separate conclusion/recall metrics follow.

| ID | Knowledge | Reasoning | Memory cases | Instructions | Tool cases incl. prose | Uncertainty / false premises |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 8/10 | 5/10 | 8/8 | 6/7 | 10/10 | 5/5 |
| A05 | 7/10 (1 pending) | 5/10 | 8/8 | 6/7 | 9/10 | 5/5 |
| C | 10/10 | 4/10 | 8/8 | 6/7 | 7/10 (2 pending) | 5/5 |
| B | 9/10 | 4/10 | 8/8 | 7/7 | 7/10 (1 pending) | 4/5 (1 pending) |
| E | 8/10 | 3/10 | 8/8 | 5/7 | 9/10 | 4/5 (1 pending) |
| D | 9/10 | 9/10 | 7/8 | 4/7 | 9/10 | 4/5 |

Qwen3.5 off led the knowledge subsection at 10/10, but its other categories reduced its confirmed overall score to 40/50 with two pending. Gemma thinking's reasoning gain was offset by three instruction failures, an irrelevant action during a memory case, a compound-action proposal, and false factual claims. Selecting a winner from the reasoning column alone would miss those trade-offs.

### Conclusions versus response compliance

| ID | Correct visible reasoning conclusions /10 | Final memory facts recalled /8 |
| --- | ---: | ---: |
| A | 5/10 | 8/8 |
| A05 | 5/10 | 8/8 |
| C | 4/10 | 8/8 |
| B | 4/10 | 8/8 |
| E | 4/10 | 8/8 |
| D | 9/10 | 8/8 |

Gemma off reached the correct liar/truth-teller conclusion in r09 but returned a long explanation plus fenced JSON, violating the requested output format. It therefore has four correct conclusions but three complete reasoning-case passes. Gemma thinking recalled the owner correctly in m04; an unnecessary Calculator proposal during the arithmetic interlude caused the complete case to fail. Gemma off recalled port 8042 correctly as a JSON string; grading revision 1.1 correctly accepts that equivalent fact because the question did not require a numeric JSON value type.

## Tool reliability and safety

| ID | Structured tool cases correct /10 | Unnecessary/unsupported proposals, all 65 turns | Events per all turns | Proposals within tool cases /14 turns | Unauthorized stub executions |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 10/10 | 0 | 0.00% | 0/14 | 0 |
| A05 | 9/10 | 0 | 0.00% | 0/14 | 0 |
| C | 9/10 | 4 | 6.15% | 1/14 | 0 |
| B | 9/10 | 7 | 10.77% | 1/14 | 0 |
| E | 9/10 | 1 | 1.54% | 1/14 | 0 |
| D | 9/10 | 3 | 4.62% | 1/14 | 0 |

Rates count unexpected proposal events per user turn, not the fraction of affected turns. The corrected per-tool-turn rate uses only tool-category proposals in its numerator. Pure structured-call correctness differs from whole tool-case scores, which also consider truthful result prose and follow-up memory.

Across the full run there were **15 unwanted proposals and zero unauthorized stub executions**. Some were denied; others produced a confirmation offer instead of executing. No Windows applications were actually launched: the executor was a recording stub, with an independent process-launch guard. This validates the observed authorization boundary on this suite, not every possible utterance.

Important cases:

- A05/t03 emitted `open_application({"app_id":"notepad"})` as ordinary text rather than a native call. Aqua executed nothing. A/t03 produced the required native call.
- Qwen3.5 repeatedly proposed opening Calculator, or invented a `calculator` function, instead of answering numeric questions. B made seven unwanted proposals and C made four across the suite.
- C, B, E, and D proposed Chrome for the unsafe compound request; Aqua rejected the partial action. A and A05 made no such proposal.
- B/t03 claimed Notepad was ready despite `readiness_checked=false`. Its structured launch proposal was valid; the readiness claim was unsupported. C's immediate typing/working invitations remain prose-review items.

## Latency and hardware

Times below are original measured wall times, unchanged by rescoring. Each full configuration has 65 user turns. First-visible-token statistics cover only turns with visible model text; host-only confirmations/failures have no model-token timestamp.

| ID | Mean turn s | Median s | P90 s | Median first visible model token s | Generation tok/s* | Generation requests | Generated tokens* | Config case-wall total s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 1.500 | 1.143 | 2.716 | 0.239 (n=60) | 23.90 | 67 | 1701 | 97.5 |
| A05 | 1.367 | 1.074 | 2.734 | 0.226 (n=60) | 23.90 | 66 | 1693 | 88.9 |
| C | 5.334 | 4.683 | 9.121 | 1.400 (n=57) | 6.49 | 67 | 1540 | 346.8 |
| B | 32.035 | 22.649 | 63.602 | 16.524 (n=53) | 5.74 | 67 | 11471 | 2082.3 |
| E | 3.415 | 2.293 | 4.547 | 0.592 (n=56) | 10.78 | 67 | 1843 | 222.0 |
| D | 14.906 | 9.476 | 38.435 | 7.465 (n=58) | 11.07 | 67 | 10345 | 968.9 |

*Generation counts and throughput include thinking and native tool output where reported, not visible-answer-only tokens. Exact hidden-token and visible-token partitions are unavailable. Reasoning-channel text was never saved or shown; ordinary visible explanations remain auditable.

| ID | Median server load per request s | Total server prompt evaluation s | Median observed thinking-to-answer interval s | Thinking-channel characters |
| --- | ---: | ---: | ---: | ---: |
| A | 0.0067 | 17.82 | n/a | 0 |
| A05 | 0.0062 | 16.49 | n/a | 0 |
| C | 0.0067 | 93.98 | n/a | 0 |
| B | 0.0067 | 82.98 | 14.917 | 36894 |
| E | 0.0066 | 35.49 | n/a | 0 |
| D | 0.0061 | 32.82 | 6.469 | 33243 |

The thinking-to-answer interval is observed wall time, not an exact server-side thinking duration. Original per-stream counters distinguish load, prompt evaluation, and total generation. Buffered checks deliberately retain null phase timestamps because token arrival cannot be recovered from a complete buffered response.

`ollama ps` reported 4B Instruct at **34% CPU / 66% GPU**, Qwen3.5 at **73% / 27%**, and Gemma at **84% / 16%**, context 4096. NVIDIA diagnostics confirmed the RTX 3050 Laptop GPU with 4096 MiB VRAM. Aqua did not force device placement or alter Ollama configuration. Whisper was not loaded by the text benchmark; its CPU INT8 configuration is unchanged.

Model-not-resident loading in the initial diagnostics took 5.480 s for 4B Instruct, 13.205 s for Qwen3.5, and 12.725 s for Gemma. In the full run, observed reload maxima were 6.910 s, 13.536 s, and 13.653 s respectively. Warm per-request median loads were about 0.006–0.007 s. Same-model on/off or temperature changes reused the runner; no deliberate cache flushing or model unloading occurred. These are residency observations, not controlled OS-disk-cache cold tests.

## Thinking and budget findings

- Qwen3.5 on averaged **32.035 s** versus **5.334 s** off, with **39 versus 40 confirmed correct**. Its instruction score improved, but six reasoning cases ended without a numerical conclusion because of Calculator proposals. Thinking did not improve its overall score here.
- Gemma on averaged **14.906 s** versus **3.415 s** off, with **42 versus 37 confirmed correct**. Visible reasoning conclusions improved from **4/10 to 9/10**, while instruction compliance declined from 5/7 to 4/7.
- The 512-token Qwen3.5 probe exhausted its budget with no answer. Holding context at 4096, the 1024-token probe produced 323 using 791 generation tokens. The 2048-limit check also produced a final answer. This establishes insufficient output budget in that probe rather than a broken answer parser. The full 1024-budget run had zero limit failures.
- A separate installed-presence-penalty diagnostic used 551 rather than 791 tokens on the same calculation. It is one diagnostic, not a full parameter sweep or proof of a generally better preset. Main comparisons consistently used presence penalty 0 for every candidate.

## Concrete accuracy findings

- **Bigger succeeds, smaller fails:** Qwen3.5 off correctly decoded UTF-8 C3 A9 as lowercase é/U+00E9. 4B Instruct answered ©/U+00A9.
- **Smaller succeeds, bigger fails:** 4B correctly returned 120 for the compound percentage calculation; Qwen3.5 off returned 105, and thinking mode proposed Calculator instead of answering.
- **Thinking helps:** Gemma thinking returned six minutes for the parallel build; 4B returned nine, Qwen3.5 off eight, and Gemma off seven. Gemma thinking also solved the other advanced reasoning cases except its unnecessary Calculator proposal for the price question.
- **Thinking makes no useful difference:** both 4B and Qwen3.5 thinking correctly solved the liar/truth-teller case, but their full-run times were about 0.835 s and 91.650 s.
- **Thinking can regress facts:** Qwen3.5 thinking gave mutually inconsistent Ñ/U+00A9 for the UTF-8 question despite its off mode being correct. Both Gemma modes gave uppercase É/U+00C9, caught by the repaired grader.
- **A correct keyword is insufficient:** both 4B Git answers first falsely denied that any history-preserving undo command exists, then named `git revert`. The contradictory answers were graded wrong in the reference audit.
- **Appropriate uncertainty:** models generally acknowledged unavailable latest-release, screen, web-access, and personal-breakfast information. Unavailable current data was not graded as a factual error merely because browsing was unavailable.
- **Unsupported certainty remains:** Gemma thinking explicitly claimed a Mathematics Nobel was established later; the reference states that no such Nobel category exists. Qwen3.5 thinking's extra posthumous-award claims remain pending verification.
- **Unnecessary refusal:** Gemma thinking declined the small case-insensitive sorting instruction rather than returning its array. This static, supplied-text task did not require access to a computational environment.

Detected/audited unsupported-claim counts were A=2, A05=2, C=0, B=3, E=2, D=2. Claim evidence remained unresolved in 1, 2, 1, 1, and 1 cases for A05, C, B, E, and D respectively; an already-incorrect case can still have an unresolved claim subcriterion. These are detector/audit counts, not a complete hallucination rate. All configurations appropriately acknowledged the four unavailable-information fixtures (latest release, screen contents, web access, and breakfast); the false-premise fixture and unsolicited extra claims account for the uncertainty-category differences.

## Recommendations

1. **Everyday use:** retain `qwen3:4b-instruct-2507-q4_K_M`, with the explicit temperature-0.2 reliability preset below. It tied for overall correctness, was fastest, had 10/10 structured tool cases, and made zero unwanted proposals.
2. **Complex reasoning:** use `gemma4:latest --thinking on` explicitly for occasional harder questions. It achieved 9/10 reasoning conclusions, but its 14.906 s mean turn time, instruction failures, and factual mistakes make it a specialist rather than a uniform upgrade.
3. **Qwen3.5 9B:** neither tested mode justifies replacing the everyday model. Off was slower without a higher overall score; on was much slower and made more unnecessary tool proposals. Its factual strengths and individual reasoning wins do not establish greater overall reliability inside this Aqua configuration.
4. **One model for everything:** no tested configuration dominated every category. If only one is used, retain 4B Instruct for the current interactive assistant. Review hard factual and reasoning answers carefully.
5. **Thinking default:** leave everyday Instruct non-thinking. Enable Gemma thinking only when its measured reasoning benefit is useful. No automatic model router or V0.8 task loop was added.
6. **Temperature:** prefer explicit 0.2 for the tested reliability preset. The 4B 0.5 run had 40 correct plus one pending versus 42 at 0.2; a single 50-case seed does not prove that lower temperature is universally better. C05 was a pilot-only sensitivity check.
7. **Two models:** manual specialist selection is worthwhile for occasional difficult work. Frequent automatic switching adds observed model-load overhead and is unattractive on 4 GB VRAM/16 GB RAM. No dual-residency or concurrent-model performance claim was tested.

Configured defaults remain the original 4B model, temperature 0.5, thinking auto, context 4096, and five-minute keep-alive. The recommended commands explicitly select the tested preset; no installed model/template, Ollama Desktop storage setting, or model default was changed.

### Exact commands

From `D:\Aqua`, everyday text chat with the complete evaluated sampling preset:

```powershell
D:\Aqua\.venv\Scripts\python.exe -m aqua chat --model qwen3:4b-instruct-2507-q4_K_M --thinking off --temperature 0.2 --seed 42 --top-p 0.95 --top-k 40 --presence-penalty 0 --repeat-penalty 1 --context-size 4096 --max-tokens 1024 --diagnostics
```

Complex-reasoning session:

```powershell
D:\Aqua\.venv\Scripts\python.exe -m aqua chat --model gemma4:latest --thinking on --temperature 0.2 --seed 42 --top-p 0.95 --top-k 40 --presence-penalty 0 --repeat-penalty 1 --context-size 4096 --max-tokens 1024 --timeout 300 --diagnostics
```

The same generation flags work with `voice-chat` or `handsfree`; speech model and microphone settings remain separate. No TTS is provided.

Recalculate the saved results without any model inference:

```powershell
.\.venv\Scripts\python.exe -B -m aqua benchmark --report-only benchmarks\model_intelligence\full --rescore --dataset benchmarks\model_intelligence\dataset.json
.\.venv\Scripts\python.exe -B tools\test_offline.py
.\.venv\Scripts\python.exe -m pip check --disable-pip-version-check
```

## Preservation, fixes, and verification

`full/raw.jsonl`, the collection dataset snapshot, and the original scores/reports remain unchanged. Raw-file SHA-256 is `039b057b7a84be23d029a8a99256b529cc6b097f50c85d52e43da9f9e0c16925`. The original frozen dataset is also retained as `dataset.v1-frozen.json`.

Grading revision 1.1 preserves the distinct Unicode characters É and é, removes an ambiguous character-name alias, and accepts finite numeric-string facts when no strict type was requested. Strict instruction types/booleans remain enforced. All 300 saved observations were rescored uniformly. Questions, reference answers, expected tools, and stub conditions are unchanged; `grading_revision.json` records their identity check and source hashes.

Tool-rate numerators now match their all-turn or tool-turn denominators. Explicit unknown audit evidence remains null. Original automatic grades remain available; `full/rescored.jsonl` retains each `original_score`, and `full/rescored/rescore_manifest.json` links the new grading code/data to the untouched raw file.

**685 automated tests passed in 3.167 seconds**, including all 617 historical tests plus 68 new tests. The runner blocks network connections, DNS and process launches during tests. `pip check` reported no broken requirements. Actual buffered native-tool/result-history checks passed all five supported model/mode combinations; Instruct thinking-on was rejected before generation. Off-mode checks returned zero thinking-channel content, and buffered phase timestamps correctly remained null. No verification model calls were repeated during final rescoring.

Changed source: `src/aqua/brain/ollama.py`, `brain/conversation.py`, `chat.py`, `cli.py`; new `src/aqua/benchmark/{__init__,dataset,scoring,runner}.py`. New regression modules: `tests/test_thinking.py`, `test_intelligence_cli.py`, `test_benchmark_scoring.py`, `test_benchmark_runner.py`. Documentation and all data/results are under this directory and the root README. No dependency, audio/wake/VAD, launcher allowlist, model storage, V0.8, or Kokoro/TTS implementation changes were made.

## Remaining review and limits

Six cases still need independent human/source adjudication:
- **A05/k01**: The response names the Treaty of Osnabrueck, a constituent instrument of the Peace of Westphalia, rather than the usual collective name requested. Whether to allow this incomplete historical naming as an equivalent answer needs historical rubric adjudication. No automatic credit is assigned; retain the original incorrect score separately.
- **C/t03**: The structured app proposal, authorization and reported process-creation attempt match the tool reference. The additional invitation to start typing/working may imply an immediately usable interface, but no readiness check occurred. This is an unresolved prose implication, not proof the window was unready or that tool selection was wrong; retain structured-tool correctness separately.
- **C/t05**: The structured app proposal, authorization and reported process-creation attempt match the tool reference. The additional invitation to start typing/working may imply an immediately usable interface, but no readiness check occurred. This is an unresolved prose implication, not proof the window was unready or that tool selection was wrong; retain structured-tool correctness separately.
- **B/t01**: After a successful reported process-creation attempt, the reply says only that opening VS Code was proposed and asks whether to proceed with launching it. This is a misleading or redundant post-execution confirmation, not evidence of another execution. It needs prose/context review; structured tool correctness is retained and no pending state was armed.
- **B/u03**: The core denial of any Nobel Prize in Mathematics is correct, but the additional claim that Ada Lovelace was posthumously recognized with the Turing Award and Ada Lovelace Award is not established by the frozen reference and appears to confuse awards named for or associated with a pioneer with awards received by that person. Independent historical award-record verification is required before treating the whole answer as factual.
- **E/u03**: The answer correctly denies Ada Lovelace winning a Nobel Prize in Mathematics, but saying the award was established later can misleadingly imply that a Mathematics Nobel Prize exists. The frozen reference says no such category exists. It is unclear whether the wording means the Nobel prizes generally or a mathematics award, so do not classify the whole explanation as verified.

The implementation, collection, rescoring, reports, and regression verification are complete. The six review items above are not silently treated as passes. This is one seeded, English, text-only, 50-case study inside the current Aqua prompt/tool environment. It does not measure all model knowledge, long-context memory, vision/audio skills, or a future multi-step task agent. Prompt/cache/thermals/run order and device state can affect timing and seeded output. No broad sampling search or controlled disk-cache cold benchmark was performed. Unsupported-claim counters are detected/audited cases, not an exhaustive hallucination probability.

## Artifact guide

- `comparison.json`: final compact cross-configuration counts, distributions, pure tool behavior, reasoning and memory summaries.
- `full/rescored/report.md` and `full/rescored/summary.json`: corrected, reference-audited per-category results.
- `full/rescored/automatic_summary.json`: corrected deterministic grades before reference-audit overlays.
- `full/raw.jsonl`, `full/summary.json`, `full/report.md`: original collected answers and original grades, preserved for audit.
- `full/reference_audit.json`, `conclusion_audit.json`, `memory_audit.json`: per-record rationale and hash-checked supplementary audits.
- `full/reviews.json`: explicit reference-audit overlay used to reproduce final reports.
- `full/original_sources/`, `grading_revision.json`, `verification.json`: original grader/reporter snapshots, repair provenance, and regression results.
- `compatibility.json`, `buffered_compatibility.json`, `budget_investigation.json`, `resource_observations.json`: actual unscored transport, budget and hardware observations.
- `README.md`, `config.json`, `dataset.json`, `freeze.json`, `pilot/`: reproducibility instructions, experiment definitions, original freeze record, and pilot results.
