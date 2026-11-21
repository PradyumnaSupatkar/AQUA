"""Maintainer tool: build a voice manifest from a verified official archive.

    .\\.venv\\Scripts\\python.exe tools\\voice_manifest.py KEY ARCHIVE

The archive must match the size and SHA-256 pinned in aqua.voice.setup.ASSETS.
Writes src/aqua/voice/manifests/KEY.json listing every kept file with its size
and SHA-256. Only files Aqua needs for English (plus licence/model cards) are kept.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# espeak-ng-data is not kept: sherpa-onnx 2.x ignores it and piper_phonemize bundles its own.
KEEP = {
    "kokoro": r"model\.onnx|voices\.bin|tokens\.txt|lexicon-(us|gb)-en\.txt|LICENSE|README\.md",
    "kokoro-int8": r"model\.int8\.onnx|voices\.bin|tokens\.txt|lexicon-(us|gb)-en\.txt|LICENSE|README\.md",
    "piper-lessac": r"en_US-lessac-medium\.onnx(\.json)?|tokens\.txt|MODEL_CARD",
    "piper-ryan": r"en_US-ryan-medium\.onnx(\.json)?|tokens\.txt|MODEL_CARD",
    "kitten": r"model\.fp16\.onnx|voices\.bin|tokens\.txt|LICENSE|README\.md",
}


def main(key: str, archive: str) -> int:
    from aqua.voice.setup import ASSETS, MANIFEST_DIR, _digest

    asset = ASSETS[key]
    path = Path(archive)
    if path.stat().st_size != asset.size or _digest(path) != asset.sha256:
        print(f"{path} is not the pinned {asset.archive}", file=sys.stderr)
        return 1
    pattern = re.compile(KEEP[key])
    files, skipped = {}, []
    with tarfile.open(path, "r:bz2") as bundle:
        for member in bundle:
            parts = PurePosixPath(member.name).parts
            if not member.isfile() or len(parts) < 2 or parts[0] != asset.root:
                continue
            relative = "/".join(parts[1:])
            if not pattern.fullmatch(relative):
                skipped.append(relative)
                continue
            with bundle.extractfile(member) as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            files[relative] = [member.size, digest]
    MANIFEST_DIR.mkdir(exist_ok=True)
    data = {"archive": asset.archive, "archive_sha256": asset.sha256, "files": dict(sorted(files.items()))}
    (MANIFEST_DIR / f"{key}.json").write_text(json.dumps(data, indent=0) + "\n", encoding="utf-8")
    total = sum(size for size, _ in files.values())
    print(f"{key}: kept {len(files)} files ({total / 1e6:.1f} MB); skipped: {', '.join(skipped) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))
