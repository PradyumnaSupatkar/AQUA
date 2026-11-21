"""Local text-to-speech engines built on the installed sherpa-onnx OfflineTts API.

Nothing here downloads anything. Each engine validates its local model
directory before importing sherpa-onnx, so a missing voice fails fast with a
clear message and no native code is loaded. sherpa-onnx 2.x ignores the voices'
espeak-ng-data, so text is phonemized by aqua.voice.phonemes (piper_phonemize)
and passed to generate() as phoneme codepoints.
"""

from math import isfinite
from pathlib import Path
from threading import Lock
from typing import Protocol, runtime_checkable

SETUP_COMMAND = "aqua setup-voice --download"
VOICE_ROOT = Path(r"D:\Aqua\models")
# Directories that `aqua setup-voice` installs into (one per Piper voice).
ENGINE_DIRS = {
    "kokoro": VOICE_ROOT / "tts-kokoro",
    "kokoro-int8": VOICE_ROOT / "tts-kokoro-int8",
    "piper": VOICE_ROOT / "tts-piper",   # prefix only; each Piper voice is tts-piper-<voice>
    "kitten": VOICE_ROOT / "tts-kitten",
}
ENGINE_NAMES = ("kokoro", "kokoro-int8", "piper", "kitten", "none")
DEFAULT_ENGINE = "kokoro"

# English speakers of sherpa-onnx kokoro-multi-lang-v1_0, as listed in the
# sherpa-onnx docs (ids 0-19 American, 20-27 British; 28-53 are other languages).
KOKORO_ENGLISH_VOICES = {
    "af_alloy": ("female", "American"), "af_aoede": ("female", "American"), "af_bella": ("female", "American"),
    "af_heart": ("female", "American"), "af_jessica": ("female", "American"), "af_kore": ("female", "American"),
    "af_nicole": ("female", "American"), "af_nova": ("female", "American"), "af_river": ("female", "American"),
    "af_sarah": ("female", "American"), "af_sky": ("female", "American"),
    "am_adam": ("male", "American"), "am_echo": ("male", "American"), "am_eric": ("male", "American"),
    "am_fenrir": ("male", "American"), "am_liam": ("male", "American"), "am_michael": ("male", "American"),
    "am_onyx": ("male", "American"), "am_puck": ("male", "American"), "am_santa": ("male", "American"),
    "bf_alice": ("female", "British"), "bf_emma": ("female", "British"), "bf_isabella": ("female", "British"),
    "bf_lily": ("female", "British"),
    "bm_daniel": ("male", "British"), "bm_fable": ("male", "British"), "bm_george": ("male", "British"),
    "bm_lewis": ("male", "British"),
}
KOKORO_VOICES = {name: index for index, name in enumerate(KOKORO_ENGLISH_VOICES)}
# kitten-nano-en-v0_2-fp16 (sherpa-onnx docs): suffix -m male, -f female.
KITTEN_VOICES = {
    name: index for index, name in enumerate((
        "expr-voice-2-m", "expr-voice-2-f", "expr-voice-3-m", "expr-voice-3-f",
        "expr-voice-4-m", "expr-voice-4-f", "expr-voice-5-m", "expr-voice-5-f",
    ))
}
# Piper ships one single-speaker model per voice, each in its own directory.
PIPER_VOICES = {"lessac": ("female", "American"), "ryan": ("male", "American")}
# Provisional until a default is chosen by ear; Piper has none (pass --voice).
DEFAULT_VOICES = {"kokoro": "af_heart", "kokoro-int8": "af_heart", "piper": None, "kitten": "expr-voice-2-f"}


def engine_dir(engine: str, voice: str | None = None) -> Path:
    if engine == "piper":
        if voice is None or str(voice).casefold() not in PIPER_VOICES:
            raise VoiceError(f"Choose a Piper voice with --voice: {', '.join(PIPER_VOICES)}.")
        return VOICE_ROOT / f"tts-piper-{str(voice).casefold()}"
    return ENGINE_DIRS[engine]


def setup_hint(engine: str, voice: str | None = None) -> str:
    extra = f" --voice {voice}" if engine == "piper" and voice else ""
    return f"aqua setup-voice --engine {engine}{extra} --download"


class VoiceError(RuntimeError):
    """Speech output could not be set up or produce audio."""


@runtime_checkable
class SpeechEngine(Protocol):
    sample_rate: int

    def synthesize(self, text: str):
        """Return mono float32 samples in [-1, 1] for ``text``."""


def _speed(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not .5 <= value <= 2:
        raise VoiceError("Speech rate must be finite and between 0.5 and 2.0.")
    return float(value)


def _threads(value) -> int:
    if type(value) is not int or not 1 <= value <= 8:
        raise VoiceError("num_threads must be an integer from 1 to 8.")
    return value


def _speaker_id(voice, table: dict[str, int], engine: str) -> int:
    text = str(voice).strip()
    if text.isdigit():
        return int(text)
    if text.casefold() in table:
        return table[text.casefold()]
    known = ", ".join(table) if table else "a numeric speaker id"
    raise VoiceError(f"Unknown {engine} voice {voice!r}; use one of: {known}.")


def _missing(engine: str, path: Path, missing, voice=None) -> VoiceError:
    return VoiceError(
        f"The {engine} voice is not installed or is incomplete at {path}; missing: "
        f"{', '.join(missing)}. No download was attempted. Run '{setup_hint(engine, voice)}' to install it."
    )


def _onnx_model(engine: str, path: Path, preferred: tuple[str, ...]) -> Path | None:
    for name in preferred:
        if (path / name).is_file() and (path / name).stat().st_size:
            return path / name
    candidates = sorted(item for item in path.glob("*.onnx") if item.is_file() and item.stat().st_size)
    if len(candidates) > 1:
        raise VoiceError(
            f"The {engine} voice directory {path} contains several .onnx models; keep exactly one."
        )
    return candidates[0] if candidates else None


def _validate(engine: str, model_dir, files: tuple[str, ...], preferred: tuple[str, ...],
              voice=None) -> tuple[Path, Path]:
    path = Path(model_dir if model_dir is not None else engine_dir(engine, voice)).expanduser()
    try:
        path = path.resolve()
        missing = [name for name in files if not (path / name).is_file() or (path / name).stat().st_size == 0]
        model = _onnx_model(engine, path, preferred) if path.is_dir() else None
    except OSError as exc:
        raise VoiceError(f"Could not inspect the {engine} voice files in {path}: {exc}") from exc
    if model is None:
        missing.insert(0, " or ".join(preferred) if preferred else "*.onnx")
    if missing:
        raise _missing(engine, path, missing, voice)
    return path, model


def _configure_runtime() -> None:
    """Disable telemetry in sherpa's bundled ONNX Runtime before model creation."""
    from aqua.wake.models import WakeError
    from aqua.wake.privacy import configure_runtime
    try:
        configure_runtime()
    except WakeError as exc:
        raise VoiceError(f"Speech output disabled: {exc}") from exc


class _SherpaEngine:
    """Shared OfflineTts wrapper; subclasses provide the model config."""

    engine = ""
    files: tuple[str, ...] = ("tokens.txt",)
    preferred: tuple[str, ...] = ()
    voices: dict[str, int] = {}

    def __init__(self, model_dir: str | Path | None = None, *, voice: str | int | None = None,
                 speed: float = 1.0, num_threads: int = 2):
        self.speed = _speed(speed)
        threads = _threads(num_threads)
        chosen = DEFAULT_VOICES[self.engine] if voice is None else voice
        self.voice = None if chosen is None else str(chosen)
        self.speaker_id = self.resolve_voice(self.voice) if self.voice is not None else 0
        self.model_dir, self.model_path = _validate(self.engine, model_dir, self.files, self.preferred, self.voice)
        _configure_runtime()
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise VoiceError("Speech output needs sherpa-onnx, a core Aqua dependency; reinstall Aqua with pip install -e .") from exc
        try:
            model_config = sherpa_onnx.OfflineTtsModelConfig(
                **{self.engine_field: self._model_config(sherpa_onnx)},
                num_threads=threads, debug=False, provider="cpu",
            )
            config = sherpa_onnx.OfflineTtsConfig(model=model_config, max_num_sentences=1)
            if hasattr(config, "validate") and not config.validate():
                raise VoiceError(f"The {self.engine} voice configuration in {self.model_dir} is invalid.")
            self._tts = sherpa_onnx.OfflineTts(config)
            self._generation_config = sherpa_onnx.GenerationConfig
            self.sample_rate = int(self._tts.sample_rate)
            speakers = int(self._tts.num_speakers)
            self.phonemizer = self._phonemizer(str(getattr(self._tts, "lang", "") or "en-us"))
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceError(f"Could not load the {self.engine} voice from {self.model_dir}: {exc}") from exc
        if speakers > 0 and self.speaker_id >= speakers:
            raise VoiceError(f"The {self.engine} voice has {speakers} speakers; id {self.speaker_id} is out of range.")
        self.num_speakers = speakers
        self._lock = Lock()

    engine_field = ""

    def resolve_voice(self, voice) -> int:
        """Speaker id for a voice name or numeric id of this engine."""
        return _speaker_id(voice, self.voices, self.engine)

    def _phonemizer(self, lang: str):
        from .phonemes import EspeakPhonemizer
        return EspeakPhonemizer(lang)

    def _model_config(self, sherpa_onnx):
        raise NotImplementedError

    def _path(self, name: str) -> str:
        return str(self.model_dir / name)

    def synthesize(self, text: str):
        import numpy as np

        if not isinstance(text, str) or not text.strip():
            return np.zeros(0, dtype=np.float32)
        codepoints = self.phonemizer.codepoints(text)
        if not codepoints:
            return np.zeros(0, dtype=np.float32)
        config = self._generation_config()
        config.sid, config.speed, config.phoneme_codepoints = self.speaker_id, self.speed, codepoints
        with self._lock:  # One OfflineTts is not shared across concurrent calls.
            audio = self._tts.generate("", config)
        samples = np.asarray(audio.samples, dtype=np.float32).reshape(-1)
        if not np.isfinite(samples).all():
            raise VoiceError(f"The {self.engine} voice produced invalid audio.")
        return samples


class KokoroEngine(_SherpaEngine):
    """kokoro-multi-lang-v1_0, English voices only.

    The lexicon and espeak-ng accent follow the voice: British voices (bf_/bm_,
    ids 20-27) use lexicon-gb-en.txt and en-gb, the others lexicon-us-en.txt
    and en-us. Chinese assets are not installed or used. Phonemes are computed
    in aqua.voice.phonemes (sherpa-onnx 2.x no longer phonemizes itself).
    """

    engine, engine_field = "kokoro", "kokoro"
    files = ("voices.bin", "tokens.txt", "lexicon-us-en.txt", "lexicon-gb-en.txt")
    preferred = ("model.onnx",)
    voices = KOKORO_VOICES

    @property
    def british(self) -> bool:
        voice = self.voice or ""
        return (KOKORO_ENGLISH_VOICES.get(voice.casefold(), ("", ""))[1] == "British"
                or (voice.isdigit() and 20 <= int(voice) <= 27))

    def _phonemizer(self, lang: str):
        from .phonemes import KokoroPhonemizer
        lexicon = "lexicon-gb-en.txt" if self.british else "lexicon-us-en.txt"
        return KokoroPhonemizer(self.model_dir / lexicon, self.model_dir / "tokens.txt",
                                # This espeak-ng build calls British English "en" (no "en-gb").
                                "en" if self.british else "en-us")

    def _model_config(self, sherpa_onnx):
        lexicon = "lexicon-gb-en.txt" if self.british else "lexicon-us-en.txt"
        return sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=str(self.model_path), voices=self._path("voices.bin"), tokens=self._path("tokens.txt"),
            lexicon=self._path(lexicon), length_scale=1.0,
        )


class KokoroInt8Engine(KokoroEngine):
    engine = "kokoro-int8"
    preferred = ("model.int8.onnx",)


class PiperEngine(_SherpaEngine):
    """Piper voices exported for sherpa-onnx's VITS runtime.

    Each voice is a separate single-speaker model in tts-piper-<voice>;
    --voice selects the directory and the speaker id is always 0.
    """

    engine, engine_field = "piper", "vits"
    files = ("tokens.txt",)
    voices: dict[str, int] = {}

    def resolve_voice(self, voice) -> int:
        text = str(voice).strip().casefold()
        if text in PIPER_VOICES or text == "0":
            return 0
        raise VoiceError(f"Unknown piper voice {voice!r}; use one of: {', '.join(PIPER_VOICES)}.")

    def _model_config(self, sherpa_onnx):
        return sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(self.model_path), tokens=self._path("tokens.txt"),
            length_scale=1.0,
        )


class KittenEngine(_SherpaEngine):
    engine, engine_field = "kitten", "kitten"
    files = ("voices.bin", "tokens.txt")
    preferred = ("model.fp16.onnx", "model.onnx")
    voices = KITTEN_VOICES

    def _model_config(self, sherpa_onnx):
        return sherpa_onnx.OfflineTtsKittenModelConfig(
            model=str(self.model_path), voices=self._path("voices.bin"), tokens=self._path("tokens.txt"),
            length_scale=1.0,
        )


class NullEngine:
    """Speech pipeline without audio, e.g. for --tts-engine none."""

    def __init__(self, sample_rate: int = 24000):
        self.sample_rate = sample_rate

    def synthesize(self, text: str):
        import numpy as np
        return np.zeros(0, dtype=np.float32)


class FakeEngine:
    """Deterministic test engine: short constant arrays, recorded inputs.

    ``fail`` raises on every call; ``fail_on`` raises only for that 1-based
    call number. ``gate`` (a threading.Event) blocks synthesis until set.
    """

    def __init__(self, sample_rate: int = 16000, *, samples_per_char: int = 4,
                 fail: bool = False, fail_on: int | None = None, gate=None):
        self.sample_rate = sample_rate
        self.samples_per_char = samples_per_char
        self.fail, self.fail_on, self.gate = fail, fail_on, gate
        self.texts: list[str] = []
        self._lock = Lock()

    def synthesize(self, text: str):
        import numpy as np

        with self._lock:
            self.texts.append(text)
            call = len(self.texts)
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail or call == self.fail_on:
            raise VoiceError("synthetic engine failure")
        return np.full(max(1, len(text)) * self.samples_per_char, .1, dtype=np.float32)


ENGINE_CLASSES = {"kokoro": KokoroEngine, "kokoro-int8": KokoroInt8Engine, "piper": PiperEngine,
                  "kitten": KittenEngine}


def load_engine(name: str = DEFAULT_ENGINE, *, model_dir=None, voice=None, speed: float = 1.0,
                num_threads: int = 2) -> SpeechEngine:
    if name == "none":
        return NullEngine()
    if name not in ENGINE_CLASSES:
        raise VoiceError(f"Unknown speech engine {name!r}; choose one of: {', '.join(ENGINE_NAMES)}.")
    return ENGINE_CLASSES[name](model_dir, voice=voice, speed=speed, num_threads=num_threads)
