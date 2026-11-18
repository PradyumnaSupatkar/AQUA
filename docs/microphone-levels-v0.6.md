# Aqua V0.6 microphone sensitivity investigation

The unfinished diagnostic hooks and tool were preserved and completed. No Qwen code, application allowlist, model files, Windows settings, or production gain/threshold defaults were changed. All microphone audio remained in memory. No dependencies or models were downloaded.

## Established findings

The reported -41.9 dBFS peak / -63.4 dBFS RMS was measured from native 44,100 Hz PCM, upstream of normalization and resampling. The initial code audit and synthetic passband tests found no duplicated normalization or unexpected attenuation. A peak of -41.9 dBFS is about 263 int16 counts, or 0.804% of digital full scale.

The current controlled MME capture, after the user raised Windows input from 9 to 100, was substantially stronger. The old quiet level was not reproduced on MME. The volume increase is a plausible explanation for the historical change, but there is no contemporaneous endpoint/gain record for the old capture; that exact historical cause cannot be proven.

Read-only Windows Core Audio inspection confirmed:

- Console, multimedia, and communications defaults share the same Realtek capture endpoint.
- Master volume is 100%, mute is off, and both channels are 100%.
- The endpoint reports +30 dB, range -17 to +30 dB. This is endpoint gain metadata, not dBFS or a proposal to add software gain.
- PortAudio WASAPI input 6 maps directly to the default endpoint's exact ID through `PaWasapi_GetIMMDevice/GetId`.
- MME input 0 matches the sole WinMM capture device, whose function-instance query returns that exact same ID.
- DirectSound and WDM identities remain explicitly unknown rather than guessed from similar names.

Thus, the Windows input control is adjusting the microphone endpoint Aqua actually uses. A wrong endpoint is not the explanation in this session.

## Actual same-capture stage measurements

November 13, 2026. Two separate eight-second captures used two scheduled quiet seconds and six scheduled speech seconds. The user confirmed normal-volume speech during both. The samples are sequential, not acoustically identical, so backend causality is not established by the level difference alone.

| Stage | MME, native 44,100 Hz: peak / RMS dBFS | WASAPI, native 48,000 Hz: peak / RMS dBFS |
| --- | ---: | ---: |
| Native mono int16 PCM | -0.00 / -21.74 | -0.06 / -39.13 |
| Native normalized float reference | -0.00 / -21.74 | -0.06 / -39.13 |
| Resampled float before clipping/framing | -0.03 / -21.75 | +0.01 / -39.14 |
| Actual 16 kHz wake-input frames | -0.03 / -21.75 | 0.00 / -39.14 |
| Simulated Whisper int16 round-trip, unity gain | -0.03 / -21.75 | 0.00 / -39.14 |

The native-to-wake RMS change was **-0.002 dB for MME** and **-0.014 dB for WASAPI**. Aqua is not responsible for tens of decibels of attenuation. Float conversion occurs once: either `/32768` at native 16 kHz, or PyAV's int16-to-float conversion while resampling. The wake adapter casts/validates float32 without another division. The conversion back to PCM for Whisper is an inverse scale with expected quantization, not a second normalization.

MME native peak was 32,753/32,768 counts (99.954% full scale), with no exact native clipping rails. Its scheduled speech RMS was -20.51 dBFS and louder speech-frame RMS was -15.42 dBFS; quiet RMS was -40.45 dBFS. It has no safe need for additional gain.

WASAPI scheduled speech RMS was -38.65 dBFS, louder speech-frame RMS -45.13 dBFS, and quiet RMS -40.97 dBFS. Its isolated near-full-scale peak did not indicate sustained strong speech. One resampled sample slightly exceeded full scale (+0.01 dBFS) and was clipped by the existing safety clamp; this small filter overshoot does not explain the low sustained level. Scheduled speech/noise RMS contrast was only 2.3 dB, so the gain evaluator refused a boost.

The substantially different native speech-frame levels warrant keeping the currently stronger MME path while investigating Windows/driver processing or channel-mode differences. They do not justify assuming WASAPI is better. No Windows/device processing settings were changed. A live stereo sample has not yet been taken; production Aqua requests one channel and never averages channels itself, so any upstream driver downmix remains a separate, unproven possibility.

## Wake-model audit and gain evaluation

The installed sherpa API/examples accept normalized float32. Aqua's keyword score 1.0, threshold 0.25, and trailing-blank setting 1 match its installed defaults. Reset occurs only after an actual keyword event; it is not happening on every audio frame. No wake-input scaling or reset bug was established.

An actual-model synthetic amplitude sweep used the same in-memory Windows voice fixture. It detected Hey Aqua at -24 dBFS peak, but not at -36, -41.9, -48, or -54 dBFS, with or without a -65 dBFS noise fixture. For the -41.9 dBFS case, +6/+12 dB, threshold 0.15/0.10, or score 1.5 did not establish a fix. Three ordinary-speech negatives and 30 seconds of synthetic noise had no events in those limited checks. These observations demonstrate model level sensitivity on that fixture, not a universal human detection threshold or calibrated false-alarm rate.

No boost or automatic normalization was implemented. The calibration tool only calculates a hypothetical candidate when the scheduled speech/noise contrast is at least 12 dB, the noise baseline is measurable, there is no detected clipping or excessive DC, and peak headroom permits it. The candidate is capped at +12 dB and a predicted -3 dBFS peak ceiling. This is report-only and is not evidence that the model would detect better. Gain raises noise with speech and cannot restore lost SNR; per-frame peak normalization would be particularly unsafe.

`--gain-db` remains recognition-only, after wake activation. Increasing it cannot fix wake detection.

## Repeatable calibration

```powershell
Set-Location D:\Aqua
.\.venv\Scripts\python.exe -m aqua mic-levels --compare-backends --check-wake
```

This single command verifies exact endpoint mapping, prints read-only volume metadata, and takes separate MME/WASAPI samples at their native rates. For each sample, stay quiet for two seconds and then repeat Hey Aqua at normal volume/distance for six seconds. It counts local keyword events only; it cannot run Whisper, Qwen, or application actions. A missing wake model causes an error without downloading. Scheduled quiet/speech labels are instructions, not automatically verified ground truth.

For a level-only check that loads no model, omit the optional flags. For the current stronger MME input, use `mic-levels --device 0 --check-wake`. Re-enumerate with `aqua devices` if indices change. Tuning `--wake-score`/`--wake-threshold` on this diagnostic affects its temporary detector only, not production defaults, and requires `--check-wake`.

An optional `mic-levels --device 6 --stereo` sample reports each channel, correlation, and the attenuation of a hypothetical `(L+R)/2` mix. It opens one separate stereo stream, not competing streams, and never uses that mix in normal operation. It cannot be combined with backend comparison or wake inference.

Repeat a negative trial using ordinary speech without Hey Aqua. Do not choose a more permissive model setting based only on positive hits. The final live wake-count and stereo trials were deferred at the user's request, so normal-volume human wake reliability remains unverified in this investigation.

## Recommended configuration and remaining uncertainty

Retain the existing score **1.0**, threshold **0.25**, and unity wake gain. Prefer the current **MME Realtek input 0** based on these measurements; do not automatically switch backend or amplify a signal already near full scale. The equivalent existing hands-free configuration is:

```powershell
.\.venv\Scripts\python.exe -m aqua handsfree --device 0 --language en --wake-score 1 --wake-threshold 0.25 --diagnostics
```

The following remain unknown: the exact setting/conditions of the old quiet recording, the reason for the native MME/WASAPI level difference, any upstream stereo cancellation, and human positive/negative wake performance under current input volume. The evidence rules out Aqua's conversion/resampling as the cause of the reported tens-of-decibels loss. It does not establish a complete fix for all remaining keyword misses.

## Implementation and validation

- Preserved `on_native_audio` and `on_resampled_audio` in `audio/streaming.py`; observers run only in the consumer thread and receive immutable bytes or copied arrays. Observer exceptions disable the failing hook, retain bounded redacted error metadata, and preserve audio. Ctrl+C/SystemExit and real capture/resampler errors still propagate. Disabled observers remain disabled after resume.
- `audio/level_probe.py` measures bounded scalar/frame summaries without keeping waveforms. Any observer error invalidates the report. Normal hands-free mode has no observers enabled. A deliberately slow arbitrary callback can still stall a consumer; the built-in bounded observers are trusted and never run in the PortAudio callback.
- `audio/windows_endpoint.py` uses read-only public endpoint/volume/identity APIs, no setters or registry changes. Unknown mappings remain unknown; an explicit unmapped input is never silently replaced during backend comparison.
- `cli.py` exposes the explicit diagnostic and validates its bounded settings.
- Added/extended `test_streaming.py`, `test_level_probe.py`, `test_level_probe_cli.py`, and `test_windows_endpoint.py`. No existing assertions were removed.

**509 automated tests passed in 2.110 seconds**, with network connections and unexpected process launches blocked. The affected tests passed first. No Qwen/application safety code or Ollama/Whisper model storage was modified. Documentation changes are limited to this report and the README calibration instructions.
