"""Opt-in, in-memory signal measurements; never adjust or normalize capture."""

from math import isfinite, log10, sqrt
import sys
from time import sleep

from .backend import AudioError, get_backend
from .devices import resolve_input_device
from .streaming import SAMPLE_RATE, MicrophoneStream
from .windows_endpoint import inspect_capture_endpoints


def _db(value: float) -> float:
    return 20 * log10(value) if value > 0 else float("-inf")


class SignalStats:
    """Bounded scalar/frame summaries; no waveform is retained."""

    def __init__(self, sample_rate: int):
        self.sample_rate = sample_rate
        self.count = self.clipped = 0
        self.peak = self.total = self.energy = 0.0
        self._window = max(1, round(.02 * sample_rate))
        self._pending_count = 0
        self._pending_energy = 0.0
        self._window_rms = []

    def update(self, samples, *, rails=None):
        import numpy as np

        audio = np.asarray(samples, dtype=np.float64)
        if audio.ndim != 1 or not np.isfinite(audio).all():
            raise ValueError("Signal measurements require finite mono audio.")
        if not audio.size:
            return
        self.count += audio.size
        self.total += float(np.sum(audio))
        self.energy += float(np.dot(audio, audio))
        self.peak = max(self.peak, float(np.max(np.abs(audio))))
        self.clipped += int(np.count_nonzero(np.abs(audio) >= 1)) if rails is None else int(np.count_nonzero(rails))
        offset = 0
        while offset < audio.size:
            take = min(self._window - self._pending_count, audio.size - offset)
            part = audio[offset:offset + take]
            self._pending_energy += float(np.dot(part, part))
            self._pending_count += take
            offset += take
            if self._pending_count == self._window:
                self._window_rms.append(sqrt(self._pending_energy / self._window))
                self._pending_count = 0
                self._pending_energy = 0.0

    @property
    def rms(self) -> float:
        return sqrt(self.energy / self.count) if self.count else 0.0

    @property
    def dc(self) -> float:
        return self.total / self.count if self.count else 0.0

    @property
    def p90_rms(self) -> float:
        import numpy as np

        values = self._window_rms
        if not values:
            return self.rms
        return float(np.percentile(values, 90))


class StageStats:
    """Split the same scheduled quiet/speech windows at each sample rate."""

    def __init__(self, sample_rate: int, seconds: float, quiet_seconds: float):
        self.rate = sample_rate
        self.limit = round(seconds * sample_rate)
        self.quiet_limit = round(quiet_seconds * sample_rate)
        self.all = SignalStats(sample_rate)
        self.quiet = SignalStats(sample_rate)
        self.speech = SignalStats(sample_rate)
        self.raw_peak_count = 0

    def update(self, samples, *, raw_pcm: bool = False):
        import numpy as np

        source = np.asarray(samples)
        take = max(0, min(source.size, self.limit - self.all.count))
        source = source[:take]
        if not take:
            return
        if raw_pcm:
            self.raw_peak_count = max(self.raw_peak_count, int(np.max(np.abs(source.astype(np.int32)))))
            rails = (source == -32768) | (source == 32767)
            audio = source.astype(np.float64) / 32768
        else:
            rails = None
            audio = source
        quiet_count = max(0, min(take, self.quiet_limit - self.all.count))
        self.all.update(audio, rails=rails)
        self.quiet.update(audio[:quiet_count], rails=None if rails is None else rails[:quiet_count])
        self.speech.update(audio[quiet_count:], rails=None if rails is None else rails[quiet_count:])


def gain_advice(stage: StageStats) -> str:
    """Evaluate a conservative hypothetical boost; never alter any samples."""
    quiet, speech = stage.quiet, stage.speech
    if not quiet.count or not speech.count or quiet.rms <= 1 / 32768:
        return "refused: quiet baseline is missing or at/below one int16 count; noise cannot be estimated"
    snr_proxy = _db(speech.rms) - _db(quiet.rms)
    if speech.clipped:
        return "refused: the speech window already reached a digital clipping rail"
    if speech.peak <= 0 or snr_proxy < 12:
        return f"refused: scheduled speech/noise RMS contrast is {snr_proxy:.1f} dB; at least 12 dB is required"
    if abs(speech.dc) > .25 * speech.rms:
        return "refused: a substantial DC component makes the speech/noise estimate unreliable"
    target_gain = -25 - _db(speech.p90_rms)
    headroom_gain = -3 - _db(speech.peak)
    candidate = min(12.0, target_gain, headroom_gain)
    if candidate <= 0:
        return "no positive boost recommended: target already met or insufficient -3 dBFS peak headroom"
    return (
        f"hypothetical upper bound +{candidate:.1f} dB (not applied); cap 12 dB, "
        f"target louder-frame RMS -25 dBFS, predicted peak {_db(speech.peak) + candidate:.1f} dBFS; "
        f"speech/noise contrast {snr_proxy:.1f} dB. Gain does not improve SNR or prove wake accuracy"
    )


def _show(name: str, stage: StageStats):
    total = stage.all
    print(
        f"{name}: {total.count / stage.rate:.3f}s at {stage.rate} Hz | "
        f"peak {_db(total.peak):.2f} dBFS | RMS {_db(total.rms):.2f} dBFS | "
        f"20ms-p90 RMS {_db(total.p90_rms):.2f} dBFS | "
        f"clip/over-range {total.clipped}/{total.count} | DC {total.dc:+.7f} FS"
    )
    print(f"  Scheduled quiet RMS {_db(stage.quiet.rms):.2f} dBFS; speech-window RMS {_db(stage.speech.rms):.2f} dBFS; louder speech frames {_db(stage.speech.p90_rms):.2f} dBFS")


def _instructions(seconds, quiet_seconds, *, check_wake=False):
    print(f"Read-only level probe: {seconds:g}s in memory. Stay quiet for the first {quiet_seconds:g}s.", flush=True)
    activity = "only the local wake model runs; no transcription, Qwen, or actions." if check_wake else "no models or actions run."
    print(f"Then repeat 'Hey Aqua' at your normal distance and volume until capture finishes. No audio is saved; {activity}", flush=True)


def _show_endpoint(metadata, selected_index):
    default = next((row for row in metadata.get("defaults", ()) if row["role"] == "console"), None)
    if default is None:
        print("Windows endpoint identity/volume metadata unavailable; capture measurements are still valid.")
        return
    print(
        f"Windows default capture: {default['volume_percent']:g}% volume; muted={default['muted']}; "
        f"channel volumes={default['channel_percents']}; endpoint level {default['level_db']:+g} dB (not dBFS)."
    )
    selected = next((row for row in metadata.get("devices", ()) if row["index"] == selected_index), None)
    if selected and selected.get("endpoint_id"):
        print(f"Selected endpoint: {selected['endpoint_id']}; matches Windows default={selected['matches_console_default']} ({selected['mapping']}).")
    else:
        print("Selected input's exact Windows endpoint mapping is unknown; no name-based identity is assumed.")


def _wake_detector(*, score, threshold):
    try:
        from aqua.wake.keyword import SherpaWakeDetector
        return SherpaWakeDetector(score=score, threshold=threshold)
    except Exception as exc:
        raise AudioError("Could not load the installed local wake model for calibration; no download was attempted.") from exc


def _mono_probe(*, device, sample_rate, seconds, quiet_seconds, metadata=None, check_wake=False, wake_score=1.0, wake_threshold=.25):
    import numpy as np

    stages = {}

    def native(pcm, rate):
        values = np.frombuffer(pcm, dtype=np.int16)
        if "native" not in stages:
            stages["native"] = StageStats(rate, seconds, quiet_seconds)
            stages["native_float"] = StageStats(rate, seconds, quiet_seconds)
        stages["native"].update(values, raw_pcm=True)
        stages["native_float"].update(values.astype(np.float32) / np.float32(32768))

    def resampled(values, rate):
        if "resampled" not in stages:
            stages["resampled"] = StageStats(rate, seconds, quiet_seconds)
        stages["resampled"].update(values)

    wake = stages["wake"] = StageStats(SAMPLE_RATE, seconds, quiet_seconds)
    roundtrip = stages["roundtrip"] = StageStats(SAMPLE_RATE, seconds, quiet_seconds)
    detector = _wake_detector(score=wake_score, threshold=wake_threshold) if check_wake else None
    hits = {"quiet": 0, "speech": 0}
    announced = False
    with MicrophoneStream(device=device, sample_rate=sample_rate, on_native_audio=native, on_resampled_audio=resampled) as microphone:
        info = microphone.device_info
        print(f"Selected input {info.index}: {info.name} | {info.host_api} | native {microphone.native_sample_rate} Hz; mono int16; unity wake gain.", flush=True)
        _show_endpoint(metadata or {}, info.index)
        if detector is not None:
            print(f"Wake check enabled: exact Hey Aqua; score {wake_score:g}; threshold {wake_threshold:g}; unity gain. Events only; no application route.")
        _instructions(seconds, quiet_seconds, check_wake=check_wake)
        while wake.all.count < wake.limit:
            frame = microphone.read_frame(timeout=.1)
            if frame is None:
                continue
            if detector is not None:
                try:
                    event = detector.feed(frame)
                except Exception as exc:
                    raise AudioError("Local wake calibration failed; no action or settings change occurred.") from exc
                if event is not None:
                    phase = "quiet" if wake.all.count < wake.quiet_limit else "speech"
                    hits[phase] += 1
                    print(f"Wake event at {wake.all.count / SAMPLE_RATE:.3f}s in scheduled {phase} window; no action executed.", flush=True)
            wake.update(frame)
            pcm = np.rint(np.clip(frame, -1, 1) * 32768).clip(-32768, 32767).astype(np.int16)
            roundtrip.update(pcm, raw_pcm=True)
            if not announced and wake.all.count >= wake.quiet_limit:
                print("Speak normally now.", flush=True)
                announced = True
        if getattr(microphone, "diagnostic_errors", ()):
            raise AudioError("A diagnostic observer failed; incomplete measurements were discarded. Normal capture was not modified.")
    for key, label in (
        ("native", "1 Native mono PCM"), ("native_float", "2 Native normalized float reference"),
        ("resampled", "3 Resampled float BEFORE clipping/framing"),
        ("wake", "4 Actual wake-input float AFTER clipping/framing"),
        ("roundtrip", "5 Simulated Whisper int16 round-trip (no recognition gain)"),
    ):
        if key not in stages:
            raise AudioError(f"No samples reached diagnostic stage {key}.")
        _show(label, stages[key])
    native_stage = stages["native"]
    print(f"Native raw peak: {native_stage.raw_peak_count} / 32768 int16 counts ({100 * native_stage.all.peak:.3f}% full scale).")
    pre = stages["resampled"].all.rms
    post = wake.all.rms
    delta = _db(post) - _db(native_stage.all.rms) if post and native_stage.all.rms else None
    pre_delta = _db(post) - _db(pre) if pre and post else None
    print(f"Native-to-wake aggregate RMS change: {delta:+.3f} dB." if delta is not None else "Native-to-wake RMS change unavailable for digital silence.")
    if pre_delta is not None:
        print(f"Preclip-to-wake RMS change: {pre_delta:+.3f} dB (small duration/filter-boundary differences can affect aggregate comparisons).")
    print("Gain evaluation only: " + gain_advice(native_stage) + ".")
    if detector is not None:
        print(f"Wake check events: quiet-window {hits['quiet']}; speech-window {hits['speech']}. Scheduled phases are not verified ground truth; this is not a false-activation-rate estimate.")
    print("Quiet/speech labels describe the requested schedule, not verified speech or calibrated SNR. Raw native levels already include Windows/driver processing.")
    print("No Windows settings, production gain, wake threshold, or audio files were changed.")
    return 0


def _stereo_probe(*, device, sample_rate, seconds, quiet_seconds):
    import numpy as np

    selected = resolve_input_device(device)
    if selected.input_channels < 2:
        raise AudioError("This input does not expose two channels; use the mono diagnostic.")
    rate = sample_rate or round(selected.default_sample_rate)
    backend = get_backend()
    left, right, mean = (StageStats(rate, seconds, quiet_seconds) for _ in range(3))
    cross = 0.0
    stream = None
    try:
        backend.check_input_settings(device=selected.index, channels=2, dtype="int16", samplerate=rate)
        stream = backend.RawInputStream(device=selected.index, channels=2, dtype="int16", samplerate=rate, latency="high")
        stream.start()
        print(f"Stereo diagnostic input {selected.index}: {selected.name} | {selected.host_api} | {rate} Hz.")
        print("This is a separate two-channel driver mode, not Aqua's production mono capture; no competing microphone stream is opened.")
        _instructions(seconds, quiet_seconds)
        announced = False
        while left.all.count < left.limit:
            count = min(max(1, rate // 10), left.limit - left.all.count)
            data, overflow = stream.read(count)
            if overflow or len(data) != count * 4:
                raise AudioError("Stereo input lost or returned incomplete audio; comparison discarded.")
            audio = np.frombuffer(data, dtype=np.int16).reshape(-1, 2)
            first, second = audio[:, 0].astype(np.float64) / 32768, audio[:, 1].astype(np.float64) / 32768
            left.update(audio[:, 0], raw_pcm=True)
            right.update(audio[:, 1], raw_pcm=True)
            mean.update((first + second) / 2)
            cross += float(np.dot(first, second))
            if not announced and left.all.count >= left.quiet_limit:
                print("Speak normally now.", flush=True)
                announced = True
    except (backend.PortAudioError, OSError, ValueError) as exc:
        raise AudioError("Could not capture the separate stereo diagnostic; this channel mode may be unsupported.") from exc
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass  # Cleanup must not mask a capture error or interruption.
    _show("Channel 1", left)
    _show("Channel 2", right)
    _show("Hypothetical (L+R)/2, NOT used by Aqua", mean)
    covariance = cross / left.all.count - left.all.dc * right.all.dc
    variance_left = max(0, left.all.rms ** 2 - left.all.dc ** 2)
    variance_right = max(0, right.all.rms ** 2 - right.all.dc ** 2)
    correlation = covariance / sqrt(variance_left * variance_right) if variance_left and variance_right else None
    attenuation = _db(mean.all.rms) - max(_db(left.all.rms), _db(right.all.rms)) if mean.all.rms else float("-inf")
    print(f"Channel correlation: {correlation:+.4f}." if correlation is not None else "Channel correlation unavailable for constant/silent channels.")
    print(f"Hypothetical mean RMS relative to stronger channel: {attenuation:+.2f} dB. Negative correlation plus substantial loss suggests cancellation if those channels were mixed.")
    print("Aqua's production capture requests ONE channel and does not perform this mix. No gain or settings changed; no audio saved.")
    return 0


def run_level_probe(
    *, device=None, sample_rate=None, seconds=8.0, quiet_seconds=2.0, stereo=False,
    compare_backends=False, check_wake=False, wake_score=1.0, wake_threshold=.25,
) -> int:
    """Opt-in capture and scalar summaries only, without inference or settings writes."""
    try:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not isfinite(seconds) or not 1 <= seconds <= 30:
            raise ValueError("Diagnostic duration must be finite and between 1 and 30 seconds.")
        if isinstance(quiet_seconds, bool) or not isinstance(quiet_seconds, (int, float)) or not isfinite(quiet_seconds) or not 0 <= quiet_seconds < seconds:
            raise ValueError("Quiet baseline must be finite, nonnegative, and shorter than capture.")
        if sample_rate is not None and (type(sample_rate) is not int or not 0 < sample_rate <= 384000):
            raise ValueError("Sample rate must be a positive integer at most 384000 Hz.")
        if stereo and (compare_backends or check_wake):
            raise ValueError("Stereo is a separate channel-only probe; omit --compare-backends and --check-wake.")
        if isinstance(wake_score, bool) or not isinstance(wake_score, (int, float)) or not isfinite(wake_score) or not 0 < wake_score <= 10:
            raise ValueError("Wake score must be finite, greater than zero, and at most ten.")
        if isinstance(wake_threshold, bool) or not isinstance(wake_threshold, (int, float)) or not isfinite(wake_threshold) or not 0 < wake_threshold <= 1:
            raise ValueError("Wake threshold must be finite, greater than zero, and at most one.")
        metadata = inspect_capture_endpoints()
        if stereo:
            return _stereo_probe(device=device, sample_rate=sample_rate, seconds=seconds, quiet_seconds=quiet_seconds)
        options = dict(sample_rate=sample_rate, seconds=seconds, quiet_seconds=quiet_seconds,
                       metadata=metadata, check_wake=check_wake, wake_score=wake_score, wake_threshold=wake_threshold)
        if not compare_backends:
            return _mono_probe(device=device, **options)
        selected = next((row for row in metadata.get("devices", ()) if row["index"] == device), None) if device is not None else None
        default = next((row for row in metadata.get("defaults", ()) if row["role"] == "console"), None)
        if device is not None and (selected is None or not selected.get("endpoint_id")):
            raise ValueError("The explicitly selected input has no exact endpoint mapping; it was not replaced with the default.")
        identity = selected.get("endpoint_id") if selected else default.get("endpoint_id") if default else None
        if not identity:
            raise ValueError("Cannot establish an exact Windows endpoint for backend comparison; use --device for a single probe.")
        inputs = [row for row in metadata.get("devices", ()) if row.get("endpoint_id") == identity and row["host_api"] in ("MME", "Windows WASAPI")]
        if len({row["host_api"] for row in inputs}) < 2:
            raise ValueError("Both MME and WASAPI could not be mapped to this same endpoint; use a single --device probe.")
        for index, row in enumerate(sorted(inputs, key=lambda row: row["host_api"])):
            if index:
                print("Next backend starts in three seconds. Repeat the same normal-volume phrase at the same distance.", flush=True)
                sleep(3)
            print(f"Backend comparison: {row['host_api']} input {row['index']}; separate sequential captures, not simultaneous.", flush=True)
            _mono_probe(device=row["index"], **options)
        print("Backend samples were sequential. Compare sustained speech-frame levels, not isolated peaks; speaker timing/volume can also change results.")
        return 0
    except (AudioError, ValueError, OSError) as exc:
        print(f"Microphone level diagnostic error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nMicrophone level diagnostic cancelled; no audio was saved.", file=sys.stderr)
        return 130
