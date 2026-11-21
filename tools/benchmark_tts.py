"""Benchmark Aqua's local text-to-speech engines on this machine.

Each engine x thread-count configuration runs in a fresh worker process (so
model load time and peak RAM are clean) twice: with the Qwen chat model loaded
in Ollama but idle, and while Qwen is generating (realistic contention). The
synthesis path is Aqua's own engines.py. Audio is measured, never saved.

    .\\.venv\\Scripts\\python.exe tools\\benchmark_tts.py            # full run, writes benchmarks/tts-v1/
    .\\.venv\\Scripts\\python.exe tools\\benchmark_tts.py --quick    # one config, prints only

Ollama is reached only on 127.0.0.1:11434.
"""

import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUTPUT = ROOT / "benchmarks" / "tts-v1"
OLLAMA = "http://127.0.0.1:11434"
CHAT_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
# Same context as Aqua's chat requests, so Ollama keeps one model instance (and
# never tries the model's default huge context).
CONTEXT = 4096

# Typical Aqua replies: short confirmations, a question, and longer explanations.
SENTENCES = [
    "Done.",
    "Opening Notepad now.",
    "The launch attempt succeeded.",
    "I couldn't find that window.",
    "Would you like me to open Visual Studio Code?",
    "Calculator is open and ready.",
    "It's twenty past three in the afternoon.",
    "Okay, I've cancelled that request.",
    "Formula One is the highest class of international open-wheel single-seater racing, sanctioned by the FIA.",
    "I can open approved apps, describe the windows on your screen, and type exact text into an empty Notepad document.",
    "Your words are processed locally on this laptop, so nothing you say is sent to the cloud or saved to disk.",
    "The rest is on screen.",
]

# (label, engine, model directory, voice) — voices are fixed measurement voices, not defaults.
CONFIGS = [
    ("kokoro-fp32", "kokoro", ROOT / "models" / "tts-kokoro", "af_heart"),
    ("piper-lessac", "piper", ROOT / "models" / "tts-piper-lessac", "lessac"),
    ("piper-ryan", "piper", ROOT / "models" / "tts-piper-ryan", "ryan"),
    ("kitten-nano", "kitten", ROOT / "models" / "tts-kitten", "expr-voice-2-f"),
]
THREADS = (2, 4)


class _Memory(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def memory_mb() -> tuple[float, float]:
    """(current, peak) working set of this process in MB (Windows RSS)."""
    counters = _Memory()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p  # pseudo-handle -1; must not be truncated
    query = kernel32.K32GetProcessMemoryInfo
    query.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Memory), ctypes.c_ulong]
    query.restype = ctypes.c_int
    if not query(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
    return counters.WorkingSetSize / 2**20, counters.PeakWorkingSetSize / 2**20


def worker(engine: str, model_dir: str, voice: str, threads: int) -> dict:
    from aqua.voice.engines import load_engine

    baseline_mb, _ = memory_mb()
    started = time.perf_counter()
    loaded = load_engine(engine, model_dir=model_dir, voice=voice, num_threads=threads)
    load_seconds = time.perf_counter() - started
    loaded.synthesize("Warm up.")  # Not timed: the first call allocates buffers once per session.
    rows = []
    wall_started, cpu_synth_started = time.perf_counter(), time.process_time()
    for text in SENTENCES:
        began = time.perf_counter()
        samples = loaded.synthesize(text)
        seconds = time.perf_counter() - began
        audio = len(samples) / loaded.sample_rate
        rows.append({"text": text, "synthesis_seconds": seconds, "audio_seconds": audio,
                     "rtf": seconds / audio if audio else None})
    wall = time.perf_counter() - wall_started
    cpu = time.process_time() - cpu_synth_started
    _, peak_mb = memory_mb()
    # First audio of a reply = synthesis of its first sentence (Aqua plays whole sentences).
    cold = time.perf_counter()
    loaded.synthesize(SENTENCES[4])
    return {
        "sample_rate": loaded.sample_rate, "load_seconds": load_seconds,
        "baseline_mb": baseline_mb, "peak_rss_mb": peak_mb, "model_rss_mb": peak_mb - baseline_mb,
        "first_audio_seconds": rows[0]["synthesis_seconds"],
        "question_first_audio_seconds": time.perf_counter() - cold,
        "sentences": rows,
        "total_synthesis_seconds": sum(r["synthesis_seconds"] for r in rows),
        "total_audio_seconds": sum(r["audio_seconds"] for r in rows),
        "cpu_percent_of_one_core": 100 * cpu / wall if wall else None,
        "cpu_percent_of_machine": 100 * cpu / wall / (os.cpu_count() or 1) if wall else None,
    }


def ollama(path: str, payload: dict | None = None, *, timeout: float = 300):
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(OLLAMA + path, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def load_chat_model() -> str:
    version = json.loads(ollama("/api/version"))["version"]
    ollama("/api/generate", {"model": CHAT_MODEL, "prompt": "Hi", "stream": False, "keep_alive": "30m",
                             "options": {"num_predict": 1, "num_ctx": CONTEXT}})
    return version


class Generator(threading.Thread):
    """Keep Qwen generating continuously until stopped (streamed, long outputs)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.tokens = 0

    def run(self):
        prompt = "Write a long, detailed history of motor racing, chapter by chapter."
        while not self.stop.is_set():
            request = Request(OLLAMA + "/api/generate", data=json.dumps({
                "model": CHAT_MODEL, "prompt": prompt, "stream": True, "keep_alive": "30m",
                "options": {"num_predict": 2048, "num_ctx": CONTEXT, "temperature": 0.7}}).encode(),
                headers={"Content-Type": "application/json"})
            try:
                with urlopen(request, timeout=600) as response:
                    for line in response:
                        if self.stop.is_set():
                            break
                        if line.strip():
                            self.tokens += 1
            except OSError:
                time.sleep(.5)


def run_worker(label, engine, model_dir, voice, threads) -> dict:
    command = [sys.executable, "-B", __file__, "--worker", engine, str(model_dir), voice, str(threads)]
    finished = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if finished.returncode != 0:
        raise RuntimeError(f"{label} x{threads} failed: {finished.stderr.strip()[-800:]}")
    return json.loads(finished.stdout.strip().splitlines()[-1])


def hardware() -> dict:
    info = {"machine": platform.machine(), "platform": platform.platform(),
            "processor": platform.processor(), "logical_cpus": os.cpu_count(), "python": platform.python_version()}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            info["cpu_name"] = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except OSError:
        pass

    class Status(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    status = Status()
    status.dwLength = ctypes.sizeof(status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    info["ram_gb"] = round(status.ullTotalPhys / 2**30, 1)
    return info


def report(data: dict) -> str:
    lines = ["# Text-to-speech benchmark v1", "",
             f"Run {data['started_at']} on {data['hardware'].get('cpu_name', data['hardware']['processor'])}, "
             f"{data['hardware']['logical_cpus']} logical CPUs, {data['hardware']['ram_gb']} GB RAM, "
             f"{data['hardware']['platform']}.",
             f"Python {data['hardware']['python']}, sherpa-onnx {data['versions']['sherpa_onnx']}, "
             f"numpy {data['versions']['numpy']}, piper_phonemize {data['versions']['piper_phonemize']}, "
             f"Ollama {data['versions']['ollama']} with `{CHAT_MODEL}`"
             + "".join(f" ({m['size'] / 2**30:.1f} GiB loaded, {m['size_vram'] / 2**30:.1f} GiB in GPU memory)"
                       for m in data.get("ollama_loaded", []) if m.get("size")) + ".",
             f"While busy, Qwen streamed about {data.get('busy_chunks_per_second', 0):.1f} chunks/s (≈ tokens/s) "
             "alongside the voice." if data.get("busy_chunks_per_second") else "", "",
             "Engines run through Aqua's `aqua.voice.engines` on CPU (`provider=cpu`, `max_num_sentences=1`, "
             "speed 1.0, one sentence per `generate` call, as in the pipeline). Text is phonemized by "
             "`aqua.voice.phonemes` (espeak-ng via piper_phonemize; Kokoro lexicon first), and phonemization is "
             "included in the synthesis time. Each configuration ran in a fresh process after one untimed warm-up "
             "sentence. Measurement voices: Kokoro `af_heart`, Piper lessac/ryan, Kitten `expr-voice-2-f` "
             "(not a default choice).", "",
             "- **Load**: model construction time. **RAM**: peak working set of the process, and the part added "
             "by the engine over a bare Python baseline.",
             "- **First audio**: synthesis time of the reply's first sentence (\"Done.\"); Aqua plays a sentence "
             "once it is fully synthesised. **Question**: the same for a typical question sentence.",
             "- **RTF**: synthesis time / audio duration over all 12 sentences (lower is faster; < 1 is faster "
             "than real time). **Worst RTF**: slowest single sentence.",
             "- **CPU**: process CPU time during synthesis / wall time, as % of one core (and of the machine).",
             "- **Idle**: Qwen loaded in Ollama, not generating. **Busy**: Qwen generating continuously "
             "on the same machine (GPU offload as Ollama chooses).", "",
             "| Engine | Threads | Qwen | Load s | Peak RAM MB (engine) | First audio s | Question s | RTF | Worst RTF | CPU % core (machine) |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in data["results"]:
        r = row["result"]
        rtf = r["total_synthesis_seconds"] / r["total_audio_seconds"]
        worst = max(s["rtf"] for s in r["sentences"] if s["rtf"] is not None)
        lines.append(
            f"| {row['label']} | {row['threads']} | {row['contention']} | {r['load_seconds']:.2f} | "
            f"{r['peak_rss_mb']:.0f} ({r['model_rss_mb']:.0f}) | {r['first_audio_seconds']:.3f} | "
            f"{r['question_first_audio_seconds']:.3f} | {rtf:.3f} | {worst:.3f} | "
            f"{r['cpu_percent_of_one_core']:.0f} ({r['cpu_percent_of_machine']:.0f}) |")
    lines += ["", "Sentences:", ""] + [f"{i}. {text}" for i, text in enumerate(SENTENCES, 1)]
    lines += ["", "Raw per-sentence timings are in `results.json`. Audio was not saved.", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--worker", nargs=4, metavar=("ENGINE", "DIR", "VOICE", "THREADS"), help=argparse.SUPPRESS)
    parser.add_argument("--quick", action="store_true", help="one configuration, idle only, print results")
    args = parser.parse_args(argv)
    if args.worker:
        engine, model_dir, voice, threads = args.worker
        print(json.dumps(worker(engine, model_dir, voice, int(threads))))
        return 0

    import numpy
    import sherpa_onnx
    import importlib.metadata as metadata
    versions = {"sherpa_onnx": sherpa_onnx.__version__, "numpy": numpy.__version__,
                "piper_phonemize": metadata.version("piper_phonemize"), "ollama": load_chat_model()}
    loaded = [m for m in json.loads(ollama("/api/ps")).get("models", []) if m.get("name") == CHAT_MODEL]
    configs = CONFIGS[:1] if args.quick else CONFIGS
    threads = THREADS[:1] if args.quick else THREADS
    contention = ("idle",) if args.quick else ("idle", "busy")
    data = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "hardware": hardware(),
            "versions": versions, "chat_model": CHAT_MODEL, "sentences": SENTENCES, "results": [],
            "ollama_loaded": [{k: m.get(k) for k in ("name", "size", "size_vram")} for m in loaded]}
    for state in contention:
        generator = None
        if state == "busy":
            generator = Generator()
            generator.start()
            time.sleep(5)  # Let generation reach steady state.
            busy_started = time.perf_counter()
        try:
            for label, engine, model_dir, voice in configs:
                for count in threads:
                    print(f"{label} threads={count} qwen={state}...", flush=True)
                    result = run_worker(label, engine, model_dir, voice, count)
                    data["results"].append({"label": label, "engine": engine, "voice": voice,
                                            "threads": count, "contention": state, "result": result})
        finally:
            if generator is not None:
                generator.stop.set()
                data["busy_stream_chunks"] = generator.tokens
                data["busy_seconds"] = time.perf_counter() - busy_started
                data["busy_chunks_per_second"] = generator.tokens / data["busy_seconds"]
    if args.quick:
        print(report(data))
        return 0
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    (OUTPUT / "report.md").write_text(report(data), encoding="utf-8")
    print(f"Wrote {OUTPUT / 'results.json'} and report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
