"""Local microphone diagnostics, explicit model setup, and offline recognition."""

import argparse
import sys
from collections.abc import Sequence
from math import isfinite
from pathlib import Path

from aqua import __version__
from aqua.chat import run_text_chat, run_voice_chat
from aqua.audio.backend import AudioError
from aqua.audio.devices import list_devices
from aqua.audio.diagnostics import analyze_audio
from aqua.audio.levels import AudioLevels
from aqua.audio.recording import RECORD_SECONDS, Recording, record_test
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, DEFAULT_CONTEXT_SIZE, DEFAULT_TEMPERATURE, GenerationSettings
from aqua.speech.comparison import compare_recording, load_models
from aqua.speech.models import BASE_MODEL_DIR, DEFAULT_MODEL, MODEL_DIRS, SpeechError, resolve_model_dir, setup_model
from aqua.speech.recognizer import OfflineRecognizer, Transcript
from aqua.speech.vocabulary import DEFAULT_HOTWORDS, normalize_hotwords


def _configure_output() -> None:
    """Preserve Unicode responses in Windows consoles and redirected output."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError, TypeError):
                # Embedded/test streams may expose an unsupported hook. Do not
                # replace them, change stdin, or alter the model's answer text.
                pass


def _positive_integer(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _device_index(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative device ID")
    return number


def _gain_db(value: str) -> float:
    gain = float(value)
    if not isfinite(gain) or not -30 <= gain <= 30:
        raise argparse.ArgumentTypeError("must be a finite gain between -30 and +30 dB")
    return gain


def _timeout_seconds(value: str) -> float:
    timeout = float(value)
    if not isfinite(timeout) or not 0 < timeout <= 600:
        raise argparse.ArgumentTypeError("must be finite, positive, and at most 600 seconds")
    return timeout


def _finite_range(minimum: float, maximum: float, *, include_minimum: bool = True):
    """Build finite scalar validators without starting an audio/model backend."""
    def parse(value: str) -> float:
        number = float(value)
        lower_bound = number >= minimum if include_minimum else number > minimum
        if not isfinite(number) or not lower_bound or number > maximum:
            comparison = "at least" if include_minimum else "greater than"
            raise argparse.ArgumentTypeError(
                f"must be finite, {comparison} {minimum:g}, and at most {maximum:g}"
            )
        return number
    return parse


def run_handsfree(**options) -> int:
    """Load wake dependencies only for the explicitly selected hands-free mode."""
    from aqua.handsfree import run_handsfree as run
    return run(**options)


def run_level_probe(**options) -> int:
    """Load the read-only stage diagnostic without any inference model."""
    from aqua.audio.level_probe import run_level_probe as run
    return run(**options)


def setup_wake_model(model_dir: Path | None = None, *, download: bool = False) -> Path:
    """Keep optional wake-model setup separate from all existing commands."""
    from aqua.wake.models import setup_model as setup
    return setup(model_dir=model_dir, download=download)


def _chat_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--allow-text-entry", action="append", choices=("notepad",), default=[],
                        help="explicitly enable guarded blank Edit text-entry proposals for the Stage 2B.1 application")
    parser.add_argument("--model", default=DEFAULT_CHAT_MODEL, help=f"installed Ollama model tag; default: {DEFAULT_CHAT_MODEL}")
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE, help=f"sampling temperature, 0 to 2; default: {DEFAULT_TEMPERATURE}")
    parser.add_argument("--thinking", choices=("auto", "on", "off"), default="auto", help="supported Ollama thinking control; auto preserves the model-appropriate default")
    parser.add_argument("--seed", type=int, help="optional Ollama sampling seed; repeatability can still vary by runtime")
    parser.add_argument("--top-p", type=float, help="optional nucleus sampling probability, 0 to 1; omit to retain the model default")
    parser.add_argument("--top-k", type=int, help="optional sampling candidate count, 0 to 1000")
    parser.add_argument("--presence-penalty", type=float, help="optional presence penalty, -2 to 2")
    parser.add_argument("--repeat-penalty", type=float, help="optional repetition penalty, 0 to 4")
    parser.add_argument("--max-tokens", type=_positive_integer, default=1024, help="generation token budget, including hidden thinking; default: 1024")
    parser.add_argument("--context-size", type=_positive_integer, default=DEFAULT_CONTEXT_SIZE, help=f"Ollama context tokens; default: {DEFAULT_CONTEXT_SIZE}")
    parser.add_argument("--max-turns", type=_positive_integer, default=8, help="maximum completed conversation pairs; default: 8")
    parser.add_argument("--history-chars", type=_positive_integer, default=12000, help="conversation character budget including system prompt; default: 12000")
    parser.add_argument("--timeout", type=_timeout_seconds, default=120.0, help="local request timeout in seconds; default: 120")
    parser.add_argument("--diagnostics", action="store_true", help="show response and voice-stage latency measurements")


def _voice_options(parser: argparse.ArgumentParser, *, speak_default: bool) -> None:
    """Spoken-reply (text-to-speech) options shared by chat, voice-chat and handsfree."""
    from aqua.voice.engines import DEFAULT_ENGINE, ENGINE_NAMES
    speaking = parser.add_mutually_exclusive_group()
    speaking.add_argument("--speak", action="store_true", dest="speak",
                          help="speak replies aloud" + (" (default)" if speak_default else ""))
    speaking.add_argument("--no-speak", action="store_false", dest="speak",
                          help="text replies only" + ("" if speak_default else " (default)"))
    parser.set_defaults(speak=speak_default)
    parser.add_argument("--tts-engine", choices=ENGINE_NAMES, default=DEFAULT_ENGINE,
                        help=f"local text-to-speech engine; default: {DEFAULT_ENGINE}")
    parser.add_argument("--voice", metavar="NAME", help="voice name or speaker id; default: the engine's default voice")
    parser.add_argument("--voice-model-dir", type=Path, metavar="PATH", help="local voice model directory instead of the default")
    parser.add_argument("--speech-rate", type=_finite_range(.5, 2), default=1.0, metavar="RATE",
                        help="speaking speed, 0.5 to 2; default: 1")


def _speech_options(parser: argparse.ArgumentParser) -> None:
    speech = parser.add_mutually_exclusive_group()
    speech.add_argument("--speech-model", choices=tuple(MODEL_DIRS), help=f"local Whisper model; default: {DEFAULT_MODEL}")
    speech.add_argument("--speech-model-dir", type=Path, metavar="PATH", help="complete local Whisper directory instead of a named model")
    parser.add_argument("--device", type=_device_index, metavar="ID", help="microphone ID; default: current Windows input")
    parser.add_argument("--sample-rate", type=_positive_integer, metavar="HZ", help="override capture rate before resampling")
    parser.add_argument("--gain-db", type=_gain_db, default=0.0, metavar="DB", help="recognition gain; default: 0 (no automatic gain)")
    parser.add_argument("--language", metavar="CODE", help="spoken language code, e.g. en; default: automatic detection")
    hints = parser.add_mutually_exclusive_group()
    hints.add_argument("--hints", action="store_true", dest="hints", help="enable optional speech vocabulary hints")
    hints.add_argument("--no-hints", action="store_false", dest="hints", help="disable speech hints (default)")
    parser.set_defaults(hints=False)
    parser.add_argument("--hotwords", metavar="TEXT", help="custom speech vocabulary; requires --hints")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aqua: local text, push-to-record, and hands-free voice conversations.")
    parser.add_argument("--version", action="version", version=f"Aqua {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    devices = commands.add_parser("devices", help="list microphone devices without recording")
    devices.add_argument("--all", action="store_true", help="include output-only devices")
    mic = commands.add_parser("mic-test", help="record five seconds in memory and display levels")
    mic.add_argument("--device", type=_device_index, metavar="ID", help="input ID from 'aqua devices'")
    mic.add_argument("--sample-rate", type=_positive_integer, metavar="HZ", help="override the device's native default rate")
    mic.add_argument("--diagnostics", action="store_true", help="show source levels and energy activity")
    probe = commands.add_parser("mic-levels", help="measure native-to-wake signal levels in memory; no models or gain changes")
    probe.add_argument("--device", type=_device_index, metavar="ID", help="input ID; default: current Windows input")
    probe.add_argument("--sample-rate", type=_positive_integer, metavar="HZ", help="override capture rate before real resampling")
    probe.add_argument("--seconds", type=_finite_range(1, 30), default=8.0, help="bounded diagnostic duration, 1 to 30 seconds; default: 8")
    probe.add_argument("--quiet-seconds", type=_finite_range(0, 10), default=2.0, help="initial quiet baseline, shorter than capture; default: 2")
    probe.add_argument("--stereo", action="store_true", help="separate two-channel probe for channel/cancellation checks; production stays mono")
    probe.add_argument("--compare-backends", action="store_true", help="sequential MME/WASAPI samples only when exact Windows endpoint IDs match")
    probe.add_argument("--check-wake", action="store_true", help="count local Hey Aqua detections without transcription, Qwen, or app actions")
    probe.add_argument("--wake-score", type=_finite_range(0, 10, include_minimum=False), default=1.0, help="wake-check keyword boost; default: 1")
    probe.add_argument("--wake-threshold", type=_finite_range(0, 1, include_minimum=False), default=.25, help="wake-check threshold; default: 0.25")
    transcribe = commands.add_parser("transcribe", help="record five seconds and recognize speech entirely offline")
    transcribe.add_argument("--device", type=_device_index, metavar="ID", help="input ID from 'aqua devices'")
    transcribe.add_argument("--sample-rate", type=_positive_integer, metavar="HZ", help="override the microphone rate before resampling")
    transcribe.add_argument("--diagnostics", action="store_true", help="show source levels, energy activity, and VAD duration")
    transcribe.add_argument("--gain-db", type=_gain_db, default=0.0, metavar="DB", help="optional recognition-only gain, -30 to +30 dB; default 0")
    transcribe.add_argument("--language", metavar="CODE", help="spoken language code, e.g. en; default: automatic detection")
    selection = transcribe.add_mutually_exclusive_group()
    selection.add_argument("--model", choices=tuple(MODEL_DIRS), help=f"locally installed speech model; default: {DEFAULT_MODEL}")
    selection.add_argument("--model-dir", type=Path, metavar="PATH", help="complete local CTranslate2 Whisper model directory")
    hints = transcribe.add_mutually_exclusive_group()
    hints.add_argument("--hints", action="store_true", dest="hints", help="enable vocabulary context; transcripts are never replaced")
    hints.add_argument("--no-hints", action="store_false", dest="hints", help="disable vocabulary hints (default)")
    transcribe.set_defaults(hints=False)
    transcribe.add_argument("--hotwords", metavar="TEXT", help="custom vocabulary instead of defaults; requires --hints")
    compare = commands.add_parser("compare", help="record once and compare Small without and with vocabulary hints")
    comparison_selection = compare.add_mutually_exclusive_group()
    comparison_selection.add_argument("--model", choices=tuple(MODEL_DIRS), help=f"compare this local model's hint settings; default: {DEFAULT_MODEL}")
    comparison_selection.add_argument("--include-small", action="store_true", help="compare both Base and Small on the same audio (legacy four-pass mode)")
    compare.add_argument("--device", type=_device_index, metavar="ID", help="input ID; default: Windows default input")
    compare.add_argument("--sample-rate", type=_positive_integer, metavar="HZ", help="override capture rate before resampling")
    compare.add_argument("--diagnostics", action="store_true", help="show source and recognition diagnostics")
    compare.add_argument("--gain-db", type=_gain_db, default=0.0, metavar="DB", help="recognition gain; default: 0 (no automatic gain)")
    compare.add_argument("--language", metavar="CODE", help="spoken language code, e.g. en; default: automatic detection")
    compare.add_argument("--hotwords", metavar="TEXT", help="custom context for hinted passes instead of the default vocabulary")
    setup = commands.add_parser("setup-model", help="verify the local base model; download only with --download")
    setup.add_argument("--model-dir", type=Path, default=BASE_MODEL_DIR, metavar="PATH")
    setup.add_argument("--download", action="store_true", help="explicitly permit the one-time approximately 148 MB base model download")
    wake_setup = commands.add_parser("setup-wake", help="verify local Hey Aqua assets; download only with --download")
    wake_setup.add_argument("--model-dir", type=Path, metavar="PATH", help="local sherpa keyword-spotting model directory")
    wake_setup.add_argument("--download", action="store_true", help="explicitly permit downloading the configured wake assets")
    chat = commands.add_parser("chat", help="interactive text chat with the local Ollama service")
    _chat_options(chat)
    _voice_options(chat, speak_default=False)
    voice = commands.add_parser("voice-chat", help="press Enter for each speech-to-text chat turn; add --speak for spoken replies")
    _chat_options(voice)
    _speech_options(voice)
    _voice_options(voice, speak_default=False)
    handsfree = commands.add_parser("handsfree", help='listen locally for "Hey Aqua", then capture speech until silence')
    _chat_options(handsfree)
    _speech_options(handsfree)
    _voice_options(handsfree, speak_default=True)
    handsfree.add_argument("--wake-model-dir", type=Path, metavar="PATH", help="complete local sherpa keyword-spotting directory")
    handsfree.add_argument("--wake-threshold", type=_finite_range(0, 1), default=.25, help="keyword detection threshold, 0 to 1; default: 0.25")
    handsfree.add_argument("--wake-score", type=_finite_range(0, 10, include_minimum=False), default=1.0, help="keyword boost, greater than 0 to 10; default: 1")
    handsfree.add_argument("--wake-cooldown", type=_finite_range(0, 30), default=2.0, metavar="SECONDS", help="minimum interval between wake activations; default: 2")
    handsfree.add_argument("--end-silence", type=_finite_range(.1, 3), default=.8, metavar="SECONDS", help="sustained silence before ending a turn; default: 0.8")
    handsfree.add_argument("--initial-timeout", type=_finite_range(.5, 15), default=5.0, metavar="SECONDS", help="wait for speech after wake; default: 5")
    handsfree.add_argument("--max-utterance", type=_finite_range(1, 30), default=15.0, metavar="SECONDS", help="discard speech exceeding this duration; default: 15")
    handsfree.add_argument("--pre-roll", type=_finite_range(0, 2), default=.5, metavar="SECONDS", help="bounded audio retained before wake; default: 0.5")
    handsfree.add_argument("--vad-threshold", type=_finite_range(.01, 1), default=.5, help="speech activity threshold, 0.01 to 1; default: 0.5")
    from aqua.benchmark.runner import add_arguments
    benchmark = commands.add_parser("benchmark", help="reproducible installed-model intelligence benchmark; execution disabled")
    add_arguments(benchmark)
    return parser


def _format_levels(levels: AudioLevels) -> str:
    return (
        f"Peak {levels.peak:6.1%} ({levels.peak_dbfs:6.1f} dBFS) | "
        f"RMS {levels.rms:6.1%} ({levels.rms_dbfs:6.1f} dBFS)"
    )


def _show_progress(elapsed: float, levels: AudioLevels) -> None:
    message = f"{elapsed:3.1f}/{RECORD_SECONDS}s | {_format_levels(levels)}"
    if sys.stdout.isatty():
        print(f"\r{message}", end="", flush=True)


def _report_capture(result: Recording) -> None:
    if sys.stdout.isatty():
        print()
    if result.device_info is not None:
        device = result.device_info
        name = " ".join(device.name.split())
        print(f"Input {device.index}: {name} ({device.host_api}).")
    print(f"Captured {result.duration:.1f}s, {result.frames} mono int16 samples at {result.sample_rate} Hz.")
    print(_format_levels(result.levels))
    if result.levels.peak == 0:
        print("Warning: no signal detected. Check mute, input level, and selected microphone.")
    if result.levels.clipped_samples:
        print(f"Warning: {result.levels.clipped_samples} samples reached full scale; lower microphone gain.")
    if result.overflow_count:
        print(f"Warning: input overflow in {result.overflow_count} blocks; some audio was lost. Close busy apps and retry.")


def _report_diagnostics(result: Recording) -> None:
    diagnostic = analyze_audio(result.pcm, result.sample_rate)
    print("Source diagnostics (before gain/resampling; 20 ms energy heuristic, not speech VAD):")
    print(
        f"  Quiet-frame RMS (p20): {diagnostic.quiet_frame_rms_dbfs:.1f} dBFS | "
        f"Louder-frame RMS (p90): {diagnostic.p90_frame_rms_dbfs:.1f} dBFS"
    )
    print(
        f"  Energy-active audio: {diagnostic.active_fraction:.1%} | "
        f"Active-frame RMS: {diagnostic.active_rms_dbfs:.1f} dBFS | "
        f"Gate: {diagnostic.activity_threshold_dbfs:.1f} dBFS"
    )
    print(f"  Source samples at full scale: {diagnostic.levels.clipped_samples}")
    if diagnostic.p90_frame_rms_dbfs < -45:
        print("  Input is quiet. Compare microphone IDs and Windows input gain while speaking across the five seconds.")
    if not diagnostic.active_fraction:
        print("  No audio crossed the energy gate; quiet speech can still be present.")


def _report_transcript(transcript: Transcript, *, diagnostics: bool, gain_db: float) -> None:
    if diagnostics:
        print(
            f"Whisper input: 16000 Hz mono, gain {gain_db:+g} dB | "
            f"Peak {transcript.input_peak_dbfs:.1f} dBFS | RMS {transcript.input_rms_dbfs:.1f} dBFS"
        )
        print(f"VAD-retained audio (including padding): {transcript.speech_seconds:.2f}s")
        if transcript.language:
            print(f"Language: {transcript.language} (model probability {transcript.language_probability:.1%})")
    if transcript.saturated_samples:
        print(f"Warning: {transcript.saturated_samples} recognition-input samples reached full scale; reduce --gain-db or microphone gain.")
    if transcript.text:
        print(f"Recognized text: {transcript.text}")
    else:
        print("No speech recognized. Check the selected microphone and try --diagnostics or --language en.")


def main(argv: Sequence[str] | None = None) -> int:
    _configure_output()
    parser = _parser()
    args = parser.parse_args(argv)
    hotwords = DEFAULT_HOTWORDS
    if args.command in ("transcribe", "compare", "voice-chat", "handsfree") and args.hotwords is not None:
        if args.command in ("transcribe", "voice-chat", "handsfree") and not args.hints:
            parser.error("--hotwords requires --hints; omit custom hotwords when hints are disabled")
        try:
            hotwords = normalize_hotwords(args.hotwords)
        except ValueError as exc:
            parser.error(str(exc))
    try:
        if args.command == "benchmark":
            from aqua.benchmark.runner import run
            try:
                return run(args)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                print(f"Benchmark setup/report error: {exc}", file=sys.stderr)
                return 2
        if args.command == "mic-levels":
            if not args.check_wake and (args.wake_score != 1.0 or args.wake_threshold != .25):
                parser.error("--wake-score and --wake-threshold require --check-wake for mic-levels")
            options = {}
            if args.compare_backends:
                options["compare_backends"] = True
            if args.check_wake:
                options.update(check_wake=True, wake_score=args.wake_score, wake_threshold=args.wake_threshold)
            return run_level_probe(
                device=args.device, sample_rate=args.sample_rate,
                seconds=args.seconds, quiet_seconds=args.quiet_seconds, stereo=args.stereo,
                **options,
            )
        if args.command in ("chat", "voice-chat", "handsfree"):
            try:
                settings = GenerationSettings(
                    temperature=args.temperature, num_predict=args.max_tokens, num_ctx=args.context_size,
                    thinking=args.thinking, seed=args.seed,
                    top_p=args.top_p, top_k=args.top_k,
                    presence_penalty=args.presence_penalty, repeat_penalty=args.repeat_penalty,
                )
            except ValueError as exc:
                parser.error(str(exc))
            common = {
                "model": args.model, "settings": settings, "timeout": args.timeout,
                "max_turns": args.max_turns, "max_chars": args.history_chars,
                "diagnostics": args.diagnostics,
                "speak": args.speak, "tts_engine": args.tts_engine, "voice": args.voice,
                "voice_model_dir": args.voice_model_dir, "speech_rate": args.speech_rate,
            }
            if args.allow_text_entry:
                common["text_entry_apps"] = tuple(dict.fromkeys(args.allow_text_entry))
            if args.command == "chat":
                return run_text_chat(**common)
            speech_path = resolve_model_dir(args.speech_model, args.speech_model_dir)
            speech_options = dict(
                **common, speech_model_dir=speech_path, device=args.device,
                sample_rate=args.sample_rate, gain_db=args.gain_db, language=args.language,
                hotwords=hotwords if args.hints else None,
            )
            if args.command == "voice-chat":
                return run_voice_chat(**speech_options)
            return run_handsfree(
                **speech_options, wake_model_dir=args.wake_model_dir,
                wake_threshold=args.wake_threshold, wake_score=args.wake_score,
                wake_cooldown=args.wake_cooldown, end_silence=args.end_silence,
                initial_timeout=args.initial_timeout, max_utterance=args.max_utterance,
                pre_roll=args.pre_roll, vad_threshold=args.vad_threshold,
            )
        if args.command == "setup-wake":
            from aqua.wake.models import WakeError
            try:
                path = setup_wake_model(args.model_dir, download=args.download)
            except WakeError as exc:
                print(f"Wake setup error: {exc}", file=sys.stderr)
                return 1
            print(f"Offline Hey Aqua wake assets ready: {path}")
            return 0
        if args.command == "setup-model":
            if args.download:
                print(f"Setting up the approved multilingual base model at {args.model_dir} (approximately 148 MB if missing).")
            path = setup_model(args.model_dir, download=args.download)
            print(f"Offline base model ready: {path}")
            return 0
        if args.command == "devices":
            devices = list_devices(include_output=args.all)
            if not devices:
                print("No audio inputs found. Connect a microphone and check Windows audio settings.", file=sys.stderr)
                return 1
            for device in devices:
                marker = "*" if device.is_default_input else " "
                name = " ".join(device.name.split())
                print(
                    f"{marker} {device.index:3} | {name} | {device.host_api} | "
                    f"in: {device.input_channels}, out: {device.output_channels} | "
                    f"{device.default_sample_rate:g} Hz"
                )
            print("* = default input; IDs may change after devices are reconnected.")
            return 0
        recognizer = None
        comparison_models = []
        if args.command == "transcribe":
            model_dir = resolve_model_dir(args.model, args.model_dir)
            print(f"Loading the local speech model from {model_dir} (CPU INT8; offline).")
            # Validate and load before capturing, so setup failures never open
            # the microphone or discard a sentence while waiting for a model.
            recognizer = OfflineRecognizer(model_dir)
            if args.hints:
                print("Vocabulary hints enabled as recognition context.")
        elif args.command == "compare":
            if args.include_small:
                paths = {"base": MODEL_DIRS["base"], "small": MODEL_DIRS["small"]}
            else:
                selected = args.model or DEFAULT_MODEL
                paths = {selected: resolve_model_dir(args.model)}
            print("Preparing local comparison models (CPU INT8; offline).")
            comparison_models = load_models(paths)
            for model in comparison_models:
                print(f"Model {model.name}: loading {model.load_seconds:.3f}s | {model.model_dir}")
        print(f"Recording {RECORD_SECONDS} seconds locally. Speak into the microphone. Ctrl+C cancels.")
        result = record_test(device=args.device, sample_rate=args.sample_rate, on_progress=_show_progress)
        _report_capture(result)
        if args.diagnostics:
            _report_diagnostics(result)
        if recognizer is not None:
            options = {"gain_db": args.gain_db, "language": args.language}
            if args.hints:
                options["hotwords"] = hotwords
            transcript = recognizer.transcribe(result.pcm, result.sample_rate, **options)
            _report_transcript(transcript, diagnostics=args.diagnostics, gain_db=args.gain_db)
        if comparison_models:
            passes = compare_recording(
                result, comparison_models, hotwords=hotwords, language=args.language, gain_db=args.gain_db
            )
            for comparison in passes:
                state = "with hints" if comparison.hints_enabled else "without hints"
                print(f"\n{comparison.model_name.title()} {state}:")
                _report_transcript(comparison.transcript, diagnostics=args.diagnostics, gain_db=args.gain_db)
                print(
                    f"Transcription: {comparison.transcription_seconds:.3f}s | "
                    f"Captured audio: {comparison.audio_seconds:.3f}s | "
                    f"RTF: {comparison.real_time_factor:.3f}"
                )
            print("All passes used the same in-memory capture. Each model was loaded once.")
            print("Transcription time includes resampling and VAD; later passes can benefit from caches.")
        print("Audio was kept in process memory only; no recording was saved.")
        return 0
    except AudioError as exc:
        if sys.stdout.isatty():
            print()
        print(f"Audio error: {exc}", file=sys.stderr)
        return 1
    except SpeechError as exc:
        print(f"Speech setup/recognition error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nMicrophone test cancelled; the microphone was released and no recording was saved.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
