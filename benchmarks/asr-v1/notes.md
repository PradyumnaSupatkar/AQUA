# asr-v1 notes: speed, method, sources, recommendation

Read with `report.md` (the generated table and every transcript that differs from its prompt).

## Why Whisper Small was ~10 s here instead of 2.2-2.6 s

Measured on one recording ("open Chrome", 2.6 s), changing one setting at a time:

| Setting | Time |
|---|---|
| Aqua now: Small, automatic language, beam 5, VAD filter | 11.3 s |
| language=en | 5.8 s |
| language=en, beam 1 | 5.3 s |
| language=en, no VAD filter | 5.7 s |
| language=en, 8 threads | 5.5 s |
| language=en, Base model | 1.8 s |
| language=en, clip padded to 30 s | 5.9 s |

- **Automatic language detection doubles the cost**: it runs an extra encoder pass. Aqua is
  English-only, so `language=en` halves recognition time with identical transcripts (the two
  Small rows in `report.md` match word for word).
- Beam size, the VAD filter, thread count and audio length change little: Whisper always encodes
  a 30 s window, and the encoder dominates.
- **The CPU was throttled**: under load the Ryzen 7 4800H ran at 48-51 % of its 2.9 GHz base
  clock (~1.4 GHz) at only ~21 % utilisation, instead of boosting toward 4.2 GHz. Nothing else was
  using the CPU. This explains Small + language=en taking 5.5 s against 2.15 s in the earlier
  hands-free measurement, and Ollama's halved decode speed in agent-v1 round 5. Likely causes: the
  vendor performance mode (Armoury Crate / Razer Cortex are running), a power limit, or heat. The
  throttle varied: in the final benchmark run Small (auto) took 6.15 s median, not 10-11 s.
  **All models were measured under the same conditions, so the ratios hold; absolute times are
  2-3x slower than an unthrottled CPU.**

## Method notes

- 30 commands recorded by the user (`tools/record_asr_set.py`), 16 kHz, no audio committed.
- Each clip gets 0.5 s of silence before and after, for every model alike. The recordings start
  the instant Enter is pressed; without the margin the Parakeet transducers returned nothing for
  several clips that start with speech ("open VS Code", "pause the video"). Live hands-free audio
  always has this margin (0.5 s pre-roll, 0.8 s trailing silence).
- Each model runs in its own process: load time and RAM are clean. 4 threads, one untimed warm-up.
- Hotwords on the Parakeet models use sherpa-onnx `modified_beam_search` with a `bpe.vocab` built
  from `tokens.txt` (scores by token id, i.e. merge order), because the exports ship no BPE model.
  Treat those rows as indicative: an exact SentencePiece model might bias differently.

## Sources and licences

| Model | Source (pinned revision, every file by SHA-256 / git blob) | Licence |
|---|---|---|
| Whisper Small | already installed, `D:\Aqua\models\faster-whisper-small` | MIT |
| Whisper small.en | Systran/faster-whisper-small.en | MIT |
| Parakeet TDT 0.6B v3 / v2 int8 | csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8, -v2-int8 (the sherpa-onnx author) | CC-BY-4.0 (NVIDIA): free for commercial use; requires attribution |
| Moonshine v2 base / tiny en | csukuangfj2/sherpa-onnx-moonshine-*-en-quantized-2026-02-27 (approved: k2-fsa organisation member) | MIT for the English models (LICENSE section 1); other languages are under the non-commercial Moonshine Community License |

The k2-fsa GitHub release served ~60-80 KB/s here, so the Hugging Face copies were used instead.

## Findings

- **Parakeet TDT 0.6B v3 int8 is the most accurate and ~6x faster than Whisper Small with
  language=en** (0.56 s vs 3.20 s median; 11x faster than Aqua's current automatic-language
  setting). It needs more RAM (~700 MB vs ~300 MB) and loads in 3 s instead of 2 s. v2 (English
  only) is about as accurate and as fast.
- **"write" vs "right" is not an acoustic problem.** They sound the same, and every model hears
  "Right ..." (or "Bright", "Ride"). Only Whisper with hotwords got 2 of 4, and it also produced
  "WriteMeetingAtNone". This has to be solved after recognition: a sentence that starts with
  "right"/"ride"/"wright"/"bright" followed by text to type is almost certainly "write".
- **"pause it" is too short**: most models hear "What is it?" / "Was it?". "pause the music" and
  "pause the video" are fine for Whisper and Parakeet. "paws" never appeared.
- **Hotwords hurt Parakeet** (WER up, empty outputs, hallucinated "Spotify, pause, pause ...") and
  give Whisper only the two "write" wins. Not recommended as configured.
- Parakeet writes numbers as words ("ten ADP" for 1080p, "two minutes thirty"); Whisper writes
  "1080p". Aqua's tools would need to accept spoken numbers (most already parse "ten seconds").
- Moonshine v2 is very fast (0.09-0.15 s) and small, but far less accurate (29-36 % WER).
- Everyone fails "who wrote Pride and Prejudice" ("Hodo ...") and "close notepad" is often
  "Close not bad": a few recordings may simply be hard; more data would help.

## Recommendation

1. **Now:** default to `language=en` for Whisper. It halves recognition time and changes no
   transcript. Aqua is English-only.
2. **Next:** move to **Parakeet TDT 0.6B v3 int8** through sherpa-onnx (already a dependency).
   It has the lowest WER here and is about 6x faster. Its licence is CC-BY-4.0: add NVIDIA
   attribution to THIRD_PARTY_NOTICES.md when adopting it. Keep Whisper Small selectable, and
   before switching, confirm live (hands-free) that spoken numbers ("ten eighty p") reach the
   tools correctly.
3. **Regardless of recogniser:** handle "write"/"right" in text (the leading "right/ride/bright +
   text" rule above), and fix the CPU throttle before measuring latency again.
