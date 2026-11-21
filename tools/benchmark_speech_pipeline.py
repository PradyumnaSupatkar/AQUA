"""End-to-end speech pipeline under real contention: Qwen streaming into Kokoro.

No microphone and no sound. Three fixed prompts go to Ollama's
qwen3:4b-instruct-2507-q4_K_M through Aqua's own ConversationController (real
system prompt and tools); the streamed reply feeds SpeechOutput with real
Kokoro af_heart, played by a timing-only player that paces audio in real time
without a device. Grid: Kokoro threads x Ollama num_thread.

    .\\.venv\\Scripts\\python.exe tools\\benchmark_speech_pipeline.py            # full grid -> benchmarks/speech-pipeline-v1/
    .\\.venv\\Scripts\\python.exe tools\\benchmark_speech_pipeline.py --quick    # one cell, printed only
    .\\.venv\\Scripts\\python.exe tools\\benchmark_speech_pipeline.py --kokoro-threads 4 --llm-threads auto \\
        --early-caps 6,8 --output benchmarks/speech-pipeline-v2/caps-6-8   # one cell, chosen chunk caps

Ollama is reached only on 127.0.0.1:11434; nothing is pulled.
"""

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import platform
import statistics
import sys
from threading import Event
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
OUTPUT = ROOT / "benchmarks" / "speech-pipeline-v1"
MODEL = "qwen3:4b-instruct-2507-q4_K_M"
VOICE = "af_heart"
KOKORO_THREADS = (2, 3, 4)
OLLAMA_THREADS = (None, 6, 4)  # None = Ollama's default
REPEATS = 2
PROMPTS = {
    "explanation": "Explain in three sentences how a turbocharger makes a car engine more powerful.",
    "short": "What is the capital of Australia?",
    "list": "Give me five quick tips for staying focused while studying.",
}


class TimingPlayer:
    """Plays nothing: waits for each clip's real duration, honouring stop/cancel."""

    tail_seconds = 0.0

    def __init__(self):
        self._stop = Event()

    def play(self, samples, sample_rate, *, cancelled=None):
        self._stop.clear()
        deadline = perf_counter() + len(samples) / sample_rate
        while (remaining := deadline - perf_counter()) > 0:
            if self._stop.wait(min(.01, remaining)) or (cancelled is not None and cancelled()):
                return

    def stop(self):
        self._stop.set()


def threaded_client(num_thread):
    from aqua.brain.ollama import OllamaClient

    class Client(OllamaClient):
        """Adds Ollama's num_thread option to every chat request (benchmark only)."""

        def _payload(self, *args, **kwargs):
            payload = super()._payload(*args, **kwargs)
            if num_thread is not None:
                payload["options"]["num_thread"] = num_thread
            return payload

    return Client(timeout=300, keep_alive="30m")


def run_once(client, engine, prompt, settings):
    from aqua.brain.controller import ConversationController
    from aqua.brain.conversation import Conversation
    from aqua.chat import _generation_rate
    from aqua.voice.output import SpeechOutput

    speaker = SpeechOutput(engine, TimingPlayer(), warn=lambda message: print(message, file=sys.stderr))
    first_text = None
    started = perf_counter()

    def on_text(content):
        nonlocal first_text
        if first_text is None and content.strip():
            first_text = perf_counter() - started
        speaker.feed(content)

    controller = ConversationController(client, Conversation(), model=MODEL, settings=settings, clock=perf_counter)
    with redirect_stdout(io.StringIO()):
        result = controller.respond(prompt, on_text=on_text)
    reply_complete = perf_counter() - started
    if result.completion is not None:
        speaker.note_generation_rate(_generation_rate(result.completion))
    speaker.finish_turn()
    speaker.wait(180)
    speech_end = perf_counter() - started
    timings = speaker.timings
    speaker.close()
    first_audio = (first_text + timings.first_audio_seconds
                   if first_text is not None and timings.first_audio_seconds is not None else None)
    return {
        "ok": result.ok, "reply_chars": len(result.text), "reply": result.text,
        "first_token_seconds": first_text, "first_audio_seconds": first_audio,
        "longest_gap_seconds": timings.longest_gap_seconds, "total_gap_seconds": timings.total_gap_seconds,
        "chunks": timings.sentences_spoken, "audio_seconds": timings.audio_seconds,
        "synthesis_seconds": timings.synthesis_seconds, "llm_tokens_per_second": timings.llm_tokens_per_second,
        "reply_complete_seconds": reply_complete, "speech_end_seconds": speech_end,
    }


def summarize(runs):
    def mean(key):
        values = [run[key] for run in runs if run[key] is not None]
        return statistics.fmean(values) if values else None
    return {
        "first_audio_seconds": mean("first_audio_seconds"),
        "longest_gap_seconds": max(run["longest_gap_seconds"] for run in runs),
        "mean_longest_gap_seconds": mean("longest_gap_seconds"),
        "total_gap_seconds": mean("total_gap_seconds"),
        "llm_tokens_per_second": mean("llm_tokens_per_second"),
        "reply_complete_seconds": mean("reply_complete_seconds"),
        "speech_end_seconds": mean("speech_end_seconds"),
    }


def label(threads):
    return "default" if threads is None else str(threads)


def recommend(cells):
    """Smallest worst gap; ties (within 0.15 s) broken by mean gaps, then first audio."""
    best_gap = min(cell["summary"]["longest_gap_seconds"] for cell in cells)
    close = [cell for cell in cells if cell["summary"]["longest_gap_seconds"] <= best_gap + .15]
    return min(close, key=lambda cell: (round(cell["summary"]["total_gap_seconds"], 1),
                                        cell["summary"]["first_audio_seconds"] or 99))


def report(data):
    lines = [f"# Speech pipeline benchmark {data['name']} (Qwen streaming into Kokoro)", "",
             f"Run {data['started_at']} on {data['hardware']}. Ollama {data['ollama_version']} with `{MODEL}` "
             f"(temperature {data['temperature']}, seed {data['seed']}, context 4096, keep-alive 30m); Kokoro v1.0 fp32 "
             f"`{VOICE}`, sherpa-onnx {data['sherpa_onnx']}. Chunk word caps: "
             f"{', '.join(map(str, data['early_caps']))}, then {data['max_words']}.", "",
             "Each prompt goes through Aqua's ConversationController (real system prompt and tools); the streamed "
             "reply feeds SpeechOutput (sentence chunking, spoken-length limit) with real Kokoro synthesis. A "
             "timing-only player waits for each chunk's real duration without a sound device. "
             f"{REPEATS} runs per prompt and cell, {len(PROMPTS)} prompts; the Ollama model was warmed up once per "
             "num_thread setting before measuring.", "",
             "- **First audio**: request sent -> first audio would start (mean).",
             "- **Worst gap**: the longest time the player sat idle waiting for the next chunk, over all runs in the "
             "cell; **mean worst gap** averages each run's longest gap; **total gaps**: mean per reply.",
             "- **Qwen tok/s**: Ollama's eval rate while speech ran. **Reply done**: last token received. "
             "**Speech end**: last audio finished (means).", "",
             "| Kokoro threads | Ollama num_thread | First audio s | Worst gap s | Mean worst gap s | Total gaps s | Qwen tok/s | Reply done s | Speech end s |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for cell in data["cells"]:
        s = cell["summary"]
        lines.append(f"| {cell['kokoro_threads']} | {label(cell['ollama_threads'])} | {s['first_audio_seconds']:.2f} | "
                     f"{s['longest_gap_seconds']:.2f} | {s['mean_longest_gap_seconds']:.2f} | {s['total_gap_seconds']:.2f} | "
                     f"{s['llm_tokens_per_second']:.1f} | {s['reply_complete_seconds']:.2f} | {s['speech_end_seconds']:.2f} |")
    best = data["recommended"]
    lines += ["", f"**Recommended:** Kokoro {best['kokoro_threads']} threads with Ollama num_thread "
              f"{label(best['ollama_threads'])} (smallest worst gap; ties within 0.15 s broken by total gaps, then "
              "first audio).", "", "Prompts:", ""]
    lines += [f"- {name}: “{text}”" for name, text in PROMPTS.items()]
    lines += ["", "Per-run results, including reply texts, are in `results.json`.", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true", help="one cell (Kokoro 4, Ollama default), one run per prompt")
    parser.add_argument("--kokoro-threads", type=int, nargs="+", help=f"Kokoro threads to test; default: {KOKORO_THREADS}")
    parser.add_argument("--llm-threads", nargs="+", metavar="N|auto|default",
                        help="Ollama num_thread values; auto = Aqua's speaking default, default = Ollama's own")
    parser.add_argument("--early-caps", help="word caps of the first chunks, e.g. 6,10 (default: Aqua's)")
    parser.add_argument("--output", type=Path, default=OUTPUT, help=f"results folder; default: {OUTPUT}")
    args = parser.parse_args(argv)

    import sherpa_onnx
    from aqua.brain.ollama import GenerationSettings
    from aqua.voice.engines import load_engine
    from benchmark_tts import hardware

    from aqua.cli import SPEAKING_LLM_THREADS
    from aqua.voice import text

    if args.early_caps:
        text.CHUNK_EARLY_MAX_WORDS = tuple(int(cap) for cap in args.early_caps.split(","))
    settings = GenerationSettings(seed=42)
    kokoro_threads = tuple(args.kokoro_threads or ((4,) if args.quick else KOKORO_THREADS))
    named = {"auto": SPEAKING_LLM_THREADS, "default": None}
    ollama_threads = (tuple(named[v] if v in named else int(v) for v in args.llm_threads) if args.llm_threads
                      else (None,) if args.quick else OLLAMA_THREADS)
    repeats = 1 if args.quick else REPEATS
    engines = {threads: load_engine("kokoro", voice=VOICE, num_threads=threads) for threads in kokoro_threads}
    for engine in engines.values():
        engine.synthesize("Warm up.")
    info = hardware()
    data = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "hardware": f"{info.get('cpu_name', info['processor'])}, {info['logical_cpus']} logical CPUs, "
                        f"{info['ram_gb']} GB RAM, {info['platform']}",
            "sherpa_onnx": sherpa_onnx.__version__, "temperature": settings.temperature, "seed": settings.seed,
            "model": MODEL, "voice": VOICE, "name": args.output.name,
            "early_caps": list(text.CHUNK_EARLY_MAX_WORDS), "max_words": text.CHUNK_MAX_WORDS, "cells": []}
    for num_thread in ollama_threads:
        client = threaded_client(num_thread)
        data["ollama_version"] = client.check_model(MODEL).version
        client.validate_thinking(MODEL, settings.thinking)
        run_once(client, engines[kokoro_threads[0]], "Say hello.", settings)  # Warm-up: load with this num_thread.
        for threads in kokoro_threads:
            runs = []
            for name, prompt in PROMPTS.items():
                for repeat in range(repeats):
                    print(f"kokoro={threads} ollama={label(num_thread)} {name} #{repeat + 1}...", flush=True)
                    run = run_once(client, engines[threads], prompt, settings)
                    run.update(prompt=name, repeat=repeat + 1)
                    runs.append(run)
                    print(f"   first audio {run['first_audio_seconds']:.2f}s, longest gap {run['longest_gap_seconds']:.2f}s, "
                          f"{run['llm_tokens_per_second'] or 0:.1f} tok/s", flush=True)
            data["cells"].append({"kokoro_threads": threads, "ollama_threads": num_thread,
                                  "summary": summarize(runs), "runs": runs})
    best = recommend(data["cells"])
    data["recommended"] = {"kokoro_threads": best["kokoro_threads"], "ollama_threads": best["ollama_threads"]}
    if args.quick:
        print(report(data))
        return 0
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    (output / "report.md").write_text(report(data), encoding="utf-8")
    print(f"Wrote {output / 'results.json'} and report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
