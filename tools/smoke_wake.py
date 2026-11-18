"""Opt-in local wake-model diagnostics, never application or chat actions.

--synthetic uses Windows' installed System.Speech voice solely to generate
fixed in-memory test fixtures. It is not an Aqua TTS feature. --microphone
measures ambient listening separately and requires explicit user invocation.
No audio, transcript, or fixture files are written, and no model is downloaded.
"""

import argparse
import base64
from dataclasses import dataclass
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter, process_time
import wave

from aqua.audio.backend import AudioError
from aqua.audio.processing import to_16khz
from aqua.audio.streaming import FRAME_SAMPLES, SAMPLE_RATE, MicrophoneStream
from aqua.wake.models import WakeError


# Fixed code: phrases are data on stdin, never interpolated into PowerShell.
SYNTHESIS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$phrase = ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd())
$voice = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$audio = [System.IO.MemoryStream]::new()
try {
    $voice.Rate = 0
    $voice.SetOutputToWaveStream($audio)
    $voice.Speak([string]$phrase)
    $voice.SetOutputToNull()
    [Console]::Out.Write([Convert]::ToBase64String($audio.ToArray()))
} finally {
    $voice.Dispose()
    $audio.Dispose()
}
"""


@dataclass(frozen=True)
class Fixture:
    name: str
    samples: object
    expected_wakes: int


def _synthesize(text: str):
    """Return a float32 mono fixture using installed Windows voices only."""
    import numpy as np

    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    try:
        response = subprocess.run(
            [str(executable), "-NoProfile", "-NonInteractive", "-Command", SYNTHESIS_SCRIPT],
            input=json.dumps(text), capture_output=True, text=True, check=True,
            timeout=20, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        encoded = response.stdout.strip()
        if len(encoded) > 16 * 1024 * 1024:
            raise ValueError("Oversized synthetic fixture")
        raw = base64.b64decode(encoded, validate=True)
        with wave.open(BytesIO(raw), "rb") as wav:
            if wav.getsampwidth() != 2 or wav.getnchannels() not in (1, 2) or wav.getcomptype() != "NONE":
                raise ValueError("Unsupported System.Speech WAV format")
            rate, channels = wav.getframerate(), wav.getnchannels()
            pcm = wav.readframes(wav.getnframes())
        if channels == 2:
            source = np.frombuffer(pcm, dtype=np.int16).reshape(-1, 2)
            pcm = np.rint(source.astype(np.float32).mean(axis=1)).astype(np.int16).tobytes()
        return to_16khz(pcm, rate)
    except (OSError, subprocess.SubprocessError, ValueError, EOFError, wave.Error) as exc:
        raise WakeError("Could not create the local in-memory Windows System.Speech fixture; no voice or audio was downloaded.") from exc


def _fixtures() -> tuple[Fixture, ...]:
    import numpy as np

    wake = _synthesize("Hey Aqua.")
    paused = np.concatenate((wake, np.zeros(round(1.5 * SAMPLE_RATE), dtype=np.float32), _synthesize("Open Chrome.")))
    return (
        Fixture("exact Hey Aqua", wake, 1),
        Fixture("Hey Aqua, open Chrome (continuous)", _synthesize("Hey Aqua, open Chrome."), 1),
        Fixture("Hey Aqua, then 1.5-second pause and Open Chrome", paused, 1),
        Fixture("ordinary Formula One question", _synthesize("What is Formula One?"), 0),
        Fixture("ordinary programming speech", _synthesize("I feel like programming."), 0),
        Fixture("different wake phrase Hey Jarvis", _synthesize("Hey Jarvis, open Chrome."), 0),
    )


def _detector(args):
    from aqua.wake.keyword import SherpaWakeDetector

    return SherpaWakeDetector(args.wake_model_dir, threshold=args.wake_threshold)


def _resident_memory_mib() -> float | None:
    """Read this process' working set through the Windows API; no psutil needed."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                )
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize / (1024 * 1024)
    except (AttributeError, OSError):
        pass
    return None


def _synthetic(detector) -> int:
    import numpy as np

    failures = 0
    for fixture in _fixtures():
        # Leading silence warms the acoustic state; trailing silence flushes
        # the streaming keyword decoder without any offline recognition pass.
        audio = np.concatenate((np.zeros(SAMPLE_RATE // 2, dtype=np.float32), fixture.samples, np.zeros(2 * SAMPLE_RATE, dtype=np.float32)))
        audio = np.pad(audio, (0, -audio.size % FRAME_SAMPLES))
        detector.reset()
        detections = []
        wall_started, cpu_started = perf_counter(), process_time()
        for offset in range(0, audio.size, FRAME_SAMPLES):
            event = detector.feed(audio[offset:offset + FRAME_SAMPLES])
            if event is not None:
                if event.keyword.replace("_", " ").casefold() != "hey aqua":
                    raise WakeError("The configured detector returned a different keyword.")
                detections.append((offset + FRAME_SAMPLES) / SAMPLE_RATE)
        wall, cpu = perf_counter() - wall_started, process_time() - cpu_started
        passed = len(detections) == fixture.expected_wakes
        failures += not passed
        positions = ", ".join(f"{position:.3f}s" for position in detections) or "none"
        print(
            f"{'PASS' if passed else 'FAIL'} {fixture.name}: wakes={len(detections)}; expected={fixture.expected_wakes}; "
            f"event audio positions={positions}; audio={audio.size / SAMPLE_RATE:.3f}s; CPU={cpu:.3f}s; processing wall={wall:.3f}s"
        )
    rss = _resident_memory_mib()
    if rss is not None:
        print(f"Wake diagnostic process working set: {rss:.1f} MiB (excludes Whisper and Ollama).")
    print("Synthetic fixtures test the exact phrase, but do not establish microphone accuracy or a background false-activation rate.")
    return 1 if failures else 0


def _microphone(detector, args) -> int:
    detector.reset()
    print(f"Ambient wake benchmark for {args.seconds:g}s. No Whisper, Qwen, or application actions are used.", flush=True)
    hits, frames, detector_wall = 0, 0, 0.0
    with MicrophoneStream(device=args.device, sample_rate=args.sample_rate) as microphone:
        wall_started, cpu_started = perf_counter(), process_time()
        while perf_counter() - wall_started < args.seconds:
            frame = microphone.read_frame(timeout=.1)
            if frame is None:
                continue
            started = perf_counter()
            event = detector.feed(frame)
            detector_wall += perf_counter() - started
            frames += 1
            if event is not None:
                if event.keyword.replace("_", " ").casefold() != "hey aqua":
                    raise WakeError("The configured detector returned a different keyword.")
                hits += 1
                print(f"Wake event {hits} at {perf_counter() - wall_started:.3f}s; no action executed.")
        wall, cpu = perf_counter() - wall_started, process_time() - cpu_started
        print(f"Native microphone: {microphone.native_sample_rate} Hz; detector input: {SAMPLE_RATE} Hz; frames={frames}.")
    cpu_percent = 100 * cpu / wall if wall else 0.0
    detector_percent = 100 * detector_wall / wall if wall else 0.0
    print(
        f"Ambient benchmark: wall={wall:.3f}s; process CPU={cpu:.3f}s; CPU={cpu_percent:.2f}% of one logical core "
        f"({cpu_percent / max(1, os.cpu_count() or 1):.2f}% of all logical cores); detector work={detector_percent:.2f}% of wall; wakes={hits}."
    )
    rss = _resident_memory_mib()
    if rss is not None:
        print(f"Wake diagnostic process working set: {rss:.1f} MiB (excludes Whisper and Ollama).")
    print("Wake events count as false activations only if nobody said Hey Aqua during this interval. No audio was saved.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--synthetic", action="store_true", help="Explicitly synthesize fixed local Windows voice fixtures in memory.")
    mode.add_argument("--microphone", action="store_true", help="Explicitly capture ambient audio for a wake-only resource benchmark.")
    parser.add_argument("--seconds", type=float, default=10.0, help="Ambient benchmark duration; 0.5 to 60 seconds.")
    parser.add_argument("--device", type=int)
    parser.add_argument("--sample-rate", type=int)
    parser.add_argument("--wake-model-dir", type=Path)
    parser.add_argument("--wake-threshold", type=float, default=.25)
    args = parser.parse_args(argv)
    if not args.synthetic and not args.microphone:
        parser.print_help()
        print("No microphone or model was opened. Choose --synthetic or --microphone explicitly.", file=sys.stderr)
        return 2
    if not .5 <= args.seconds <= 60:
        parser.error("--seconds must be finite and between 0.5 and 60.")
    try:
        detector = _detector(args)
        return _synthetic(detector) if args.synthetic else _microphone(detector, args)
    except KeyboardInterrupt:
        print("\nWake diagnostic cancelled; no audio was saved.")
        return 130
    except (AudioError, WakeError, ValueError) as exc:
        print(f"Wake diagnostic error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
