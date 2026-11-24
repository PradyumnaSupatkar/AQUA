# Third-party notices

Aqua's own source code is licensed under [Apache-2.0](LICENSE). Aqua uses, but does not include, the
components below. They are installed separately (Python dependencies via `pip`, speech recognition models
via `aqua setup-model`, voice models via `aqua setup-voice`) and keep their own licences. Check each licence before redistributing them or using
them commercially.

## Speech recognition models (downloaded by `aqua setup-model`)

| Model | Use | Source (pinned revision, every file by SHA-256) | Licence |
| --- | --- | --- | --- |
| **Parakeet TDT 0.6B v3** (int8 ONNX), by NVIDIA | Default recogniser (`--asr parakeet`); `aqua setup-model --model parakeet --download` | [csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8), revision `ed5d745623472f97cfc2e6c2916ee248798fc5a8`; pins in `src/aqua/speech/parakeet.py` | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) |
| Whisper Small / Base, by OpenAI (CTranslate2 conversions by Systran) | Fallback recogniser (`--asr whisper`) | [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small), [Systran/faster-whisper-base](https://huggingface.co/Systran/faster-whisper-base); pins in `src/aqua/speech/models.py` | MIT |

**Attribution (CC-BY-4.0):** Aqua's default speech recogniser is **Parakeet TDT 0.6B v3** by **NVIDIA**,
<https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3>, licensed under the Creative Commons Attribution 4.0
International licence (<https://creativecommons.org/licenses/by/4.0/>). Aqua uses the int8 ONNX export made by
the [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) project (k2-fsa); that export quantizes the
model's weights to int8. Aqua does not modify the model files; it only pads each utterance with silence before
recognition.

### Benchmark-only models (`tools/asr_models.py`; not used by Aqua at runtime)

These were compared in `benchmarks/asr-v1` and are downloaded only by that tool, into `.cache/asr-models`.

| Model | Source (pinned) | Licence |
| --- | --- | --- |
| Parakeet TDT 0.6B v2 (English), by NVIDIA (int8 ONNX by sherpa-onnx) | [csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8) ([model card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2)) | CC-BY-4.0 |
| Moonshine v2 base and tiny (English, quantized 2026-02-27), by Moonshine AI (Useful Sensors) | [csukuangfj2/sherpa-onnx-moonshine-base-en-quantized-2026-02-27](https://huggingface.co/csukuangfj2/sherpa-onnx-moonshine-base-en-quantized-2026-02-27), [csukuangfj2/sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27](https://huggingface.co/csukuangfj2/sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27) (a k2-fsa organisation member's account) | MIT for the English models (the repos' `LICENSE`, section 1). Moonshine models for other languages use the non-commercial Moonshine Community License. |
| Whisper small.en, by OpenAI (CTranslate2 conversion by Systran) | [Systran/faster-whisper-small.en](https://huggingface.co/Systran/faster-whisper-small.en) | MIT |

## Speech output dependency

| Component | Version | Licence | Notes |
| --- | --- | --- | --- |
| [piper_phonemize](https://github.com/csukuangfj/piper-phonemize) | 1.5.9 (Windows cp312 wheel, pinned by SHA-256 in `pyproject.toml`) | GPL-3.0 | Converts text to phonemes for speech output. sherpa-onnx 2.x no longer does this itself. |
| [espeak-ng](https://github.com/espeak-ng/espeak-ng) | bundled in piper_phonemize, with its `espeak-ng-data` | GPL-3.0 | The phonemizer engine and pronunciation data used through piper_phonemize. |

## Voice models (downloaded by `aqua setup-voice`)

All archives come from the official [k2-fsa/sherpa-onnx `tts-models` release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models)
and are pinned by size and SHA-256 in `src/aqua/voice/setup.py`.

| Voice | Archive | Model licence | Training data licence |
| --- | --- | --- | --- |
| Kokoro v1.0 (default engine; default voice Heart `af_heart`) | `kokoro-multi-lang-v1_0` | Apache-2.0 ([hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M); `LICENSE` included in the archive) | See the Kokoro-82M model card |
| Piper `en_US-lessac-medium` (optional) | `vits-piper-en_US-lessac-medium` | Piper voice ([rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)) | Blizzard Challenge 2013 Lessac dataset licence: <https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html> |
| Piper `en_US-ryan-medium` (optional) | `vits-piper-en_US-ryan-medium` | Piper voice ([rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)) | RyanSpeech, **CC BY-NC-SA 4.0 (non-commercial)**, per the voice's `MODEL_CARD` |
| KittenTTS nano v0.2 | `kitten-nano-en-v0_2-fp16` | Apache-2.0 ([KittenML/kitten-tts-nano-0.2](https://huggingface.co/KittenML/kitten-tts-nano-0.2); `LICENSE` included in the archive) | See the KittenTTS model card |

Each installed voice directory keeps the archive's own `LICENSE`, `README.md` or `MODEL_CARD`.

**Piper voices are optional downloads only and never a default.** Their training data is licensed more narrowly
than the models above: RyanSpeech (ryan) is CC BY-NC-SA 4.0, non-commercial and share-alike, and lessac uses the
Blizzard Challenge 2013 Lessac licence. Aqua installs them only when asked by name
(`aqua setup-voice --engine piper [--voice lessac|ryan] --download`), and `setup-voice` repeats this notice
whenever it handles them.
