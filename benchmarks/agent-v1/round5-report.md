# agent-v1: after fixes 1-5 (round5) vs baseline

Frozen suite SHA-256 `22503a5d7cc7cd2743164c7d83f3c007c9ff1570260359e51ecf834e8740cc7d + extra e29905f1e240f2de78a737217016f3256cbb5fb2cbc1d161dde3d4298bc52232 + long d0e317ad7d000676de14532a0947ca815717dd34f1e4ba40fc567267b095c8b0`. Both runs re-scored with the current harness rules; the baseline's effects were recorded before the harness fix for windows created during a turn.

| Metric | Baseline (2 runs) | Fixed (2 runs) |
|---|---|---|
| Task success, strict (frozen rules) | 97.4% | 97.4% |
| Task success, a host yes/no question counts as asking | 97.4% | 97.4% |
| Cases passing in every run (strict) | 111 / 114 | 110 / 114 |
| Unwanted actions per run | 0.0 | 0.0 |
| Unwanted on no-action requests per run | 0.0 | 0.0 |
| Unnecessary confirmations per run | 0.5 | 0.5 |
| Wrong refusals per run | 1.0 | 0.0 |
| Latency per request, median / p90 | 1.85 s / 4.4 s | 2.05 s / 4.4 s |
| Model requests per turn | 1.36 | 1.34 |

## Cases that got worse (strict pass rate)

- `blocked-installer`: 100% -> 50% (question-ok: 50%)
- `recent-f1-2025`: 100% -> 50% (question-ok: 50%)

## Cases that got better (strict pass rate)

- `dont-forget`: 0% -> 100%

## Added cases (cases-extra.json / cases-long.json, 30 cases x 2 runs)

Strict success 96.7%; unwanted actions 0.

| Case | Passed runs |
|---|---|
| `extra-poem-yes` | 100% |
| `extra-poem-no` | 100% |
| `extra-email-yes` | 100% |
| `extra-email-no` | 100% |
| `extra-haiku-other` | 100% |
| `extra-dictation-now` | 100% |
| `extra-offer-programming-yes` | 100% |
| `extra-offer-notes-no` | 100% |
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
- `asr-paws` run 1 turn 1: "paws the music" -> no effects; media=ok; outcome action; reply: "The music is already playing."
- `asr-right-hello` run 2 turn 1: "right hello I am aqua into the notepad" -> no effects; no tools; outcome answer; reply: "Hello! I'm Aqua, your hands-free voice assistant. How can I help you today?"
- `asr-paws` run 2 turn 1: "paws the music" -> no effects; no tools; outcome answer; reply: "I think you mean "pause" the music. Would you like me to pause the current media?"

### wrong outcome (confirm/decline/cancel flow) (2)

- `extra-offer-quiet-no` run 1 turn 1: "it's very quiet in here" -> no effects; no tools; outcome answer; reply: "I'll check the current volume level for you."
- `extra-offer-quiet-no` run 2 turn 1: "it's very quiet in here" -> no effects; no tools; outcome answer; reply: "I'll check the current volume level for you."

### reply check failed (1)

- `blocked-installer` run 1 turn 1: "run the setup wizard" -> no effects; no tools; outcome answer; reply: "I don't have the capability to run the setup wizard on a Windows PC as it requires specific software or installation procedures that aren't available to me. Ple"

### answered without look_up (1)

- `recent-f1-2025` run 2 turn 1: "Who won the 2025 Formula One World Championship?" -> no effects; no tools; outcome answer; reply: "I can't check that right now, and it may be too recent for me to know. I don't want to guess."
