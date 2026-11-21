"""Write one listening sample per installed English voice, for choosing by ear.

    .\\.venv\\Scripts\\python.exe tools\\tts_samples.py
    .\\.venv\\Scripts\\python.exe tools\\tts_samples.py --text "..." --voices kokoro:af_heart kokoro:am_puck --out DIR

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


def custom(text: str, voices: list[str], output: Path) -> int:
    """Selected voices, one WAV each, through Aqua's own splitter and speakable()."""
    import numpy as np
    from aqua.voice.engines import load_engine
    from aqua.voice.phonemes import _TOKEN
    from aqua.voice.text import SentenceSplitter, speakable

    splitter = SentenceSplitter()
    sentences = [spoken for part in splitter.feed(text) + splitter.flush() if (spoken := speakable(part))]
    lines = ["# Aqua fallback samples", "", f"Text: “{text}”", "",
             "Each file is the sentences Aqua would speak, synthesized one by one and joined back to back "
             "(as the persistent output stream plays them). Default thread counts.", "",
             "| Engine | Voice | File | Audio s | RTF |", "| --- | --- | --- | --- | --- |"]
    details = []
    for item in voices:
        engine, _, voice = item.partition(":")
        loaded = load_engine(engine, voice=voice or None)
        loaded.synthesize("Warm up.")
        started = time.perf_counter()
        audio = np.concatenate([loaded.synthesize(sentence) for sentence in sentences])
        seconds = time.perf_counter() - started
        path = output / f"{voice or engine}.wav"
        write_wav(path, audio, loaded.sample_rate)
        length = len(audio) / loaded.sample_rate
        lines.append(f"| {engine} | {voice} | [{path.name}]({path.name}) | {length:.2f} | {seconds / length:.3f} |")
        phonemizer = loaded.phonemizer
        if hasattr(phonemizer, "lexicon"):
            for sentence in sentences:
                def source(part):
                    return "lexicon" if part.lower() in phonemizer.lexicon else "espeak"

                sources = ", ".join(
                    f"{token} ({' + '.join(source(part) for part in token.split('.'))})" if "." in token and token[0].isalpha()
                    else f"{token} ({source(token)})"
                    for token in _TOKEN.findall(sentence) if token[0].isalnum())
                details += [f"- **{voice}** “{sentence}”", f"  - words: {sources}",
                            f"  - phonemes: `{phonemizer.phonemes(sentence)}`"]
        print(f"{engine}/{voice}: {length:.2f}s audio, RTF {seconds / length:.3f} -> {path}", flush=True)
    lines += ["", "Sentences spoken: " + " | ".join(f"“{s}”" for s in sentences), ""]
    lines += ["## Phonemes (lexicon first, espeak-ng fallback)", ""] + details + [""]
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {output / 'index.md'}")
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--text", help="custom text (requires --voices)")
    parser.add_argument("--voices", nargs="+", metavar="ENGINE:VOICE", help="for example kokoro:af_heart kokoro:am_puck")
    parser.add_argument("--out", type=Path, default=OUTPUT / "custom", help="output directory for --text samples")
    args = parser.parse_args()
    if args.text or args.voices:
        if not (args.text and args.voices):
            parser.error("--text and --voices go together")
        return custom(args.text, args.voices, args.out)
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
