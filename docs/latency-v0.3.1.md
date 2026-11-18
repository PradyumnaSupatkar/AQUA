# Aqua V0.3.1 latency investigation

Investigation date: 2026-11-12. Measurements below are local to the Ryzen 7 4800H, RTX 3050 Laptop GPU with 4 GB VRAM, and 16 GB RAM. They describe specific test runs, not guaranteed response times.

## Findings

The installed `qwen3:4b` is the **Thinking-2507** variant. The official registry manifests for [`qwen3:4b`](https://registry.ollama.ai/v2/library/qwen3/manifests/4b) and [`qwen3:4b-thinking-2507-q4_K_M`](https://registry.ollama.ai/v2/library/qwen3/manifests/4b-thinking-2507-q4_K_M) have the same SHA-256 digest:

```text
359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7
```

That digest matches the installed model. This is stronger evidence than the family name or a library-page badge. Qwen's [official Thinking-2507 model card](https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507) documents a thinking-only model with increased thinking length and a template that inserts the thinking opener. The delay is therefore not simply a display-filter problem or a hidden switch in the original hybrid Qwen3 model.

The measured short-prompt warm runs spent most of their time generating before the first visible answer. Loading and prompt evaluation were small once the runner was resident. Cold runner loading added a separate delay. Mixed CPU/GPU placement also limits generation throughput, but GPU use is working. The approved non-thinking candidate subsequently produced answers much sooner on the same controlled fixtures while using the same GPU offload; removing private generation, rather than gaining GPU layers, was the dominant measured improvement.

These short-prompt results do not account for every previously reported 15-30 second turn. Longer internal generation, conversation length, model residency, and system load can produce different results. No reasoning text, user conversation, or microphone audio was saved for this investigation; only numeric timing and count measurements are retained here.

## Controlled baseline

The controlled runs used the existing installed model, Aqua's `DEFAULT_SYSTEM_PROMPT`, the same short arithmetic fixture, no conversation history, `temperature=0`, `num_predict=1024`, and `keep_alive="5m"`. The reported input count was 81 tokens. Temperature zero is a comparison control, not a recommended new production default: Qwen cautions that greedy decoding can cause repetition in thinking models. [Official Qwen guidance](https://huggingface.co/Qwen/Qwen3-4B#best-practices)

| Run | Context | Load | Prompt evaluation | Generation | Generated tokens | Tokens/s | First visible answer | Complete response |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Runner unloaded | 4096 | 3.522 s | 0.239 s | 3.457 s | 94 | 27.19 | 6.928 s | 7.225 s |
| Warm runner | 4096 | 0.0047 s | 0.0386 s | 3.497 s | 94 | 26.88 | 3.250 s | 3.554 s |
| Warm runner | 2048 | 0.0074 s | 0.0321 s | 4.004 s | 128 | 31.97 | 3.801 s | 4.057 s |

Here, **runner unloaded** means the model runner was removed from RAM/VRAM before the request. The operating-system disk cache was not cleared, so this is not a disk-cache-cold benchmark. All runs produced the correct short arithmetic answer; their visible outputs were 15 characters.

The 4096-context warm run contained 83 incoming thinking chunks, compared with 117 at 2048 context. These are **chunk counts, not exact hidden-token counts**. The observed thinking phase was approximately 3.19 s and 3.75 s respectively. Ollama's `eval_count` includes total generated tokens; it does not by itself provide a separate hidden-token count. No hidden content is retained.

Changing context to 2048 incurred a runner reload on the first affected request: 4.345 s loading and 7.234 s to the first visible answer. Consequently, changing context can initially make a request slower even when the new setting later increases token throughput.

The shorter context improved generation rate in this sample but produced more internal generation and a later answer. It is not a demonstrated end-to-end latency improvement, and it can reduce usable conversational context. Do not change the application's context default on this evidence alone.

## CPU/GPU placement

Ollama reports a `llamacpp` runner using both CPU and CUDA backends. Recent load logs explicitly show the following layer placement; these counts are not inferred from allocated-memory percentages:

| Context | Layers offloaded to GPU | Ollama CPU/GPU placement readout | Runtime allocation | VRAM allocation |
| --- | ---: | --- | ---: | ---: |
| 4096 | 26 / 37 | 34% / 66% | 3,534,917,467 B | 2,344,636,907 B |
| 2048 | 29 / 37 | 28% / 72% | 3,232,917,092 B | 2,340,327,259 B |

The observed loads used batch and microbatch sizes of 512 and one sequence. Reducing context allowed three more layers to be offloaded in these loads. It did not move the entire model onto the GPU.

NVIDIA diagnostics identified the RTX 3050 Laptop GPU with 4096 MiB VRAM and approximately 2524 MiB allocated while idle. Allocation is not a measure of compute utilization; idle GPU utilization is not evidence that inference is CPU-only. Likewise, Ollama's placement percentages should not be interpreted as percentages of execution time.

The candidate at 4096 context later reported the **same** 34% / 66% placement, runtime allocation, and VRAM allocation as the existing model. Its latest load log also showed **26 / 37 layers offloaded to CUDA**, batch size 512, and one sequence. NVIDIA reported 2522 MiB allocated at idle and 52 C. The faster candidate answers therefore did not result from extra layers being moved to the GPU.

## Supported non-thinking options

For the original hybrid Qwen3, Qwen documents a soft `/no_think` instruction and a hard input-template switch, `enable_thinking=False`. Those instructions do not establish a supported non-thinking mode in **Thinking-2507**, whose official card says it supports thinking mode only. [Qwen switching guidance](https://qwen.readthedocs.io/en/latest/getting_started/quickstart.html)

Ollama's [chat API](https://docs.ollama.com/api/chat) supports a `think` parameter but has no documented per-request `raw` or template override. The [generate API](https://docs.ollama.com/api/generate) supports `raw:true`; the [official request types](https://github.com/ollama/ollama/blob/main/api/types.go) also expose a generate-only template override. A manually closed thinking prefix in raw generation is an input-format experiment, not evidence that these thinking-only weights support a genuine non-thinking mode. It would also require separate history serialization and quality validation before use in Aqua.

The earlier `think:false`, `/no_think`, and assistant-prefill probes did not establish a reliable solution for this installed combination. Aqua therefore keeps the working separate answer/thinking channels and does not guess at reasoning in prose. The existing models' templates, weights, digests, and modification dates were preserved, and no persistent Ollama configuration was changed. Only the separately approved candidate was added. No fragile reasoning-removal text filter is introduced.

## Dedicated non-thinking candidate

The exact official candidate is [`qwen3:4b-instruct-2507-q4_K_M`](https://ollama.com/library/qwen3:4b-instruct-2507-q4_K_M). Qwen describes [Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) as a dedicated non-thinking model. Its [Ollama template](https://ollama.com/library/qwen3:4b-instruct-2507-q4_K_M/blobs/eade0a07cac7) does not force a thinking opener.

Verified official manifest digest:

```text
0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0
```

Its model layer is 2,497,280,480 bytes. All declared layers plus the config total **2,497,293,803 bytes**, approximately **2.50 GB / 2.33 GiB**. Some small blobs can be shared with an existing installation, so actual additional storage is measured after installation rather than inferred solely from that total. This candidate is a separate model and does not replace `qwen3:4b`.

It uses the same Qwen3 family, approximately 4B parameters, Q4_K_M quantization, and 36 transformer layers. After explicit download approval, the pull completed and its installed digest matched the official manifest above. Real local inference verified compatibility with Ollama `0.40.16` and the existing dependencies. Its 262K native context is not an appropriate default for this laptop's 4 GB VRAM; the comparison retained 4096 context.

This Ollama installation reports a thinking capability for the candidate because of its Qwen3 architecture even though the exact Instruct-2507 weights and plain generation template are non-thinking. Aqua recognizes the **verified manifest digest**, rather than trusting a model name alone, to identify this known non-thinking model accurately. No thinking flag or text-removal workaround is needed for this candidate. All verified candidate calls reported **zero thinking chunks and zero thinking characters**.

The existing `qwen3:4b` and `gemma4:latest` model digests and modification dates remained unchanged. No model was deleted or replaced, and no new dependencies were installed. Aqua's configured default remains the existing `qwen3:4b`; selecting a different default is a separate decision.

Select the installed candidate explicitly:

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -m aqua chat --model qwen3:4b-instruct-2507-q4_K_M --diagnostics
```

## Paired warm-model results

These paired calls used the same inputs for both models, `temperature=0`, 4096 context, a 1024-token generation budget, and five-minute keep-alive. The recall case used frozen conversation context so the two models received the same prior messages. These are small controlled fixtures, not a broad accuracy benchmark.

| Fixture | Model | Load | Prompt evaluation | Generation | Generated tokens | Tokens/s | First visible answer | Complete response |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Short arithmetic | Thinking | 0.0057 s | 0.1823 s | 3.718 s | 99 | 26.63 | 3.624 s | 3.934 s |
| Short arithmetic | Instruct | 0.0062 s | 0.0396 s | 0.3019 s | 9 | 29.81 | 0.054 s | 0.356 s |
| Frozen-context recall | Thinking | 0.0062 s | 0.0425 s | 7.525 s | 198 | 26.31 | 7.518 s | 7.597 s |
| Frozen-context recall | Instruct | 0.0052 s | 0.1463 s | 0.0731 s | 3 | 41.03 | 0.161 s | 0.234 s |

Both models answered both fixtures correctly. A repeat of the Instruct arithmetic case returned nine generated tokens, 0.065 s to the first visible answer, and 0.361 s total. The recall answer is only three tokens, so its reported token rate is not a stable sustained-throughput estimate.

The arithmetic token rates were fairly similar, while the generated-token totals fell from 99 to 9. Recall fell from 198 total generated tokens to 3. Together with zero candidate thinking chunks, this supports **avoiding private generation** as the main cause of the improvement. It does not show that all longer answers or difficult tasks will have the same speedup.

The candidate's first-load arithmetic call took 4.682 s to load, 4.925 s to first visible text, and 5.218 s overall. The earlier unloaded Thinking call took 6.928 s to first visible text and 7.225 s overall. These runner-load measurements retained warm operating-system caches; they are not guaranteed cold-boot times. Keeping a runner resident still matters even for the faster candidate.

## Application and voice checks

The actual chat application was also checked with its production `temperature=0.5` setting over five turns. It retained and recalled conversation context, treated the context as unknown after `/clear`, covered encryption and authentication in a two-sentence HTTPS explanation, and correctly stated that it could not open VS Code. First visible text ranged from **0.194 to 0.222 s**, with complete replies from **0.252 to 1.622 s** depending on answer length. No thinking chunks or characters were reported.

One Windows cp1252 Unicode-output failure was found and fixed; the complete quality checks passed when rerun with UTF-8 output. These are quick conversational checks, not a large factual-accuracy, multilingual, coding, or reasoning evaluation. Complex reasoning quality may differ from the Thinking model.

A separate end-to-end voice fixture used actual Whisper Small and actual candidate inference, with the speech model loaded once. Whisper load time was **1.663 s**, speech transcription was **2.156 s**, and Ollama's first visible response arrived in **0.143 s**, completing in **0.467 s**. The fixture stayed in memory. Capture was mocked for this fixture and its wall time must not be presented as a live microphone capture measurement. Whisper remained CPU INT8 and Ollama retained automatic CPU/GPU placement.

## Recommendation

Recommend **`qwen3:4b-instruct-2507-q4_K_M` for Aqua's everyday conversational default**, subject to the user's final selection. It provided correct answers in the tested conversational checks and much lower visible latency without a reasoning-removal workaround. Keep the original Thinking model installed and selectable for slower, harder reasoning tasks and ongoing comparison. **The configured default was not changed by this investigation.**

Retain the current 4096 context, `temperature=0.5`, and five-minute keep-alive rather than combining a model change with unproven tuning. Measure more representative conversations before claiming equal accuracy across tasks. Do not force full-GPU loading, reduce the history capacity, or cut generation tokens merely to make a timer look better.

## Safe options and verification

- Keep the conversational runner warm during an active session. The existing five-minute keep-alive avoids repeated loads between nearby turns; a longer per-request interval may help longer pauses but retains RAM/VRAM longer.
- Keep history bounded and avoid unnecessarily large contexts. A smaller context can increase offload and token throughput, but the controlled 2048 run did not improve answer latency and offers less conversation capacity.
- Do not reduce the token budget merely to hide the delay. In a thinking-only model this can end generation before a final answer and sacrifice conversational usefulness.
- Prefer the verified dedicated instruct candidate for responsive everyday chat over unsupported thinking-mode tricks or rewriting generated prose. Preserve the existing model for reasoning tasks and comparison.

The complete automated suite has **248 passing tests**: all **227 original tests** plus **21 new tests**. No new dependencies were installed. The only model download was the explicitly approved Instruct candidate, approximately 2.50 GB. Automated checks use mocked inference and require no network or microphone:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```
