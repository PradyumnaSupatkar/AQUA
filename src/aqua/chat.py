"""Interactive coordination for local text and push-to-record voice chat.

All input modes share one conversational tool controller. Speech capture stays
separate; model tool proposals pass the fixed launcher boundary, never a shell.
"""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from math import isfinite
import sys
from time import perf_counter

from aqua.actions.tools import ActionState
from aqua.audio.backend import AudioError
from aqua.audio.recording import RECORD_SECONDS, Recording, record_test
from aqua.brain.conversation import Conversation
from aqua.brain.controller import ConversationController
from aqua.brain.ollama import (
    DEFAULT_CHAT_MODEL, ChatEvent, GenerationSettings,
    OllamaClient, OllamaError,
)
from aqua.speech.models import SpeechError
from aqua.speech.recognizer import OfflineRecognizer, Transcript
from aqua.speech.reliability import transcript_rejection_reason


def _help(*, voice: bool) -> None:
    print("/quit exits; /clear forgets this session; /help shows these commands.")
    if voice:
        print(f"Press Enter to record {RECORD_SECONDS} seconds. Responses appear as text.")
    else:
        print("Type a message to chat. Responses stream from the local model.")


def _control_command(
    text: str, conversation: Conversation, *, voice: bool,
    action_state: ActionState | None = None,
) -> str | None:
    if not text.startswith("/"):
        return None
    from aqua.brain.entry_objective import clear_pending
    clear_pending(conversation)
    (action_state if action_state is not None else conversation.action_state).clear()
    if text == "/quit":
        return "quit"
    if text == "/clear":
        conversation.clear()
        print("Conversation cleared from this session.")
    elif text == "/help":
        _help(voice=voice)
    else:
        print("Unknown command. Use /help for available commands.")
    return "continue"


def _nonnegative(value: object) -> float | None:
    """Optional diagnostics must never break an otherwise valid reply."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _counter(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _duration(value: object) -> float | None:
    value = _counter(value)
    return value / 1_000_000_000 if value is not None else None


def _seconds(value: float | None) -> str:
    return f"{value:.3f}s" if value is not None else "n/a"


def _report_server_metrics(event: ChatEvent) -> None:
    """Separate server work from observed streaming phases without reasoning text."""
    load = _duration(event.load_duration)
    prompt = _duration(event.prompt_eval_duration)
    generation = _duration(event.eval_duration)
    total = _duration(event.total_duration)
    prompt_tokens = _counter(event.prompt_eval_count)
    generated_tokens = _counter(event.eval_count)
    throughput = generated_tokens / generation if generated_tokens is not None and generation else None
    prompt_count = str(prompt_tokens) if prompt_tokens is not None else "n/a"
    generated_count = str(generated_tokens) if generated_tokens is not None else "n/a"
    speed = f"{throughput:.2f} tok/s" if throughput is not None else "n/a"
    print(
        f"Ollama server: load {_seconds(load)}; prompt {_seconds(prompt)} ({prompt_count} tokens); "
        f"generation {_seconds(generation)} ({generated_count} total tokens, {speed}); "
        f"total {_seconds(total)}"
    )
    print("Generation counts and throughput include any hidden thinking; answer-only token count is unavailable.")
    first_stream = _nonnegative(event.first_stream_seconds)
    first_thinking = _nonnegative(event.first_thinking_seconds)
    first_content = _nonnegative(event.first_content_seconds)
    phase = (
        first_content - first_thinking
        if first_content is not None and first_thinking is not None and first_content >= first_thinking
        else None
    )
    chunks = _counter(event.thinking_chunks)
    characters = _counter(event.thinking_characters)
    print(
        f"Ollama channels: first generated chunk {_seconds(first_stream)}; "
        f"first thinking chunk {_seconds(first_thinking)}; "
        f"thinking-to-answer interval {_seconds(phase)} "
        "(observed wall interval, not exact server thinking time)"
    )
    print(
        f"Hidden stream volume: {chunks if chunks is not None else 'n/a'} chunks, "
        f"{characters if characters is not None else 'n/a'} characters; these are not token counts."
    )


def _respond(
    client: OllamaClient,
    conversation: Conversation,
    user_text: str,
    *,
    model: str,
    settings: GenerationSettings,
    diagnostics: bool,
    action_state: ActionState | None = None,
    speaker=None,
    wait_for_speech: bool = True,
) -> bool:
    """Render the shared controller without a separate intent-classifier call."""
    return _respond_result(
        client, conversation, user_text, model=model, settings=settings, diagnostics=diagnostics,
        action_state=action_state, speaker=speaker, wait_for_speech=wait_for_speech,
    ).ok


def _speech_diagnostics(speaker, *, wait: bool) -> None:
    if wait:
        try:
            speaker.wait(120)
        except KeyboardInterrupt:
            speaker.stop()
    timings = speaker.timings
    print(timings.describe() if timings is not None else "Speech output: nothing spoken this turn.")


def _respond_result(
    client: OllamaClient,
    conversation: Conversation,
    user_text: str,
    *,
    model: str,
    settings: GenerationSettings,
    diagnostics: bool,
    action_state: ActionState | None = None,
    speaker=None,
    wait_for_speech: bool = True,
):
    """Same as _respond, returning the full TurnResult.

    ``speaker`` (a SpeechOutput or None) hears exactly the text that is
    printed. It is stopped first, so a new turn always silences the old one;
    playback may continue after this returns.
    """
    if action_state is not None:
        conversation.bind_action_state(action_state)
    if speaker is not None:
        speaker.stop()

    def on_text(content):
        print(content, end="", flush=True)
        if speaker is not None:
            speaker.feed(content)

    print("Aqua: ", end="", flush=True)
    controller = ConversationController(client, conversation, model=model, settings=settings, clock=perf_counter)
    try:
        result = controller.respond(user_text, on_text=on_text)
    finally:
        if speaker is not None:
            speaker.finish_turn()
    print()
    if result.error:
        print(f"Chat error ({result.stage}): {result.error}", file=sys.stderr)
    if diagnostics:
        first = f"{result.first_token_seconds:.3f}s" if result.first_token_seconds is not None else "n/a"
        print(f"Ollama: first visible token {first}; complete response {result.total_seconds:.3f}s")
        print(f"Conversation: chat requests {result.requests}; outcome {result.outcome}; stage {result.stage}")
        if result.task is not None:
            task = result.task
            print(f"Task: model decisions {task.decisions}; tool operations {len(task.operations)}; "
                  f"completed {len(task.completed_operations)}; failed {len(task.failed_operations)}; "
                  f"unknown {len(task.unknown_operations)}; status {task.status}")
        if result.stage == "authorization":
            latest = conversation.verified_results[-1:]
            if latest and latest[0].get("authorization_reason"):
                print(f"Entry authorization: {latest[0]['authorization_reason']}")
        print(f"Sampling: temperature {settings.temperature:g}; thinking {settings.thinking}; seed {settings.seed if settings.seed is not None else 'unset'}; context {settings.num_ctx}; output limit {settings.num_predict}")
        overrides = [f"{name}={getattr(settings, name):g}" for name in ("top_p", "top_k", "presence_penalty", "repeat_penalty") if getattr(settings, name) is not None]
        if overrides:
            print("Sampling overrides: " + "; ".join(overrides))
        if result.completion is not None:
            _report_server_metrics(result.completion)
        if speaker is not None and wait_for_speech:
            _speech_diagnostics(speaker, wait=True)
    return result


def _open_speaker(*, speak: bool, tts_engine: str, voice, voice_model_dir, speech_rate: float):
    """Load the voice once per session; missing voices mean text-only, not an error."""
    if not speak:
        return None
    from aqua.voice.output import open_speaker
    return open_speaker(engine=tts_engine, voice=voice, model_dir=voice_model_dir, rate=speech_rate)


def _close_speaker(speaker) -> None:
    if speaker is not None:
        speaker.close()


def _start(
    *, model: str, timeout: float, max_turns: int, max_chars: int,
    settings: GenerationSettings | None,
) -> tuple[OllamaClient, Conversation, GenerationSettings] | None:
    try:
        generation = settings if settings is not None else GenerationSettings()
        conversation = Conversation(max_turns=max_turns, max_chars=max_chars)
        client = OllamaClient(timeout=timeout)
        info = client.check_model(model)
        client.validate_thinking(model, generation.thinking)
    except (OllamaError, ValueError) as exc:
        print(f"Chat setup error: {exc}", file=sys.stderr)
        return None
    print(f"Local Ollama {info.version}; model {info.name}. Conversation stays in memory.")
    if getattr(info, "thinking_control", "") == "hidden":
        print("This local model uses hidden thinking; the first visible reply may take longer.")
    return client, conversation, generation


def run_text_chat(
    *,
    model: str = DEFAULT_CHAT_MODEL,
    settings: GenerationSettings | None = None,
    timeout: float = 120.0,
    max_turns: int = 8,
    max_chars: int = 12000,
    diagnostics: bool = False,
    text_entry_apps: tuple[str, ...] = (),
    speak: bool = False,
    tts_engine: str = "kokoro",
    voice: str | None = None,
    voice_model_dir: Path | None = None,
    speech_rate: float = 1.0,
) -> int:
    """Run multiple local text turns until a command, EOF, or prompt Ctrl+C."""
    speaker = None
    try:
        session = _start(
            model=model, timeout=timeout, max_turns=max_turns,
            max_chars=max_chars, settings=settings,
        )
        if session is None:
            return 1
        client, conversation, generation = session
        conversation.text_entry_apps = text_entry_apps
        speaker = _open_speaker(speak=speak, tts_engine=tts_engine, voice=voice,
                                voice_model_dir=voice_model_dir, speech_rate=speech_rate)
        _help(voice=False)
        while True:
            try:
                text = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                conversation.action_state.clear()
                print("\nChat ended.")
                return 0
            command = _control_command(text, conversation, voice=False)
            if command == "quit":
                return 0
            if command == "continue" or not text:
                continue
            _respond(
                client, conversation, text, model=model,
                settings=generation, diagnostics=diagnostics, speaker=speaker,
            )
    except KeyboardInterrupt:
        print("\nChat ended.")
        return 0
    finally:
        _close_speaker(speaker)


def _respond_or_launch(
    client: OllamaClient,
    conversation: Conversation,
    text: str,
    *,
    model: str,
    settings: GenerationSettings | None,
    diagnostics: bool,
    action_state: ActionState | None = None,
    intent_timeout: float = 15.0,
    speaker=None,
    wait_for_speech: bool = True,
):
    """Compatibility entry for existing voice front ends; one controller only.

    intent_timeout is retained for older callers, but there is no classifier
    request. The configured Ollama client's timeout governs model interactions.
    Returns the TurnResult (older callers ignore it).
    """
    return _respond_result(
        client,
        conversation,
        text,
        model=model,
        settings=settings if settings is not None else GenerationSettings(),
        diagnostics=diagnostics,
        action_state=action_state,
        speaker=speaker,
        wait_for_speech=wait_for_speech,
    )


def _transcribe_recording(
    recognizer: OfflineRecognizer,
    recording: Recording,
    *,
    capture_seconds: float,
    gain_db: float,
    language: str | None,
    hotwords: str | None,
    diagnostics: bool,
    text_transform: Callable[[str], str] | None = None,
) -> Transcript:
    """Shared manual/hands-free recognition and capture diagnostics."""
    started = perf_counter()
    options = {"gain_db": gain_db, "language": language}
    if hotwords is not None:
        options["hotwords"] = hotwords
    transcript = recognizer.transcribe(recording.pcm, recording.sample_rate, **options)
    elapsed = perf_counter() - started
    if text_transform is not None:
        transcript = replace(transcript, text=text_transform(transcript.text))
    print(f"Recognized text: {transcript.text or '(empty)'}")
    if diagnostics:
        if recording.device_info is not None:
            name = " ".join(recording.device_info.name.split())
            print(f"Input {recording.device_info.index}: {name} ({recording.device_info.host_api})")
        print(
            f"Audio capture: {capture_seconds:.3f}s wall; {recording.duration:.3f}s audio at {recording.sample_rate} Hz | "
            f"Peak {recording.levels.peak_dbfs:.1f} dBFS | RMS {recording.levels.rms_dbfs:.1f} dBFS"
        )
        print(f"Speech transcription: {elapsed:.3f}s | VAD-retained audio: {transcript.speech_seconds:.3f}s")
    return transcript


def run_voice_chat(
    *,
    model: str = DEFAULT_CHAT_MODEL,
    settings: GenerationSettings | None = None,
    timeout: float = 120.0,
    max_turns: int = 8,
    max_chars: int = 12000,
    speech_model_dir: Path | None = None,
    device: int | None = None,
    sample_rate: int | None = None,
    gain_db: float = 0.0,
    language: str | None = None,
    hotwords: str | None = None,
    diagnostics: bool = False,
    text_entry_apps: tuple[str, ...] = (),
    speak: bool = False,
    tts_engine: str = "kokoro",
    voice: str | None = None,
    voice_model_dir: Path | None = None,
    speech_rate: float = 1.0,
) -> int:
    """Load speech once, then capture and recognize each deliberate Enter turn."""
    speaker = None
    try:
        session = _start(
            model=model, timeout=timeout, max_turns=max_turns,
            max_chars=max_chars, settings=settings,
        )
        if session is None:
            return 1
        client, conversation, generation = session
        conversation.text_entry_apps = text_entry_apps
        started = perf_counter()
        try:
            recognizer = OfflineRecognizer() if speech_model_dir is None else OfflineRecognizer(speech_model_dir)
        except SpeechError as exc:
            print(f"Speech setup error: {exc}", file=sys.stderr)
            return 1
        load_seconds = perf_counter() - started
        if diagnostics:
            print(f"Speech model loading: {load_seconds:.3f}s (once per session; CPU INT8)")
        speaker = _open_speaker(speak=speak, tts_engine=tts_engine, voice=voice,
                                voice_model_dir=voice_model_dir, speech_rate=speech_rate)
        _help(voice=True)
        action_state = conversation.action_state
        while True:
            try:
                text = input("Press Enter to record (/help, /clear, /quit): ").strip()
            except (EOFError, KeyboardInterrupt):
                action_state.clear()
                print("\nVoice chat ended.")
                return 0
            command = _control_command(text, conversation, voice=True, action_state=action_state)
            if command == "quit":
                return 0
            if command == "continue":
                continue

            if text:
                action_state.clear()
                print("Press Enter to record, or type /help, /clear, or /quit.")
                continue
            if speaker is not None:
                speaker.stop()  # Never record Aqua's own voice.
            try:
                print(f"Recording {RECORD_SECONDS} seconds. Speak now. Ctrl+C cancels this turn.", flush=True)
                capture_started = perf_counter()
                recording = record_test(device=device, sample_rate=sample_rate)
                capture_seconds = perf_counter() - capture_started
                transcript = _transcribe_recording(
                    recognizer, recording, capture_seconds=capture_seconds,
                    gain_db=gain_db, language=language, hotwords=hotwords,
                    diagnostics=diagnostics,
                )
                reason = transcript_rejection_reason(recording, transcript)
                if reason is not None:
                    action_state.clear()
                    print(f"Skipped: {reason}")
                    continue
            except (AudioError, SpeechError) as exc:
                action_state.clear()
                print(f"Voice turn error: {exc}", file=sys.stderr)
                continue
            except KeyboardInterrupt:
                action_state.clear()
                print("\nVoice turn cancelled; no message was sent.")
                continue

            _respond_or_launch(
                client,
                conversation,
                transcript.text,
                model=model,
                settings=generation,
                diagnostics=diagnostics,
                action_state=action_state,
                intent_timeout=min(timeout, 15.0),
                speaker=speaker,
            )

    except KeyboardInterrupt:
        print("\nVoice chat ended.")
        return 0
    finally:
        _close_speaker(speaker)
