"""Local text-to-speech engines built on the installed sherpa-onnx OfflineTts API.

Nothing here downloads anything. Each engine validates its local model
directory before importing sherpa-onnx, so a missing voice fails fast with a
clear message and no native code is loaded.
"""

from math import isfinite
from pathlib import Path
from threading import Lock
from typing import Protocol, runtime_checkable

SETUP_COMMAND = "aqua setup-voice --download"
VOICE_ROOT = Path(r"D:\Aqua\models")
ENGINE_DIRS = {
    "kokoro": VOICE_ROOT / "tts-kokoro",
    "piper": VOICE_ROOT / "tts-piper",
    "kitten": VOICE_ROOT / "tts-kitten",
}
ENGINE_NAMES = ("kokoro", "piper", "kitten", "none")
DEFAULT_ENGINE = "kokoro"

# Speaker ids as published for the sherpa-onnx Kokoro v1.0 and Kitten nano
# v0.1 voice packs. Numeric ids are always accepted as well. Part B confirms
# these against the files actually installed.
KOKORO_VOICES = {
    name: index for index, name in enumerate((
        "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
        "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
        "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
        "am_onyx", "am_puck", "am_santa",
        "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
        "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    ))
}
KITTEN_VOICES = {
    name: index for index, name in enumerate((
        "expr-voice-2-m", "expr-voice-2-f", "expr-voice-3-m", "expr-voice-3-f",
        "expr-voice-4-m", "expr-voice-4-f", "expr-voice-5-m", "expr-voice-5-f",
    ))
}
DEFAULT_VOICES = {"kokoro": "af_heart", "piper": "0", "kitten": "expr-voice-2-f"}


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


def _missing(engine: str, path: Path, missing) -> VoiceError:
    return VoiceError(
        f"The {engine} voice is not installed or is incomplete at {path}; missing: "
        f"{', '.join(missing)}. No download was attempted. Run '{SETUP_COMMAND}' to install it."
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


def _validate(engine: str, model_dir, files: tuple[str, ...], preferred: tuple[str, ...]) -> tuple[Path, Path]:
    path = Path(model_dir if model_dir is not None else ENGINE_DIRS[engine]).expanduser()
    try:
        path = path.resolve()
        missing = [name for name in files if not (path / name).is_file() or (path / name).stat().st_size == 0]
        if not (path / "espeak-ng-data").is_dir():
            missing.append("espeak-ng-data/")
        model = _onnx_model(engine, path, preferred) if path.is_dir() else None
    except OSError as exc:
        raise VoiceError(f"Could not inspect the {engine} voice files in {path}: {exc}") from exc
    if model is None:
        missing.insert(0, " or ".join(preferred) if preferred else "*.onnx")
    if missing:
        raise _missing(engine, path, missing)
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
        self.speaker_id = _speaker_id(DEFAULT_VOICES[self.engine] if voice is None else voice, self.voices, self.engine)
        self.model_dir, self.model_path = _validate(self.engine, model_dir, self.files, self.preferred)
        _configure_runtime()
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise VoiceError("Speech output needs sherpa-onnx; install Aqua's handsfree extra.") from exc
        try:
            model_config = sherpa_onnx.OfflineTtsModelConfig(
                **{self.engine_field: self._model_config(sherpa_onnx)},
                num_threads=threads, debug=False, provider="cpu",
            )
            config = sherpa_onnx.OfflineTtsConfig(model=model_config, max_num_sentences=1)
            if hasattr(config, "validate") and not config.validate():
                raise VoiceError(f"The {self.engine} voice configuration in {self.model_dir} is invalid.")
            self._tts = sherpa_onnx.OfflineTts(config)
            self.sample_rate = int(self._tts.sample_rate)
            speakers = int(self._tts.num_speakers)
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceError(f"Could not load the {self.engine} voice from {self.model_dir}: {exc}") from exc
        if speakers > 0 and self.speaker_id >= speakers:
            raise VoiceError(f"The {self.engine} voice has {speakers} speakers; id {self.speaker_id} is out of range.")
        self._lock = Lock()

    engine_field = ""

    def _model_config(self, sherpa_onnx):
        raise NotImplementedError

    def _path(self, name: str) -> str:
        return str(self.model_dir / name)

    def synthesize(self, text: str):
        import numpy as np

        if not isinstance(text, str) or not text.strip():
            return np.zeros(0, dtype=np.float32)
        with self._lock:  # One OfflineTts is not shared across concurrent calls.
            audio = self._tts.generate(text, sid=self.speaker_id, speed=self.speed)
        samples = np.asarray(audio.samples, dtype=np.float32).reshape(-1)
        if not np.isfinite(samples).all():
            raise VoiceError(f"The {self.engine} voice produced invalid audio.")
        return samples


class KokoroEngine(_SherpaEngine):
    engine, engine_field = "kokoro", "kokoro"
    files = ("voices.bin", "tokens.txt")
    preferred = ("model.onnx", "model.int8.onnx")
    voices = KOKORO_VOICES

    def _model_config(self, sherpa_onnx):
        lexicons = sorted(self.model_dir.glob("lexicon-*.txt"))
        return sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=str(self.model_path), voices=self._path("voices.bin"), tokens=self._path("tokens.txt"),
            lexicon=",".join(str(item) for item in lexicons), data_dir=self._path("espeak-ng-data"),
            length_scale=1.0,
        )


class PiperEngine(_SherpaEngine):
    """Piper voices exported for sherpa-onnx's VITS runtime."""

    engine, engine_field = "piper", "vits"
    files = ("tokens.txt",)
    voices: dict[str, int] = {}

    def _model_config(self, sherpa_onnx):
        return sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(self.model_path), tokens=self._path("tokens.txt"),
            data_dir=self._path("espeak-ng-data"), length_scale=1.0,
        )


class KittenEngine(_SherpaEngine):
    engine, engine_field = "kitten", "kitten"
    files = ("voices.bin", "tokens.txt")
    preferred = ("model.fp16.onnx", "model.onnx")
    voices = KITTEN_VOICES

    def _model_config(self, sherpa_onnx):
        return sherpa_onnx.OfflineTtsKittenModelConfig(
            model=str(self.model_path), voices=self._path("voices.bin"), tokens=self._path("tokens.txt"),
            data_dir=self._path("espeak-ng-data"), length_scale=1.0,
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


ENGINE_CLASSES = {"kokoro": KokoroEngine, "piper": PiperEngine, "kitten": KittenEngine}


def load_engine(name: str = DEFAULT_ENGINE, *, model_dir=None, voice=None, speed: float = 1.0,
                num_threads: int = 2) -> SpeechEngine:
    if name == "none":
        return NullEngine()
    if name not in ENGINE_CLASSES:
        raise VoiceError(f"Unknown speech engine {name!r}; choose one of: {', '.join(ENGINE_NAMES)}.")
    return ENGINE_CLASSES[name](model_dir, voice=voice, speed=speed, num_threads=num_threads)
