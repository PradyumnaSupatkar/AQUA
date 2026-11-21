# Text-to-speech benchmark v1

Run 2026-11-21T15:58:35+00:00 on AMD Ryzen 7 4800H with Radeon Graphics, 16 logical CPUs, 15.4 GB RAM, Windows-11-10.0.26200-SP0.
Python 3.12.6, sherpa-onnx 2.0.1, numpy 2.5.4, piper_phonemize 1.5.9, Ollama 0.40.16 with `qwen3:4b-instruct-2507-q4_K_M` (3.3 GiB loaded, 2.2 GiB in GPU memory).
While busy, Qwen streamed about 19.7 chunks/s (≈ tokens/s) alongside the voice.

Engines run through Aqua's `aqua.voice.engines` on CPU (`provider=cpu`, `max_num_sentences=1`, speed 1.0, one sentence per `generate` call, as in the pipeline). Text is phonemized by `aqua.voice.phonemes` (espeak-ng via piper_phonemize; Kokoro lexicon first), and phonemization is included in the synthesis time. Each configuration ran in a fresh process after one untimed warm-up sentence. Measurement voices: Kokoro `af_heart`, Piper lessac/ryan, Kitten `expr-voice-2-f` (not a default choice).

- **Load**: model construction time. **RAM**: peak working set of the process, and the part added by the engine over a bare Python baseline.
- **First audio**: synthesis time of the reply's first sentence ("Done."); Aqua plays a sentence once it is fully synthesised. **Question**: the same for a typical question sentence.
- **RTF**: synthesis time / audio duration over all 12 sentences (lower is faster; < 1 is faster than real time). **Worst RTF**: slowest single sentence.
- **CPU**: process CPU time during synthesis / wall time, as % of one core (and of the machine).
- **Idle**: Qwen loaded in Ollama, not generating. **Busy**: Qwen generating continuously on the same machine (GPU offload as Ollama chooses).

| Engine | Threads | Qwen | Load s | Peak RAM MB (engine) | First audio s | Question s | RTF | Worst RTF | CPU % core (machine) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| kokoro-fp32 | 2 | idle | 2.25 | 711 (687) | 0.478 | 1.250 | 0.533 | 0.808 | 199 (12) |
| kokoro-fp32 | 4 | idle | 2.31 | 712 (687) | 0.340 | 0.869 | 0.374 | 0.575 | 397 (25) |
| kokoro-int8 | 2 | idle | 2.51 | 531 (506) | 1.366 | 3.315 | 1.396 | 2.361 | 170 (11) |
| kokoro-int8 | 4 | idle | 2.39 | 531 (507) | 1.203 | 3.012 | 1.293 | 2.084 | 300 (19) |
| piper-lessac | 2 | idle | 1.15 | 302 (277) | 0.031 | 0.154 | 0.064 | 0.069 | 198 (12) |
| piper-lessac | 4 | idle | 1.08 | 304 (279) | 0.022 | 0.128 | 0.046 | 0.053 | 387 (24) |
| piper-ryan | 2 | idle | 1.10 | 298 (274) | 0.025 | 0.147 | 0.062 | 0.071 | 196 (12) |
| piper-ryan | 4 | idle | 1.09 | 297 (273) | 0.027 | 0.105 | 0.050 | 0.078 | 387 (24) |
| kitten-nano | 2 | idle | 1.45 | 300 (276) | 0.356 | 1.523 | 0.289 | 0.298 | 180 (11) |
| kitten-nano | 4 | idle | 1.47 | 301 (276) | 0.346 | 1.448 | 0.273 | 0.279 | 335 (21) |
| kokoro-fp32 | 2 | busy | 4.59 | 711 (687) | 1.272 | 2.117 | 1.096 | 2.682 | 188 (12) |
| kokoro-fp32 | 4 | busy | 3.87 | 712 (687) | 0.676 | 1.562 | 0.689 | 1.144 | 389 (24) |
| kokoro-int8 | 2 | busy | 4.03 | 531 (506) | 2.141 | 4.856 | 2.058 | 3.708 | 171 (11) |
| kokoro-int8 | 4 | busy | 4.11 | 531 (507) | 1.888 | 4.428 | 1.855 | 3.279 | 309 (19) |
| piper-lessac | 2 | busy | 2.07 | 305 (281) | 0.054 | 0.255 | 0.105 | 0.145 | 195 (12) |
| piper-lessac | 4 | busy | 2.03 | 304 (280) | 0.052 | 0.201 | 0.082 | 0.098 | 390 (24) |
| piper-ryan | 2 | busy | 2.02 | 295 (270) | 0.049 | 0.218 | 0.102 | 0.114 | 199 (12) |
| piper-ryan | 4 | busy | 1.98 | 300 (276) | 0.042 | 0.176 | 0.083 | 0.108 | 375 (23) |
| kitten-nano | 2 | busy | 2.72 | 301 (276) | 0.532 | 2.195 | 0.418 | 0.438 | 180 (11) |
| kitten-nano | 4 | busy | 2.67 | 301 (276) | 0.513 | 2.078 | 0.398 | 0.419 | 340 (21) |

Sentences:

1. Done.
2. Opening Notepad now.
3. The launch attempt succeeded.
4. I couldn't find that window.
5. Would you like me to open Visual Studio Code?
6. Calculator is open and ready.
7. It's twenty past three in the afternoon.
8. Okay, I've cancelled that request.
9. Formula One is the highest class of international open-wheel single-seater racing, sanctioned by the FIA.
10. I can open approved apps, describe the windows on your screen, and type exact text into an empty Notepad document.
11. Your words are processed locally on this laptop, so nothing you say is sent to the cloud or saved to disk.
12. The rest is on screen.

Raw per-sentence timings are in `results.json`. Audio was not saved.
