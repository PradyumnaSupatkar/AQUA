# Third-party notices

Aqua's own source code is licensed under [Apache-2.0](LICENSE). Aqua uses, but does not include, the
components below. They are installed separately (Python dependencies via `pip`, voice models via
`aqua setup-voice`) and keep their own licences. Check each licence before redistributing them or using
them commercially.

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
