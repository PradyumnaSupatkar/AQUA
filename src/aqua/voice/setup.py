"""Pinned text-to-speech voice assets, verified locally and downloaded only on request.

Each voice comes from one archive in the official k2-fsa/sherpa-onnx
"tts-models" GitHub release, pinned by URL, size and SHA-256. Only the files
listed in that voice's manifest (size + SHA-256 per file, shipped with Aqua) are
extracted; members are read one by one, never with extractall, so archive
paths cannot escape the target directory. Extraction goes to a staging
directory next to the target and is moved into place only after every file
verifies. An existing directory that fails verification is never overwritten.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
from urllib.error import URLError
from urllib.request import Request, urlopen

from .engines import VOICE_ROOT, VoiceError

RELEASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models"
MANIFEST_DIR = Path(__file__).with_name("manifests")
RANGE_BYTES = 4 << 20
RANGE_CONNECTIONS = 12
RANGE_ATTEMPTS = 8


@dataclass(frozen=True, slots=True)
class VoiceAsset:
    key: str            # manifest name and default directory suffix
    engine: str         # setup-voice --engine value
    voice: str | None   # setup-voice --voice value for engines with one archive per voice
    archive: str
    size: int
    sha256: str
    root: str           # top-level folder inside the archive

    @property
    def url(self) -> str:
        return f"{RELEASE_URL}/{self.archive}"

    @property
    def directory(self) -> Path:
        return VOICE_ROOT / f"tts-{self.key}"


ASSETS = {asset.key: asset for asset in (
    VoiceAsset("kokoro", "kokoro", None, "kokoro-multi-lang-v1_0.tar.bz2", 349_886_476,
               "60703707bc05ccd48d60e1eb723f38b07d6e474795a0b6a588d7220400e0f7a4", "kokoro-multi-lang-v1_0"),
    VoiceAsset("kokoro-int8", "kokoro-int8", None, "kokoro-int8-multi-lang-v1_0.tar.bz2", 132_353_410,
               "72c347230107ef8a31098ec4d468a605244beef5f1ed00db97efa13518800653", "kokoro-int8-multi-lang-v1_0"),
    VoiceAsset("piper-lessac", "piper", "lessac", "vits-piper-en_US-lessac-medium.tar.bz2", 67_230_653,
               "9e3febfacf0abf4270172d2958bcec246032b7e88efc2720840cc80c93de334e", "vits-piper-en_US-lessac-medium"),
    VoiceAsset("piper-ryan", "piper", "ryan", "vits-piper-en_US-ryan-medium.tar.bz2", 67_213_100,
               "c546af78b6395b4e7c4ce1ed899438b64426a362f5d4ec5fecd090ded9ad7505", "vits-piper-en_US-ryan-medium"),
    VoiceAsset("kitten", "kitten", None, "kitten-nano-en-v0_2-fp16.tar.bz2", 26_586_708,
               "0345a8a2f4a710cb8f7912c9a731ded8b3e1e69b33a871efa95c2e64651518fe", "kitten-nano-en-v0_2-fp16"),
)}
SETUP_ENGINES = ("kokoro", "kokoro-int8", "piper", "kitten")


def manifest(asset: VoiceAsset) -> dict[str, tuple[int, str]]:
    try:
        data = json.loads((MANIFEST_DIR / f"{asset.key}.json").read_text(encoding="utf-8"))
        files = {name: (int(size), str(digest)) for name, (size, digest) in data["files"].items()}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise VoiceError(f"Aqua's pinned file list for the {asset.key} voice is missing or invalid.") from exc
    for name in files:
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise VoiceError(f"Aqua's pinned file list for {asset.key} contains an unsafe path.")
    return files


def select_assets(engine: str, voice: str | None = None) -> list[VoiceAsset]:
    if engine not in SETUP_ENGINES:
        raise VoiceError(f"Unknown voice engine {engine!r}; choose one of: {', '.join(SETUP_ENGINES)}.")
    candidates = [asset for asset in ASSETS.values() if asset.engine == engine]
    if voice is None or candidates[0].voice is None:
        return candidates
    chosen = [asset for asset in candidates if asset.voice == voice.casefold()]
    if not chosen:
        raise VoiceError(f"Unknown {engine} voice {voice!r}; choose one of: "
                         f"{', '.join(asset.voice for asset in candidates)}.")
    return chosen


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def problems(asset: VoiceAsset, model_dir: str | Path | None = None) -> list[str]:
    """Files that are missing or differ from the pinned manifest."""
    path = Path(model_dir if model_dir is not None else asset.directory)
    invalid = []
    try:
        for name, (size, digest) in manifest(asset).items():
            target = path / name
            if not target.is_file() or target.stat().st_size != size or _digest(target) != digest:
                invalid.append(name)
    except OSError as exc:
        raise VoiceError(f"Could not read the {asset.key} voice files at {path}: {exc}") from exc
    return invalid


def verify(asset: VoiceAsset, model_dir: str | Path | None = None) -> Path:
    path = Path(model_dir if model_dir is not None else asset.directory)
    invalid = problems(asset, path)
    if invalid:
        shown = ", ".join(invalid[:5]) + (f" and {len(invalid) - 5} more" if len(invalid) > 5 else "")
        raise VoiceError(
            f"The {asset.key} voice is missing or failed verification at {path}: {shown}. "
            f"No download was attempted. Run 'aqua setup-voice --engine {asset.engine}"
            f"{' --voice ' + asset.voice if asset.voice else ''} --download' to install it "
            f"(about {asset.size / 1e6:.0f} MB)."
        )
    return path


def _request(asset: VoiceAsset, first: int | None = None, last: int | None = None) -> Request:
    headers = {"User-Agent": "Aqua-voice-setup"}
    if first is not None:
        headers["Range"] = f"bytes={first}-{last}"
    return Request(asset.url, headers=headers)


def _download_single(asset: VoiceAsset, destination: Path) -> None:
    received = 0
    with urlopen(_request(asset), timeout=60) as response, destination.open("wb") as output:
        while block := response.read(min(1 << 20, asset.size + 1 - received)):
            received += len(block)
            if received > asset.size:
                raise VoiceError(f"The {asset.archive} download exceeded its pinned size.")
            output.write(block)
    if received != asset.size:
        raise VoiceError(f"The {asset.archive} download ended early.")


def _download_ranges(asset: VoiceAsset, destination: Path) -> bool:
    """Fetch byte ranges over several connections; False if ranges are unsupported.

    The release host limits each connection's speed, so parallel ranges make
    large voices practical. Each range retries on its own; the complete file
    is still checked against the pinned SHA-256 afterwards.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    from time import sleep

    spans = [(start, min(start + RANGE_BYTES, asset.size) - 1) for start in range(0, asset.size, RANGE_BYTES)]
    with destination.open("wb") as output:
        output.truncate(asset.size)
    lock = Lock()

    def fetch(span, *, probe=False):
        first, last = span
        for attempt in range(RANGE_ATTEMPTS):
            try:
                with urlopen(_request(asset, first, last), timeout=30) as response:
                    if getattr(response, "status", None) != 206:
                        if probe:
                            return False
                        raise VoiceError("The release host stopped honouring range requests.")
                    data = response.read(last - first + 2)
                if len(data) != last - first + 1:
                    raise OSError("short range response")
                with lock, destination.open("r+b") as output:
                    output.seek(first)
                    output.write(data)
                return True
            except (OSError, URLError):
                if attempt == RANGE_ATTEMPTS - 1:
                    raise
                sleep(min(10, 2 ** attempt))
        return True

    if not fetch(spans[0], probe=True):
        return False
    with ThreadPoolExecutor(max_workers=RANGE_CONNECTIONS) as pool:
        list(pool.map(fetch, spans[1:]))
    return True


def _download(asset: VoiceAsset, destination: Path) -> None:
    if not _download_ranges(asset, destination):
        _download_single(asset, destination)
    if destination.stat().st_size != asset.size or _digest(destination) != asset.sha256:
        raise VoiceError(f"The downloaded {asset.archive} failed size/SHA-256 verification.")


def _extract(asset: VoiceAsset, archive: Path, staging: Path) -> None:
    wanted = manifest(asset)
    found = set()
    with tarfile.open(archive, "r:bz2") as bundle:
        for member in bundle:
            parts = PurePosixPath(member.name).parts
            if len(parts) < 2 or parts[0] != asset.root:
                continue
            relative = "/".join(parts[1:])
            if relative not in wanted:
                continue  # Only pinned files are kept.
            size, digest = wanted[relative]
            if not member.isfile() or member.size != size:
                raise VoiceError(f"Archive member {relative} is not the pinned regular file.")
            target = staging.joinpath(*parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            hasher = hashlib.sha256()
            with source, target.open("wb") as output:
                while block := source.read(1 << 20):
                    output.write(block)
                    hasher.update(block)
            if hasher.hexdigest() != digest:
                raise VoiceError(f"Archive member {relative} failed SHA-256 verification.")
            found.add(relative)
    missing = sorted(set(wanted) - found)
    if missing:
        raise VoiceError(f"The {asset.archive} archive lacks pinned files: {', '.join(missing[:5])}.")


def install(asset: VoiceAsset, model_dir: str | Path | None = None, *, archive_path: str | Path | None = None) -> Path:
    """Download (or take a local copy of the pinned archive), verify and extract.

    Complete installs are reused offline. ``archive_path`` must be the exact
    pinned archive: its size and SHA-256 are checked before anything is read.
    """
    path = Path(model_dir if model_dir is not None else asset.directory)
    if path.exists():
        if not problems(asset, path):
            return path
        raise VoiceError(
            f"The existing {asset.key} voice directory {path} failed verification. It was not "
            "overwritten; inspect or remove it, or choose another directory."
        )
    work = path.parent / f".{path.name}.setup"
    archive = work / (asset.archive + ".part")
    staging = work / "files"
    try:
        if staging.exists():
            shutil.rmtree(staging)  # Only Aqua's own staging area for this asset.
        staging.mkdir(parents=True)
        if archive_path is not None:
            archive = Path(archive_path)
            if not archive.is_file() or archive.stat().st_size != asset.size or _digest(archive) != asset.sha256:
                raise VoiceError(f"{archive} is not the pinned {asset.archive} (size/SHA-256 mismatch).")
        else:
            _download(asset, archive)
        _extract(asset, archive, staging)
        if problems(asset, staging):
            raise VoiceError(f"The extracted {asset.key} files failed verification.")
        staging.replace(path)
        shutil.rmtree(work, ignore_errors=True)
    except (OSError, URLError, tarfile.TarError) as exc:
        raise VoiceError(
            f"Voice setup for {asset.key} failed: {exc}. Setup files were kept in {work}; "
            "no other models were modified."
        ) from exc
    return verify(asset, path)


def setup_voice(engine: str, voice: str | None = None, *, download: bool = False,
                model_dir: str | Path | None = None, archive: str | Path | None = None) -> list[Path]:
    """Verify (or, with ``download`` or ``archive``, install) the selected voice assets."""
    assets = select_assets(engine, voice)
    if (model_dir is not None or archive is not None) and len(assets) != 1:
        raise VoiceError(f"--model-dir and --from-archive need a single voice; add --voice for {engine}.")
    if archive is not None:
        return [install(assets[0], model_dir, archive_path=archive)]
    return [install(asset, model_dir) if download else verify(asset, model_dir) for asset in assets]
