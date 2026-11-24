"""Compare speech recognisers on the recordings from tools/record_asr_set.py.

    .venv\\Scripts\\python.exe tools\\asr_models.py          download + verify the pinned models once
    .venv\\Scripts\\python.exe tools\\benchmark_asr.py       run every model, write benchmarks/asr-v1/

Each model runs in its own process (clean load time and RAM), with 4 CPU threads like Aqua's
Whisper, and one warm-up utterance that is not timed. Results (no audio) go to
benchmarks/asr-v1/results.json and report.md. Aqua's default recogniser is not changed.
"""

import argparse
import json
import re
import subprocess
import sys
import time
import wave
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

SET = ROOT / ".cache" / "asr-set"
OUT = ROOT / "benchmarks" / "asr-v1"
THREADS = 4
# Biasing list for the hotword runs: the words Whisper got wrong live, plus app names.
HOTWORDS = ["write", "pause", "Notepad", "VS Code", "Visual Studio Code", "Spotify", "Calculator", "Chrome", "Brave",
            "YouTube"]

MODELS = {
    "whisper-small": {"kind": "whisper", "dir": r"D:\Aqua\models\faster-whisper-small", "language": None,
                      "label": "Whisper Small (current: auto language, no hints)", "licence": "MIT"},
    "whisper-small-lang-en": {"kind": "whisper", "dir": r"D:\Aqua\models\faster-whisper-small", "language": "en",
                              "label": "Whisper Small, language=en", "licence": "MIT"},
    "whisper-small-hints": {"kind": "whisper", "dir": r"D:\Aqua\models\faster-whisper-small", "language": "en",
                            "hotwords": True, "label": "Whisper Small, language=en + hotwords", "licence": "MIT"},
    "whisper-small.en": {"kind": "whisper", "dir": "faster-whisper-small.en", "language": None,
                         "label": "Whisper small.en", "licence": "MIT"},
    "parakeet-v3": {"kind": "transducer", "dir": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
                    "label": "Parakeet TDT 0.6B v3 int8", "licence": "CC-BY-4.0"},
    "parakeet-v3-hotwords": {"kind": "transducer", "dir": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
                             "hotwords": True, "label": "Parakeet TDT 0.6B v3 int8 + hotwords", "licence": "CC-BY-4.0"},
    "parakeet-v2": {"kind": "transducer", "dir": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
                    "label": "Parakeet TDT 0.6B v2 int8 (English)", "licence": "CC-BY-4.0"},
    "parakeet-v2-hotwords": {"kind": "transducer", "dir": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
                             "hotwords": True, "label": "Parakeet TDT 0.6B v2 int8 + hotwords", "licence": "CC-BY-4.0"},
    "moonshine-v2-base": {"kind": "moonshine2", "dir": "sherpa-onnx-moonshine-base-en-quantized-2026-02-27",
                          "label": "Moonshine v2 base (en, quantized)", "licence": "MIT (English models)"},
    "moonshine-v2-tiny": {"kind": "moonshine2", "dir": "sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27",
                          "label": "Moonshine v2 tiny (en, quantized)", "licence": "MIT (English models)"},
}


# -- scoring --------------------------------------------------------------------------------------

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
        "seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_ORDINALS = {"1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth", "5th": "fifth"}
_JOIN = {"note pad": "notepad", "you tube": "youtube", "lo fi": "lofi", "v s code": "vs code", "v s": "vs",
         "minimize": "minimise", "minimized": "minimised", "ok": "okay", "hiphop": "hip hop", "e mail": "email"}


def say(number: int) -> str:
    """How people say a number in a command: 30 -> thirty, 1080 -> ten eighty, 250 -> two hundred fifty."""
    if number < 20:
        return _ONES[number]
    if number < 100:
        return _TENS[number // 10] + ("" if number % 10 == 0 else " " + _ONES[number % 10])
    if number < 1000:
        rest = number % 100
        return _ONES[number // 100] + " hundred" + ("" if rest == 0 else " " + say(rest))
    if number < 10000 and number % 1000:
        high, low = divmod(number, 100)
        return say(high) + " " + ("hundred" if low == 0 else ("oh " + _ONES[low] if low < 10 else say(low)))
    return say(number // 1000) + " thousand" + ("" if number % 1000 == 0 else " " + say(number % 1000))


def normalize(text: str) -> list[str]:
    text = text.casefold().replace("’", "'").replace("'", "")
    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: f"{say(int(m.group(1)))} "
                  + ("oclock" if m.group(2) == "00" else say(int(m.group(2)))), text)
    for short, word in _ORDINALS.items():
        text = re.sub(rf"\b{short}\b", word, text)
    text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
    text = re.sub(r"\d+", lambda m: say(int(m.group())) if len(m.group()) < 6 else m.group(), text)
    text = " ".join(re.sub(r"[^\w\s]", " ", text).split())
    for spoken, canonical in _JOIN.items():
        text = re.sub(rf"\b{spoken}\b", canonical, text)
    return text.split()


def edits(ref: list[str], hyp: list[str]) -> int:
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1]


def score(prompts: list[dict], texts: dict) -> dict:
    words = errors = exact = 0
    write_cases = [p for p in prompts if p["text"].casefold().startswith("write ")]
    pause_cases = [p for p in prompts if "pause" in p["text"].casefold().split()]
    write_ok = pause_ok = 0
    for prompt in prompts:
        ref, hyp = normalize(prompt["text"]), normalize(texts.get(prompt["id"], ""))
        words += len(ref)
        errors += edits(ref, hyp)
        exact += ref == hyp
        if prompt in write_cases and hyp[:1] == ["write"]:
            write_ok += 1
        if prompt in pause_cases and "pause" in hyp:
            pause_ok += 1
    return {"wer": errors / words, "exact": exact / len(prompts), "exact_count": exact, "utterances": len(prompts),
            "write_ok": write_ok, "write_cases": len(write_cases), "pause_ok": pause_ok, "pause_cases": len(pause_cases)}


# -- one model, in its own process ----------------------------------------------------------------

def _memory() -> tuple[float, float]:
    """(working set MB, peak working set MB) of this process."""
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    kernel32 = ctypes.WinDLL("kernel32")
    psapi = ctypes.WinDLL("psapi")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return counters.WorkingSetSize / 2**20, counters.PeakWorkingSetSize / 2**20


PAD_SECONDS = .5  # Silence added before and after each clip, for every model alike.


def read_wav(path: Path):
    """(16-bit PCM bytes, float32 samples), with PAD_SECONDS of silence on both sides.

    The recordings start the moment Enter is pressed (no pre-roll). Hands-free capture keeps a
    0.5 s pre-roll and 0.8 s of trailing silence, so live audio always has this margin; without it
    the Parakeet transducers returned nothing for several clips that start with speech."""
    import numpy as np
    with wave.open(str(path)) as handle:
        assert handle.getframerate() == 16000 and handle.getnchannels() == 1 and handle.getsampwidth() == 2
        pcm = handle.readframes(handle.getnframes())
    pad = b"\0\0" * round(PAD_SECONDS * 16000)
    pcm = pad + pcm + pad
    return pcm, np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0


def _model_dir(spec: dict) -> Path:
    path = Path(spec["dir"])
    return path if path.is_absolute() else ROOT / ".cache" / "asr-models" / path


def _hotword_files(model_dir: Path) -> tuple[str, str]:
    """(hotwords file, bpe.vocab) for sherpa-onnx contextual biasing. The sherpa-onnx Parakeet
    exports ship tokens.txt but no bpe.vocab; NeMo's tokenizer is a SentencePiece BPE whose ids
    follow merge priority, so each piece is scored by its id (earlier = higher)."""
    pieces = [line.rsplit(" ", 1)[0] for line in (model_dir / "tokens.txt").read_text(encoding="utf-8").splitlines()
              if line.strip()]
    vocab = model_dir.parent / f"bpe-{model_dir.name}.vocab"
    vocab.write_text("".join(f"{piece}\t{-index}\n" for index, piece in enumerate(pieces)), encoding="utf-8")
    hotwords = model_dir.parent / f"hotwords-{model_dir.name}.txt"
    hotwords.write_text("\n".join(HOTWORDS) + "\n", encoding="utf-8")
    return str(hotwords), str(vocab)


def load(spec: dict):
    """A function audio -> text for this model."""
    kind, model_dir = spec["kind"], _model_dir(spec)
    if kind == "whisper":
        from aqua.speech.recognizer import OfflineRecognizer
        recognizer = OfflineRecognizer(model_dir)
        hotwords = ", ".join(HOTWORDS) if spec.get("hotwords") else None
        return lambda pcm, _audio: recognizer.transcribe(pcm, 16000, language=spec.get("language"),
                                                         hotwords=hotwords).text
    import sherpa_onnx
    if kind == "transducer":
        find = lambda stem: str(next(model_dir.glob(f"{stem}*.onnx")))  # noqa: E731
        options = {"decoding_method": "greedy_search"}
        if spec.get("hotwords"):
            hotwords, vocab = _hotword_files(model_dir)
            options = {"decoding_method": "modified_beam_search", "max_active_paths": 4, "hotwords_file": hotwords,
                       "hotwords_score": 2.0, "modeling_unit": "bpe", "bpe_vocab": vocab}
        recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=find("encoder"), decoder=find("decoder"), joiner=find("joiner"),
            tokens=str(model_dir / "tokens.txt"), num_threads=THREADS, model_type="nemo_transducer", **options)
    elif kind == "moonshine2":
        find = lambda stem: str(next(p for p in model_dir.iterdir() if p.name.startswith(stem)  # noqa: E731
                                     and p.suffix in (".onnx", ".ort")))
        recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine_v2(
            encoder=find("encoder"), decoder=find("decoder"), tokens=str(model_dir / "tokens.txt"),
            num_threads=THREADS)
    else:
        raise ValueError(kind)

    def transcribe(_pcm, audio):
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, audio)
        recognizer.decode_stream(stream)
        return stream.result.text.strip()
    return transcribe


def worker(model_id: str) -> dict:
    spec = MODELS[model_id]
    prompts = json.loads((SET / "prompts.json").read_text(encoding="utf-8"))["prompts"]
    audio = {p["id"]: read_wav(SET / p["file"]) for p in prompts}
    base_memory, _ = _memory()
    started = time.perf_counter()
    transcribe = load(spec)
    load_seconds = time.perf_counter() - started
    loaded_memory, _ = _memory()
    transcribe(*audio[prompts[0]["id"]])  # Warm-up, not timed.
    texts, seconds = {}, {}
    for prompt in prompts:
        started = time.perf_counter()
        texts[prompt["id"]] = transcribe(*audio[prompt["id"]])
        seconds[prompt["id"]] = round(time.perf_counter() - started, 3)
    _, peak = _memory()
    return {"model": model_id, "load_seconds": round(load_seconds, 2),
            "model_ram_mb": round(loaded_memory - base_memory), "peak_ram_mb": round(peak), "texts": texts,
            "seconds": seconds}


# -- all models -----------------------------------------------------------------------------------

def run(model_ids: list[str]) -> list[dict]:
    results = []
    for model_id in model_ids:
        print(f"... {model_id}", flush=True)
        if not _model_dir(MODELS[model_id]).is_dir():
            results.append({"model": model_id, "skipped": "model not downloaded (see tools/asr_models.py)"})
            continue
        done = subprocess.run([sys.executable, "-B", __file__, "--worker", model_id], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=3600)
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        if done.returncode or not lines:
            print(f"    failed: {(done.stderr or done.stdout)[-600:]}", flush=True)
            results.append({"model": model_id, "error": (done.stderr or done.stdout)[-600:]})
            continue
        results.append(json.loads(lines[-1]))
    return results


def report(results: list[dict], prompts: list[dict], meta: dict) -> str:
    rows = []
    for result in results:
        spec = MODELS[result["model"]]
        if "skipped" in result:
            rows.append(f"| {spec['label']} | skipped: {result['skipped']} | | | | | | | | {spec['licence']} |")
            continue
        if "error" in result:
            rows.append(f"| {spec['label']} | failed: {result['error'][:80]} | | | | | | | | |")
            continue
        s = score(prompts, result["texts"])
        times = sorted(result["seconds"].values())
        result["score"] = s
        result["median_seconds"] = round(median(times), 3)
        result["p90_seconds"] = round(times[int(.9 * (len(times) - 1))], 3)
        rows.append(f"| {spec['label']} | {s['wer']:.1%} | {s['exact_count']}/{s['utterances']} | "
                    f"{s['write_ok']}/{s['write_cases']} | {s['pause_ok']}/{s['pause_cases']} | "
                    f"{result['median_seconds']:.2f} | {result['p90_seconds']:.2f} | {result['load_seconds']:.1f} | "
                    f"{result['model_ram_mb']} / {result['peak_ram_mb']} | {spec['licence']} |")
    lines = [
        "# asr-v1: speech recognisers on Aqua commands", "",
        f"{len(prompts)} utterances recorded by the user ({meta['seconds']:.0f} s of audio, {meta['microphone']}), "
        f"16 kHz. CPU only, {THREADS} threads, one untimed warm-up. {meta['finished']}.", "",
        "WER and exact match use the same normalisation for reference and transcript (case, punctuation, "
        "numbers as spoken: 1080p = ten eighty p, 2:30 = two thirty). write = the four 'write ...' prompts start "
        "with 'write' (not 'right'); pause = 'pause' heard in the three pause prompts.", "",
        "| Model | WER | Exact | write | pause | Median s | p90 s | Load s | RAM MB (model / peak) | Licence |",
        "|---|---|---|---|---|---|---|---|---|---|", *rows, "",
        f"Hotwords (contextual biasing): {', '.join(HOTWORDS)}.", "", "## Transcripts that differ from the prompt", ""]
    for prompt in prompts:
        ref = normalize(prompt["text"])
        wrong = [(MODELS[r["model"]]["label"], r["texts"].get(prompt["id"], "")) for r in results
                 if "texts" in r and normalize(r["texts"].get(prompt["id"], "")) != ref]
        if wrong:
            lines.append(f"- \"{prompt['text']}\"")
            lines += [f"  - {label}: \"{text}\"" for label, text in wrong]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--worker", choices=list(MODELS), help=argparse.SUPPRESS)
    parser.add_argument("--models", nargs="*", choices=list(MODELS), help="only these models")
    args = parser.parse_args(argv)
    if args.worker:
        print(json.dumps(worker(args.worker), ensure_ascii=False), flush=True)
        return 0
    index = json.loads((SET / "prompts.json").read_text(encoding="utf-8"))
    prompts = index["prompts"]
    results = run(args.models or list(MODELS))
    meta = {"seconds": sum(p["seconds"] for p in prompts), "microphone": index.get("microphone"),
            "finished": time.strftime("Run %Y-%m-%d %H:%M")}
    text = report(results, prompts, meta)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.md").write_text(text, encoding="utf-8")
    (OUT / "results.json").write_text(json.dumps({"meta": meta, "hotwords": HOTWORDS, "prompts": [
        {"id": p["id"], "text": p["text"], "seconds": p["seconds"]} for p in prompts], "results": results},
        indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
