# agent-v1: after fixes 1-5 (round4) vs baseline

Frozen suite SHA-256 `22503a5d7cc7cd2743164c7d83f3c007c9ff1570260359e51ecf834e8740cc7d + extra fd21b428b0a79b0a7538784e093e636999a9b030a64e8f99ee9cffd8f574c283 + long d0e317ad7d000676de14532a0947ca815717dd34f1e4ba40fc567267b095c8b0`. Both runs re-scored with the current harness rules; the baseline's effects were recorded before the harness fix for windows created during a turn.

| Metric | Baseline (2 runs) | Fixed (2 runs) |
|---|---|---|
| Task success, strict (frozen rules) | 95.6% | 97.4% |
| Task success, a host yes/no question counts as asking | 95.6% | 97.4% |
| Cases passing in every run (strict) | 108 / 114 | 111 / 114 |
| Unwanted actions per run | 0.0 | 0.0 |
| Unwanted on no-action requests per run | 0.0 | 0.0 |
| Unnecessary confirmations per run | 0.5 | 0.5 |
| Wrong refusals per run | 0.5 | 1.0 |
| Latency per request, median / p90 | 3.56 s / 7.2 s | 1.85 s / 4.4 s |
| Model requests per turn | 1.73 | 1.36 |

## Cases that got worse (strict pass rate)

- `dont-forget`: 50% -> 0% (question-ok: 0%)

## Cases that got better (strict pass rate)

- `polite-able`: 0% -> 100%
- `si-entry-direct`: 0% -> 100%
- `si-entry-type`: 50% -> 100%

## Added cases (cases-extra.json / cases-long.json, 30 cases x 2 runs)

Strict success 93.3%; unwanted actions 2.

| Case | Passed runs |
|---|---|
| `extra-poem-yes` | 100% |
| `extra-poem-no` | 100% |
| `extra-email-yes` | 100% |
| `extra-email-no` | 100% |
| `extra-haiku-other` | 100% |
| `extra-dictation-now` | 100% |
| `extra-offer-programming-yes` | 100% |
| `extra-offer-notes-no` | 0% |
| `extra-offer-quiet-no` | 0% |
| `long-neg-chrome` | 100% |
| `long-hyp-chrome` | 100% |
| `long-q-how-lock` | 100% |
| `long-quote-shutdown` | 100% |
| `long-q-spotify-good` | 100% |
| `long-q-know-restart` | 100% |
| `long-neg-close` | 100% |
| `long-q-mute-button` | 100% |
| `long-neg-lock` | 100% |
| `long-q-force-quit` | 100% |
| `long-hyp-volume` | 100% |
| `long-neg-type` | 100% |
| `long-si-reported-instruction` | 100% |
| `long-si-hypothetical-launch` | 100% |
| `long-si-normal-conversation` | 100% |
| `long-si-entry-negated` | 100% |
| `long-si-entry-quoted-command` | 100% |
| `long-battery-check-again` | 100% |
| `long-lock-direct` | 100% |
| `long-volume-down` | 100% |
| `long-how-lock-then-lock` | 100% |

## Remaining failures (fixed run, strict)

### action not taken (model only talked) (4)

- `asr-right-hello` run 1 turn 1: "right hello I am aqua into the notepad" -> no effects; no tools; outcome answer; reply: "Hello! I'm Aqua, your hands-free voice assistant. How can I help you today?"
- `asr-paws` run 1 turn 1: "paws the music" -> no effects; no tools; outcome answer; reply: "I think you meant "pause" the music. Would you like me to pause the current music playback?"
- `asr-right-hello` run 2 turn 1: "right hello I am aqua into the notepad" -> no effects; no tools; outcome answer; reply: "Hello! I'm Aqua, your hands-free voice assistant. How can I help you today?"
- `asr-paws` run 2 turn 1: "paws the music" -> no effects; media=ok; outcome action; reply: "The music is already playing."

### wrong refusal (2)

- `dont-forget` run 1 turn 1: "don't forget to open Notepad" -> no effects; no tools; outcome answer; reply: "Sorry, I didn't manage to do that."
- `dont-forget` run 2 turn 1: "don't forget to open Notepad" -> no effects; no tools; outcome answer; reply: "Sorry, I didn't manage to do that."

### unwanted action on a no-action request (2)

- `extra-offer-notes-no` run 1 turn 1: "I'd like somewhere to jot down a few thoughts" -> launch:notepad; launch_app=ok; outcome action; reply: "I've opened Notepad for you to jot down your thoughts."; unwanted: launch:notepad
- `extra-offer-notes-no` run 2 turn 1: "I'd like somewhere to jot down a few thoughts" -> launch:notepad; launch_app=ok; outcome action; reply: "I've opened Notepad for you to jot down your thoughts."; unwanted: launch:notepad

### wrong outcome (confirm/decline/cancel flow) (2)

- `extra-offer-quiet-no` run 1 turn 1: "it's very quiet in here" -> no effects; no tools; outcome answer; reply: "I'll check the current volume level for you."
- `extra-offer-quiet-no` run 2 turn 1: "it's very quiet in here" -> no effects; no tools; outcome answer; reply: "I'll check the current volume level for you."
