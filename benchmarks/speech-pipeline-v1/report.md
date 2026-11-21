# Speech pipeline benchmark v1 (Qwen streaming into Kokoro)

Run 2026-11-21T16:45:41+00:00 on AMD Ryzen 7 4800H with Radeon Graphics, 16 logical CPUs, 15.4 GB RAM, Windows-11-10.0.26200-SP0. Ollama 0.40.16 with `qwen3:4b-instruct-2507-q4_K_M` (temperature 0.2, seed 42, context 4096, keep-alive 30m); Kokoro v1.0 fp32 `af_heart`, sherpa-onnx 2.0.1.

Each prompt goes through Aqua's ConversationController (real system prompt and tools); the streamed reply feeds SpeechOutput (sentence chunking, spoken-length limit) with real Kokoro synthesis. A timing-only player waits for each chunk's real duration without a sound device. 2 runs per prompt and cell, 3 prompts; the Ollama model was warmed up once per num_thread setting before measuring.

- **First audio**: request sent -> first audio would start (mean).
- **Worst gap**: the longest time the player sat idle waiting for the next chunk, over all runs in the cell; **mean worst gap** averages each run's longest gap; **total gaps**: mean per reply.
- **Qwen tok/s**: Ollama's eval rate while speech ran. **Reply done**: last token received. **Speech end**: last audio finished (means).

| Kokoro threads | Ollama num_thread | First audio s | Worst gap s | Mean worst gap s | Total gaps s | Qwen tok/s | Reply done s | Speech end s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | default | 3.49 | 0.02 | 0.01 | 0.03 | 20.6 | 4.18 | 15.62 |
| 3 | default | 3.21 | 0.02 | 0.01 | 0.03 | 20.7 | 4.24 | 15.40 |
| 4 | default | 3.07 | 0.02 | 0.01 | 0.03 | 20.4 | 4.48 | 15.17 |
| 2 | 6 | 3.34 | 0.02 | 0.01 | 0.03 | 20.0 | 4.23 | 15.11 |
| 3 | 6 | 2.99 | 0.02 | 0.01 | 0.02 | 19.9 | 4.38 | 14.96 |
| 4 | 6 | 2.88 | 0.02 | 0.01 | 0.02 | 19.2 | 4.45 | 15.00 |
| 2 | 4 | 3.16 | 0.01 | 0.01 | 0.03 | 19.3 | 4.62 | 15.12 |
| 3 | 4 | 2.85 | 0.01 | 0.01 | 0.02 | 19.0 | 4.48 | 14.85 |
| 4 | 4 | 2.74 | 0.02 | 0.01 | 0.03 | 18.6 | 4.93 | 14.97 |

**Recommended:** Kokoro 4 threads with Ollama num_thread 4 (smallest worst gap; ties within 0.15 s broken by total gaps, then first audio).

Prompts:

- explanation: “Explain in three sentences how a turbocharger makes a car engine more powerful.”
- short: “What is the capital of Australia?”
- list: “Give me five quick tips for staying focused while studying.”

Per-run results, including reply texts, are in `results.json`.
