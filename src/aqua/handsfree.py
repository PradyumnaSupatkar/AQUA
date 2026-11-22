"""Wake-gated, bounded audio turns feeding Aqua's existing voice/chat path."""

from collections import deque
from dataclasses import dataclass, replace
from math import ceil, isfinite
from pathlib import Path
import re
import sys
from time import perf_counter

from aqua import chat
from aqua.actions.tools import ActionState
from aqua.audio.backend import AudioError
from aqua.audio.console import ConsoleCommands
from aqua.audio.levels import measure_levels
from aqua.audio.recording import Recording
from aqua.audio.streaming import FRAME_SAMPLES, SAMPLE_RATE, MicrophoneStream
from aqua.audio.vad import StreamingVad, UtteranceCollector, UtteranceConfig
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings
from aqua.speech.models import SpeechError
from aqua.speech.recognizer import OfflineRecognizer
from aqua.speech.reliability import transcript_rejection_reason
from aqua.wake.keyword import SherpaWakeDetector
from aqua.wake.models import WakeError

# After Aqua asks a question, listen this long for an answer without the wake
# word. The window opens only once playback has fully finished plus a short
# tail, counted in microphone frames so Aqua never hears its own last syllable.
FOLLOW_UP_SECONDS = 3.0
FOLLOW_UP_TAIL_SECONDS = .25
FOLLOW_UP_TAIL_FRAMES = ceil(FOLLOW_UP_TAIL_SECONDS * SAMPLE_RATE / FRAME_SAMPLES)
# Spoken commands arrive minutes apart; keep the model loaded between them.
HANDSFREE_KEEP_ALIVE = "30m"


def command_text(text: str) -> str:
    """Remove only a leading fixed wake greeting, never interpret an action."""
    return re.sub(r"^(?:hey[\s,]+)?aqua\b[\s,.!?:;-]*", "", text.strip(), count=1, flags=re.IGNORECASE).strip()


@dataclass(frozen=True, slots=True)
class FrontendEvent:
    status: str
    samples: object | None = None
    speech_seconds: float = 0.0
    duration: float = 0.0
    detector_seconds: float = 0.0
    follow_up: bool = False


class HandsfreeFrontend:
    """Only acoustic wake events can arm the post-wake speech collector.

    The one exception is arm_follow_up(), which the session calls only after
    Aqua asked a question and its spoken reply has completely finished.
    """

    def __init__(self, detector, vad, config: UtteranceConfig, *, cooldown: float = 2.0):
        if isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)) or not isfinite(cooldown) or not 0 <= cooldown <= 30:
            raise ValueError("Wake cooldown must be finite and between 0 and 30 seconds.")
        self.detector, self.vad, self.config = detector, vad, config
        self.cooldown = cooldown
        self._next_wake = float("-inf")
        self._pre_roll = deque(maxlen=ceil(config.pre_roll * SAMPLE_RATE / FRAME_SAMPLES))
        self.collector = None
        self.follow_up = False

    @property
    def active(self) -> bool:
        return self.collector is not None

    def reset(self) -> None:
        self.collector = None
        self.follow_up = False
        self._pre_roll.clear()
        self.detector.reset()
        self.vad.reset()

    def arm_follow_up(self, seconds: float) -> None:
        """Collect an answer without the wake word, with no earlier audio as context."""
        import numpy as np

        # Keep the window inside the utterance cap, so silence ends as
        # "no_speech" (consent kept) rather than "too_long" (turn discarded).
        window = max(.5, min(seconds, self.config.max_utterance - .1))
        self.collector = UtteranceCollector(replace(self.config, initial_timeout=window))
        self.collector.begin(np.empty(0, dtype=np.float32))
        self.follow_up = True
        self._pre_roll.clear()
        self.vad.reset()

    def feed(self, frame, *, now: float, keep_context: bool = True) -> FrontendEvent | None:
        """``keep_context=False`` (used while Aqua is speaking) means a wake
        starts a capture without pre-roll, so no playback audio reaches Whisper."""
        import numpy as np

        if self.collector is not None:
            event = self.collector.push(frame, self.vad.probability(frame))
            if event is None:
                return None
            follow_up, self.collector, self.follow_up = self.follow_up, None, False
            return FrontendEvent(event.status, event.samples, event.speech_seconds, event.duration,
                                 follow_up=follow_up)
        self._pre_roll.append(frame.copy())
        started = perf_counter()
        detection = self.detector.feed(frame)
        elapsed = perf_counter() - started
        if detection is None or now < self._next_wake:
            return None
        if detection.keyword.replace("_", " ").casefold() != "hey aqua":
            raise WakeError("The wake detector returned an unexpected keyword; no turn was activated.")
        self._next_wake = now + self.cooldown
        self.collector = UtteranceCollector(self.config)
        context = (np.concatenate(tuple(self._pre_roll)) if self._pre_roll and keep_context
                   else np.empty(0, dtype=np.float32))
        self.collector.begin(context)
        self._pre_roll.clear()
        self.vad.reset()
        return FrontendEvent("wake", detector_seconds=elapsed)


def _recording(samples, device_info) -> Recording:
    import numpy as np

    pcm = np.rint(np.clip(samples, -1, 1) * 32768).clip(-32768, 32767).astype(np.int16).tobytes()
    return Recording(pcm, SAMPLE_RATE, measure_levels(pcm), 0, device_info)


def _help() -> None:
    print('Say "Hey Aqua", followed by a command or question. No Enter is needed.')
    print("Type /clear, /help, or /quit then Enter; Ctrl+C stops speech, cancels a capture, or exits while idle.")


def _asked_question(result, state: ActionState, agent=None) -> bool:
    """A follow-up answer is expected only after a question or pending confirmation."""
    from aqua.voice.text import is_question

    if state.pending_app is not None or (agent is not None and agent.awaiting_answer):
        return True
    text = getattr(result, "text", None)
    return isinstance(text, str) and is_question(text)


def run_handsfree(
    *, model: str = DEFAULT_CHAT_MODEL, settings: GenerationSettings | None = None,
    timeout: float = 120.0, max_turns: int = 8, max_chars: int = 12000,
    speech_model_dir: Path | None = None, device: int | None = None,
    sample_rate: int | None = None, gain_db: float = 0.0, language: str | None = None,
    hotwords: str | None = None, diagnostics: bool = False,
    wake_model_dir: Path | None = None, wake_threshold: float = .25,
    wake_score: float = 1.0, wake_cooldown: float = 2.0,
    end_silence: float = .8, initial_timeout: float = 5.0,
    max_utterance: float = 15.0, pre_roll: float = .5, vad_threshold: float = .5,
    text_entry_apps: tuple[str, ...] = (),
    speak: bool = False, tts_engine: str = "kokoro", voice: str | None = None,
    voice_model_dir: Path | None = None, speech_rate: float = 1.0,
    follow_up: float = FOLLOW_UP_SECONDS,
    keep_alive: str = HANDSFREE_KEEP_ALIVE,
    tts_threads: int | None = None,
    llm_threads: int | None = None,
    engine: str = "legacy",
    lookup: bool = True,
) -> int:
    """Load detectors once, use one mic, and never send idle audio to Whisper/Qwen.

    While a reply is being spoken, the microphone feeds only the wake
    detector. A wake stops playback and starts a capture without pre-roll, so
    audio heard during playback never reaches Whisper.
    """
    state = ActionState()
    speaker = None
    agent = None  # The new action core, created with the Ollama client (--engine new).

    def clear_consent() -> None:
        state.clear()
        if agent is not None:
            agent.cancel_pending()
    try:
        if isinstance(follow_up, bool) or not isinstance(follow_up, (int, float)) or not isfinite(follow_up) \
                or not (follow_up == 0 or .5 <= follow_up <= 10):
            raise ValueError("The follow-up window must be 0 (off) or between 0.5 and 10 seconds.")
        config = UtteranceConfig(
            end_silence=end_silence, initial_timeout=initial_timeout,
            max_utterance=max_utterance, pre_roll=pre_roll, speech_threshold=vad_threshold,
        )
        generation = settings if settings is not None else GenerationSettings()
        conversation = Conversation(max_turns=max_turns, max_chars=max_chars)
        conversation.bind_action_state(state)
        conversation.text_entry_apps = text_entry_apps
        detector = SherpaWakeDetector(wake_model_dir, threshold=wake_threshold, score=wake_score)
        frontend = HandsfreeFrontend(detector, StreamingVad(), config, cooldown=wake_cooldown)
        started = perf_counter()
        recognizer = OfflineRecognizer() if speech_model_dir is None else OfflineRecognizer(speech_model_dir)
        if diagnostics:
            print(f"Speech model loading: {perf_counter() - started:.3f}s (once per session; CPU INT8)")
        speaker = chat._open_speaker(speak=speak, tts_engine=tts_engine, voice=voice,
                                     voice_model_dir=voice_model_dir, speech_rate=speech_rate,
                                     tts_threads=tts_threads)
        commands = ConsoleCommands()
        # No Ollama request, including metadata, is made until a valid awakened
        # command survives recognition. A silent idle session stays acoustic-only.
        client = None
        wake_started = 0.0
        vad_started = False
        follow_up_due = False       # Aqua asked a question; open the window after playback.
        tail_frames = 0             # Frames still to skip after playback before opening it.
        report_speech = False       # Print speech timings once playback ends (--diagnostics).

        def speaking() -> bool:
            return speaker is not None and speaker.is_speaking

        def silence(reason: str) -> None:
            nonlocal follow_up_due, report_speech
            follow_up_due = report_speech = False
            if speaker is not None and speaker.is_speaking:
                speaker.stop()
                print(reason, flush=True)

        with MicrophoneStream(device=device, sample_rate=sample_rate) as microphone:
            _help()
            print('Listening for "Hey Aqua"...', flush=True)
            while True:
                try:
                    control = commands.poll()
                    if control is not None:
                        clear_consent()
                        silence("Speech stopped.")
                        microphone.pause()
                        frontend.reset()
                        if control == "/help":
                            _help()
                            command = "continue"
                        else:
                            command = chat._control_command(control, conversation, voice=False, action_state=state)
                        if command == "quit":
                            return 0
                        if command is None:
                            print("Use /clear, /help, or /quit; spoken turns require the wake phrase.")
                        microphone.resume()
                        print('Listening for "Hey Aqua"...', flush=True)
                        continue
                    frame = microphone.read_frame(timeout=.1)
                    if frame is None:
                        continue
                    playing = speaking()
                    if report_speech and not playing:
                        report_speech = False
                        chat._speech_diagnostics(speaker, wait=False)
                    if agent is not None and not playing and not frontend.active:
                        reminder = agent.countdown_announcement()
                        if reminder is not None:
                            # Spoken restart/shutdown countdown; the answer window then hears "cancel".
                            print(f"Aqua: {reminder}", flush=True)
                            if speaker is not None:
                                speaker.feed(reminder)
                                speaker.finish_turn()
                            if follow_up:
                                follow_up_due, tail_frames = True, FOLLOW_UP_TAIL_FRAMES
                            continue
                    if follow_up_due and not playing and not frontend.active:
                        if tail_frames > 0:
                            tail_frames -= 1
                        else:
                            follow_up_due = False
                            frontend.arm_follow_up(follow_up)
                            wake_started, vad_started = perf_counter(), False
                            print("Listening for your answer...", flush=True)
                    event = frontend.feed(frame, now=perf_counter(), keep_context=not playing)
                except KeyboardInterrupt:
                    if speaking() and not frontend.active:
                        # Ctrl+C while Aqua talks silences it; pending consent is untouched.
                        silence("\nSpeech stopped.")
                        print('Listening for "Hey Aqua"...', flush=True)
                        continue
                    clear_consent()
                    follow_up_due = False
                    if not frontend.active:
                        print("\nHands-free chat ended.")
                        return 0
                    print("\nVoice turn cancelled; no message was sent.")
                    microphone.pause()
                    frontend.reset()
                    microphone.resume()
                    print('Listening for "Hey Aqua"...', flush=True)
                    continue
                if event is None:
                    if diagnostics and frontend.active and not vad_started and frontend.collector.speech_seconds >= config.min_speech:
                        vad_started = True
                        print(f"VAD: speech started; {perf_counter() - wake_started:.3f}s after wake detection")
                    continue
                if event.status == "wake":
                    silence("Speech stopped.")
                    wake_started = perf_counter()
                    vad_started = False
                    print("Wake word detected.\nListening...", flush=True)
                    if diagnostics:
                        print(f"Wake detector: {event.detector_seconds:.4f}s on triggering frame; CPU; cooldown {wake_cooldown:g}s")
                    continue
                if event.follow_up and event.status == "no_speech":
                    # Silence after a question is not a cancelled turn; consent stays as it was.
                    print('No answer heard. Listening for "Hey Aqua"...', flush=True)
                    continue
                capture_seconds = perf_counter() - wake_started
                microphone.pause()  # Never queue speech or detect wakes during processing.
                try:
                    if diagnostics:
                        print(f"VAD: {event.status}; capture {event.duration:.3f}s; voiced after wake {event.speech_seconds:.3f}s; silence limit {end_silence:g}s")
                    if event.status != "complete":
                        clear_consent()
                        reason = "no meaningful speech after wake" if event.status == "no_speech" else "maximum utterance length reached; entire turn discarded"
                        print(f"Skipped: {reason}.")
                        continue
                    print("Speech ended.", flush=True)
                    recording = _recording(event.samples, microphone.device_info)
                    transcript = chat._transcribe_recording(
                        recognizer, recording, capture_seconds=capture_seconds,
                        gain_db=gain_db, language=language, hotwords=hotwords,
                        diagnostics=diagnostics, text_transform=command_text,
                    )
                    reason = transcript_rejection_reason(recording, transcript)
                    if reason is not None:
                        clear_consent()
                        print(f"Skipped: {reason}.")
                        continue
                    if client is None:
                        session = chat._start(
                            model=model, timeout=timeout, max_turns=max_turns,
                            max_chars=max_chars, settings=generation, keep_alive=keep_alive,
                            llm_threads=llm_threads,
                        )
                        if session is None:
                            clear_consent()
                            continue
                        client, conversation, generation = session
                        conversation.bind_action_state(state)
                        conversation.text_entry_apps = text_entry_apps
                        if engine == "new":
                            agent = chat._start_agent(client, model=model, settings=generation,
                                                      max_turns=max_turns, max_chars=max_chars, lookup=lookup)
                            if agent is None:
                                return 1
                            conversation = agent.conversation
                    if agent is not None:
                        result = chat._respond_agent(agent, transcript.text, diagnostics=diagnostics,
                                                     speaker=speaker, wait_for_speech=False)
                    else:
                        result = chat._respond_or_launch(
                            client, conversation, transcript.text, model=model, settings=generation,
                            diagnostics=diagnostics, action_state=state, intent_timeout=min(timeout, 15.0),
                            speaker=speaker, wait_for_speech=False,
                        )
                    # Playback may continue; the loop resumes wake-only listening meanwhile.
                    report_speech = diagnostics and speaker is not None
                    if follow_up and _asked_question(result, state, agent):
                        follow_up_due, tail_frames = True, FOLLOW_UP_TAIL_FRAMES
                except (SpeechError, KeyboardInterrupt) as exc:
                    clear_consent()
                    silence("Speech stopped.")
                    message = "Voice turn cancelled" if isinstance(exc, KeyboardInterrupt) else f"Voice turn error: {exc}"
                    print(f"\n{message}; no new action was authorized.", file=sys.stderr)
                finally:
                    if diagnostics:
                        print(f"Total interaction since wake detection: {perf_counter() - wake_started:.3f}s")
                    frontend.reset()
                    microphone.resume()
                    print('Listening for "Hey Aqua"...', flush=True)
    except (WakeError, AudioError, SpeechError, ValueError) as exc:
        clear_consent()
        print(f"Hands-free error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        clear_consent()
        print("\nHands-free chat ended.")
        return 0
    finally:
        chat._close_speaker(speaker)

