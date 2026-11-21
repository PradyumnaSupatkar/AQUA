"""Write one listening sample per installed English voice, for choosing by ear.

    .\\.venv\\Scripts\\python.exe tools\\tts_samples.py

Writes D:\\Aqua\\.cache\\tts-samples\\<engine>\\<voice>.wav (16-bit mono, the
engine's native rate) and an index.md. Synthesis goes through Aqua's own
aqua.voice.engines with 2 threads. Nothing is played and nothing is uploaded.
"""

from pathlib import Path
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUTPUT = ROOT / ".cache" / "tts-samples"
TEXT = ("Okay, Notepad is open and ready for you. "
        "Would you like me to type the shopping list into it now?")


def voices():
    """(engine folder, engine, model dir, voice, gender, accent) for every English voice installed."""
    from aqua.voice.engines import KITTEN_VOICES, KOKORO_ENGLISH_VOICES, PIPER_VOICES, engine_dir

    for name, (gender, accent) in KOKORO_ENGLISH_VOICES.items():
        yield "kokoro-fp32", "kokoro", engine_dir("kokoro"), name, gender, accent
    for name, (gender, accent) in PIPER_VOICES.items():
        yield "piper", "piper", engine_dir("piper", name), name, gender, accent
    for name in KITTEN_VOICES:
        gender = "male" if name.endswith("-m") else "female"
        yield "kitten", "kitten", engine_dir("kitten"), name, gender, "American"


def write_wav(path: Path, samples, rate: int) -> None:
    import numpy as np

    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm)


def main() -> int:
    from aqua.voice.engines import load_engine

    rows, engines = [], {}
    for folder, engine, directory, voice, gender, accent in voices():
        # One load per model and lexicon: Piper per voice, Kokoro per accent (US/GB lexicon).
        key = (folder, voice if engine == "piper" else accent if engine.startswith("kokoro") else None)
        try:
            if key not in engines:
                engines[key] = load_engine(engine, model_dir=directory, voice=voice, num_threads=2)
            loaded = engines[key]
            loaded.speaker_id = loaded.resolve_voice(voice)  # same model and lexicon, other speaker
            loaded.synthesize("Warm up.")
            started = time.perf_counter()
            samples = loaded.synthesize(TEXT)
            seconds = time.perf_counter() - started
        except Exception as exc:  # Report and continue; one broken voice must not hide the rest.
            print(f"{folder}/{voice}: FAILED {exc}", flush=True)
            continue
        path = OUTPUT / folder / f"{voice}.wav"
        write_wav(path, samples, loaded.sample_rate)
        audio = len(samples) / loaded.sample_rate
        rows.append((folder, voice, gender, accent, path, path.stat().st_size, audio, seconds / audio))
        print(f"{folder}/{voice}: {audio:.2f}s audio, RTF {seconds / audio:.3f}", flush=True)
    lines = ["# Aqua voice samples", "",
             f"Every sample says: \u201c{TEXT}\u201d", "",
             "Generated with Aqua's engines (CPU, 2 threads, speed 1.0). RTF = synthesis time / audio length "
             "for this sample (lower is faster). Open a file to listen.", "",
             "| Engine | Voice | Gender | Accent | File | Size KB | Audio s | RTF |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for folder, voice, gender, accent, path, size, audio, rtf in rows:
        lines.append(f"| {folder} | {voice} | {gender} | {accent} | [{folder}/{path.name}]({folder}/{path.name}) | "
                     f"{size / 1024:.0f} | {audio:.2f} | {rtf:.3f} |")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} samples and {OUTPUT / 'index.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
