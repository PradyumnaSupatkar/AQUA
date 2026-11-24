"""Record a small speech set for the ASR benchmark (tools/benchmark_asr.py).

Shows each prompt, records after Enter, stops on silence with Aqua's own VAD (the same
end-of-speech rule as hands-free mode), and saves 16 kHz mono WAVs plus prompts.json to
D:\\Aqua\\.cache\\asr-set\\ (gitignored: audio never goes into the repository).

    .venv\\Scripts\\python.exe tools\\record_asr_set.py            record what is missing
    .venv\\Scripts\\python.exe tools\\record_asr_set.py --redo 7   record prompt 7 again
    .venv\\Scripts\\python.exe tools\\record_asr_set.py --device 3 use another microphone

At each prompt: Enter = record, s = skip, q = quit. After a take: Enter = keep, r = redo.
Speak naturally, as you would to Aqua.
"""

import argparse
import json
import sys
import time
import wave
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / ".cache" / "asr-set"

# Real Aqua commands. Grouped by what they test; the ids are stable file names.
PROMPTS = [
    ("write-second-line", "write this is a second line into notepad"),
    ("write-hello", "write hello I am Aqua"),
    ("write-meeting", "write meeting at noon"),
    ("write-shopping", "write buy milk and eggs into notepad"),
    ("open-notepad", "open notepad"),
    ("open-vscode", "open VS Code"),
    ("open-spotify", "open Spotify"),
    ("open-calculator", "open Calculator"),
    ("open-chrome", "open Chrome"),
    ("open-brave", "open Brave"),
    ("open-visual-studio-code", "open Visual Studio Code"),
    ("pause-video", "pause the video"),
    ("pause-music", "pause the music"),
    ("pause-it", "pause it"),
    ("volume-down", "turn the volume down"),
    ("volume-30", "set the volume to 30"),
    ("lock", "lock my screen"),
    ("quality-1080", "set the quality to 1080p"),
    ("back-10", "go back 10 seconds"),
    ("jump-230", "jump to 2:30"),
    ("search-lofi", "search youtube for lofi hip hop"),
    ("play-second", "play the second one"),
    ("whats-playing", "what's playing on youtube"),
    ("question-time", "what time is it"),
    ("question-fact", "who wrote pride and prejudice"),
    ("negation-chrome", "don't open Chrome"),
    ("close-notepad", "close notepad"),
    ("minimise-spotify", "minimise Spotify"),
    ("brightness-up", "make the screen a bit brighter"),
    ("long-sentence", "open notepad and write remember to call the dentist on Monday morning before the "
                      "team meeting starts"),
]


def write_wav(path: Path, samples) -> None:
    import numpy as np
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(pcm.tobytes())


def record_one(microphone, vad, config):
    """One utterance: from Enter until 0.8 s of silence after speech (or a timeout)."""
    import numpy as np
    from aqua.audio.vad import UtteranceCollector
    collector = UtteranceCollector(config)
    vad.reset()
    microphone.resume()
    collector.begin(np.zeros(0, dtype=np.float32))
    try:
        while True:
            frame = microphone.read_frame(timeout=.1)
            if frame is None:
                continue
            event = collector.push(frame, vad.probability(frame))
            if event is not None:
                return event
    finally:
        microphone.pause()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", type=int, help="microphone device index (aqua devices lists them)")
    parser.add_argument("--redo", type=int, action="append", default=[], help="record prompt N again (1-based)")
    args = parser.parse_args(argv)

    from aqua.audio.streaming import MicrophoneStream
    from aqua.audio.vad import StreamingVad, UtteranceConfig

    OUT.mkdir(parents=True, exist_ok=True)
    index_path = OUT / "prompts.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"prompts": []}
    done = {item["id"]: item for item in index["prompts"]}
    redo = {PROMPTS[n - 1][0] for n in args.redo if 1 <= n <= len(PROMPTS)}
    todo = [(n, pid, text) for n, (pid, text) in enumerate(PROMPTS, 1)
            if pid in redo or not (pid in done and (OUT / done[pid]["file"]).exists())]
    if not todo:
        print(f"All {len(PROMPTS)} prompts are recorded in {OUT}. Use --redo N to record one again.")
        return 0
    # Same end-of-speech rule as hands-free mode; a longer start timeout and cap for reading.
    config = UtteranceConfig(end_silence=.8, initial_timeout=8.0, max_utterance=20.0, pre_roll=0.0)
    vad = StreamingVad()
    print(__doc__.split("\n\n")[0])
    print(f"{len(todo)} of {len(PROMPTS)} prompts to record. Saving to {OUT}\n")
    with MicrophoneStream(device=args.device) as microphone:
        microphone.pause()
        print(f"Microphone: {getattr(microphone.device_info, 'name', '?')} "
              f"({microphone.native_sample_rate} Hz, resampled to 16 kHz)\n")
        for number, pid, text in todo:
            while True:
                answer = input(f"[{number}/{len(PROMPTS)}]  \"{text}\"\n   Enter = record, s = skip, q = quit: ")
                answer = answer.strip().casefold()
                if answer == "q":
                    print("Stopped. Run again to continue where you left off.")
                    return 0
                if answer == "s":
                    print("   skipped\n")
                    break
                print("   recording... speak now", flush=True)
                event = record_one(microphone, vad, config)
                if event.status != "complete":
                    print(f"   no usable speech ({event.status}); try again")
                    continue
                print(f"   got {event.duration:.1f} s ({event.speech_seconds:.1f} s of speech)")
                keep = input("   Enter = keep, r = redo: ").strip().casefold()
                if keep == "r":
                    continue
                name = f"{number:02d}-{pid}.wav"
                write_wav(OUT / name, event.samples)
                done[pid] = {"id": pid, "number": number, "text": text, "file": name,
                             "seconds": round(event.duration, 2), "speech_seconds": round(event.speech_seconds, 2),
                             "recorded": datetime.now().astimezone().isoformat(timespec="seconds")}
                index = {"version": 1, "sample_rate": 16000,
                         "microphone": getattr(microphone.device_info, "name", None),
                         "prompts": sorted(done.values(), key=lambda item: item["number"])}
                index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
                print(f"   saved {name}\n")
                time.sleep(.2)
                break
    missing = len(PROMPTS) - len([p for p in done.values() if (OUT / p["file"]).exists()])
    print("Done." if not missing else f"{missing} prompts still missing; run again to record them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
