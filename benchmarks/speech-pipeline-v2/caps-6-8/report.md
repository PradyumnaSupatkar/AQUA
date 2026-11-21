# Speech pipeline benchmark caps-6-8 (Qwen streaming into Kokoro)

Run 2026-11-21T17:12:55+00:00 on AMD Ryzen 7 4800H with Radeon Graphics, 16 logical CPUs, 15.4 GB RAM, Windows-11-10.0.26200-SP0. Ollama 0.40.16 with `qwen3:4b-instruct-2507-q4_K_M` (temperature 0.2, seed 42, context 4096, keep-alive 30m); Kokoro v1.0 fp32 `af_heart`, sherpa-onnx 2.0.1. Chunk word caps: 6, 8, then 16.

Each prompt goes through Aqua's ConversationController (real system prompt and tools); the streamed reply feeds SpeechOutput (sentence chunking, spoken-length limit) with real Kokoro synthesis. A timing-only player waits for each chunk's real duration without a sound device. 2 runs per prompt and cell, 3 prompts; the Ollama model was warmed up once per num_thread setting before measuring.

- **First audio**: request sent -> first audio would start (mean).
- **Worst gap**: the longest time the player sat idle waiting for the next chunk, over all runs in the cell; **mean worst gap** averages each run's longest gap; **total gaps**: mean per reply.
- **Qwen tok/s**: Ollama's eval rate while speech ran. **Reply done**: last token received. **Speech end**: last audio finished (means).

| Kokoro threads | Ollama num_thread | First audio s | Worst gap s | Mean worst gap s | Total gaps s | Qwen tok/s | Reply done s | Speech end s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 4 | 4 | 2.80 | 0.02 | 0.01 | 0.03 | 15.0 | 6.05 | 15.01 |

**Recommended:** Kokoro 4 threads with Ollama num_thread 4 (smallest worst gap; ties within 0.15 s broken by total gaps, then first audio).

Prompts:

- explanation: “Explain in three sentences how a turbocharger makes a car engine more powerful.”
- short: “What is the capital of Australia?”
- list: “Give me five quick tips for staying focused while studying.”

Per-run results, including reply texts, are in `results.json`.
