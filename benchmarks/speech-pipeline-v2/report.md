# Speech pipeline benchmark v2: first-chunk word caps

Same harness as `speech-pipeline-v1`, one cell only: Kokoro `af_heart` 4 threads, Ollama num_thread 4
(`--llm-threads auto` while speaking), 3 prompts x 2 runs per setting, all run back to back in one session.
Caps are the word limits of the first and second chunks; later chunks stay at 16.
Each subfolder has the full `report.md` and `results.json`.

| Chunk caps | First audio s | Worst gap s | Total gaps s | Qwen tok/s | Speech end s |
| --- | --- | --- | --- | --- | --- |
| 10, 10 (before; v1 run: 2.74 / 0.02 / 0.03) | 3.13 | 0.01 | 0.02 | 15.1 | 14.86 |
| 6, 10 | 2.72 | 1.41 | 0.26 | 14.7 | 14.74 |
| 6, 10 (repeat) | 2.82 | 0.01 | 0.02 | 14.8 | 14.77 |
| **6, 8 (chosen)** | 2.80 | 0.02 | 0.03 | 15.0 | 15.01 |

The 1.41 s gap at 6, 10 came on one "list" run (same reply text as its other run, which had 0.01 s) and did
not recur in the repeat; 6, 8 had no gap over 0.3 s at the same first audio, so Aqua now uses 6, 8, then 16.
Qwen ran slower than in v1 (~15 vs ~18.6 tok/s) because of background load during this session, so compare rows
within this table rather than against v1.
