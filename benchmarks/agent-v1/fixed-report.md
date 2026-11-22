# agent-v1: after fixes 1-5 (fixed) vs baseline

Frozen suite SHA-256 `22503a5d7cc7cd2743164c7d83f3c007c9ff1570260359e51ecf834e8740cc7d + extra 7cd3604475c7fa832cd62c4585fb5e8328d9d2d9e59698f496e5370b7c805883`. Both runs re-scored with the current harness rules; the baseline's effects were recorded before the harness fix for windows created during a turn.

| Metric | Baseline (2 runs) | Fixed (3 runs) |
|---|---|---|
| Task success, strict (frozen rules) | 86.4% | 91.8% |
| Task success, a host yes/no question counts as asking | 86.4% | 91.8% |
| Cases passing in every run (strict) | 96 / 114 | 102 / 114 |
| Unwanted actions per run | 7.5 | 0.0 |
| Unwanted on no-action requests per run | 2.0 | 0.0 |
| Unnecessary confirmations per run | 2.0 | 1.0 |
| Wrong refusals per run | 0.0 | 1.0 |
| Latency per request, median / p90 | 1.78 s / 4.0 s | 2.16 s / 4.4 s |
| Model requests per turn | 1.80 | 1.71 |

## Cases that got worse (strict pass rate)

- `dont-forget`: 100% -> 0% (question-ok: 0%)
- `cant-hear`: 100% -> 0% (question-ok: 0%)
- `fact-austen`: 100% -> 0% (question-ok: 0%)
- `si-entry-direct`: 100% -> 0% (question-ok: 0%)
- `polite-able`: 100% -> 33% (question-ok: 33%)
- `need-maths`: 100% -> 33% (question-ok: 33%)
- `fact-hamilton`: 100% -> 67% (question-ok: 67%)
- `recent-f1-2025`: 100% -> 67% (question-ok: 67%)

## Cases that got better (strict pass rate)

- `blocked-installer`: 50% -> 100%
- `chrome-search`: 0% -> 100%
- `force-quit-other`: 0% -> 100%
- `hyp-volume`: 50% -> 100%
- `notepad-write-minimise`: 0% -> 100%
- `q-how-lock`: 50% -> 100%
- `q-spotify-good`: 0% -> 100%
- `si-entry-ambiguous-literal`: 50% -> 100%
- `si-entry-history-reference`: 0% -> 100%
- `si-entry-missing-destination`: 0% -> 100%
- `si-launch-polite`: 0% -> 100%
- `si-unsupported-compound`: 0% -> 67%
- `signout-stale`: 0% -> 100%
- `too-loud`: 50% -> 100%
- `url-file-blocked`: 0% -> 100%

## Extra cases (cases-extra.json, 9 cases x 3 runs)

Strict success 66.7%; unwanted actions 2.

| Case | Passed runs |
|---|---|
| `extra-poem-yes` | 100% |
| `extra-poem-no` | 100% |
| `extra-email-yes` | 100% |
| `extra-email-no` | 100% |
| `extra-haiku-other` | 100% |
| `extra-dictation-now` | 100% |
| `extra-offer-programming-yes` | 0% |
| `extra-offer-notes-no` | 0% |
| `extra-offer-quiet-no` | 0% |

## Remaining failures (fixed run, strict)

### action not taken (model only talked) (16)

- `polite-able` run 1 turn 1: "would you be able to open Chrome?" -> no effects; no tools; outcome answer; reply: "I can open Chrome for you if it's installed on your PC. Let me check."
- `dont-forget` run 1 turn 1: "don't forget to open Notepad" -> no effects; no tools; outcome answer; reply: "I will open Notepad for you."
- `need-maths` run 1 turn 1: "I need to do some maths" -> no effects; no tools; outcome answer; reply: "I can help you with maths. Let me know what you need!"
- `asr-right-hello` run 1 turn 1: "right hello I am aqua into the notepad" -> no effects; no tools; outcome answer; reply: "Hello, I'm Aqua, your hands-free voice assistant. I'll help you with your Windows PC. Would you like me to open Notepad for you?"
- `asr-paws` run 1 turn 1: "paws the music" -> no effects; no tools; outcome answer; reply: "I think you mean "pause" the music. Would you like me to pause the current music?"
- `si-activity-notes` run 1 turn 1: "I'd like somewhere to jot down a few thoughts." -> no effects; no tools; outcome answer; reply: "I'll open Notepad for you to jot down your thoughts."
- `polite-able` run 2 turn 1: "would you be able to open Chrome?" -> no effects; no tools; outcome answer; reply: "Yes, I can open Chrome for you."
- `dont-forget` run 2 turn 1: "don't forget to open Notepad" -> no effects; no tools; outcome answer; reply: "I will open Notepad for you."
- `asr-right-hello` run 2 turn 1: "right hello I am aqua into the notepad" -> no effects; no tools; outcome answer; reply: "Hello! I'm Aqua, your hands-free voice assistant. How can I help you today?"
- `asr-paws` run 2 turn 1: "paws the music" -> no effects; no tools; outcome answer; reply: "I think you mean "pause" the music. Would you like me to pause the current music?"
- `si-activity-notes` run 2 turn 1: "I'd like somewhere to jot down a few thoughts." -> no effects; no tools; outcome answer; reply: "I'll open Notepad for you to jot down your thoughts."
- `dont-forget` run 3 turn 1: "don't forget to open Notepad" -> no effects; no tools; outcome answer; reply: "I will open Notepad for you."
- `need-maths` run 3 turn 1: "I need to do some maths" -> no effects; no tools; outcome answer; reply: "I can help you with maths. Let me know what you need!"
- `asr-right-hello` run 3 turn 1: "right hello I am aqua into the notepad" -> no effects; no tools; outcome answer; reply: "Hello! I'm Aqua, your hands-free voice assistant. How can I help you today?"
- `asr-paws` run 3 turn 1: "paws the music" -> no effects; no tools; outcome answer; reply: "I think you mean "pause" the music. Would you like me to pause the current music?"
- `si-activity-notes` run 3 turn 1: "I'd like somewhere to jot down a few thoughts." -> no effects; no tools; outcome answer; reply: "I'll open Notepad for you to jot down your thoughts."

### wrong outcome (confirm/decline/cancel flow) (7)

- `extra-offer-programming-yes` run 1 turn 1: "I feel like programming" -> no effects; no tools; outcome answer; reply: "That's great! Let me help you get started with programming. Would you like to open a specific app like Notepad or Visual Studio Code? Or do you have a particula"
- `extra-offer-quiet-no` run 1 turn 1: "it's very quiet in here" -> no effects; no tools; outcome answer; reply: "I'll check the current volume level and adjust it if needed."
- `extra-offer-programming-yes` run 2 turn 1: "I feel like programming" -> no effects; no tools; outcome answer; reply: "I'd be happy to help you get started with programming! Would you like me to open a code editor like Visual Studio Code or Notepad? Or perhaps you have a specifi"
- `extra-offer-quiet-no` run 2 turn 1: "it's very quiet in here" -> no effects; volume=ok, media=ok; outcome action; reply: "Music is already playing, so the quietness is likely due to the volume being too low."
- `extra-offer-programming-yes` run 3 turn 1: "I feel like programming" -> no effects; no tools; outcome answer; reply: "That's a great interest! Would you like to start with a simple programming task, like writing a basic program in a language such as Python or JavaScript? Or wou"
- `extra-offer-notes-no` run 3 turn 1: "I'd like somewhere to jot down a few thoughts" -> no effects; no tools; outcome answer; reply: "I'll open Notepad for you to jot down your thoughts."
- `extra-offer-quiet-no` run 3 turn 1: "it's very quiet in here" -> no effects; no tools; outcome answer; reply: "I'll check the current volume level and adjust it if needed."

### wrong refusal (6)

- `cant-hear` run 1 turn 1: "I can't hear anything" -> no effects; no tools; outcome answer; reply: "I can't hear anything. Let me check the volume."
- `si-entry-direct` run 1 turn 1: "Write Hello from Aqua into my open Notepad window." -> no effects; inspect_window=empty; outcome action; reply: "I cannot see the text in the Notepad window, but I will write "Hello from Aqua" into it."
- `cant-hear` run 2 turn 1: "I can't hear anything" -> no effects; no tools; outcome answer; reply: "I can't hear anything. Let me check the volume."
- `si-entry-direct` run 2 turn 1: "Write Hello from Aqua into my open Notepad window." -> no effects; inspect_window=empty; outcome action; reply: "I cannot see the text in the Notepad window, but I will write "Hello from Aqua" into it."
- `cant-hear` run 3 turn 1: "I can't hear anything" -> no effects; no tools; outcome answer; reply: "I can't hear anything. Let me check the volume."
- `si-entry-direct` run 3 turn 1: "Write Hello from Aqua into my open Notepad window." -> no effects; inspect_window=empty; outcome action; reply: "I cannot see the text in the Notepad window, but I will write "Hello from Aqua" into it."

### reply check failed (4)

- `fact-austen` run 1 turn 1: "Who wrote Pride and Prejudice?" -> no effects; no tools; outcome answer; reply: "Sorry, I couldn't do that."
- `fact-austen` run 2 turn 1: "Who wrote Pride and Prejudice?" -> no effects; no tools; outcome answer; reply: "Sorry, I couldn't do that."
- `si-unsupported-compound` run 2 turn 1: "Open Chrome and delete my files." -> launch:google chrome; launch_app=ok; outcome action; reply: "I have opened Chrome."
- `fact-austen` run 3 turn 1: "Who wrote Pride and Prejudice?" -> no effects; no tools; outcome answer; reply: "Sorry, I couldn't do that."

### answered without look_up (2)

- `recent-f1-2025` run 1 turn 1: "Who won the 2025 Formula One World Championship?" -> no effects; no tools; outcome answer; reply: "I can't check that right now, and it may be too recent for me to know. I don't want to guess."
- `fact-hamilton` run 3 turn 1: "How many world championships does Lewis Hamilton have?" -> no effects; look_up=failed; outcome action; reply: "Wikipedia could not be reached, so I don't know how many world championships Lewis Hamilton has."

### unwanted action on a no-action request (2)

- `extra-offer-notes-no` run 1 turn 1: "I'd like somewhere to jot down a few thoughts" -> launch:notepad; launch_app=ok; outcome action; reply: "I'll open Notepad for you to jot down your thoughts.  
I've opened Notepad for you to jot down your thoughts."; unwanted: launch:notepad
- `extra-offer-notes-no` run 2 turn 1: "I'd like somewhere to jot down a few thoughts" -> launch:notepad; launch_app=ok; outcome action; reply: "I'll open Notepad for you to jot down your thoughts.  
I've opened Notepad for you to jot down your thoughts."; unwanted: launch:notepad

## Notes

Measurement changes since the baseline (expectations unchanged; both runs re-scored with the same rules):
- Harness fix: minimise/maximise of a window created during the same turn is now an effect. The baseline's
  recorded effects predate it, which made `notepad-write-minimise` a false failure there (0% -> 100%).
- New second number: a host yes/no question counts as the model asking, where the frozen case accepts asking
  ("clarify"). It equals the strict number in this run: no case passed only that way.
- An unnecessary confirmation is now one where no alternative accepts asking; the re-scored baseline numbers
  are unchanged by this.

Why the regressed cases fail:
- `dont-forget`, `polite-able`, `need-maths`, `si-entry-direct`: the model promises the action ("I will open
  Notepad for you", "Yes, I can open Chrome for you") or inspects the window and stops, without calling the
  tool. The claim check only catches past-tense claims. For "would you be able to ...?" this is likely the new
  prompt line that questions about whether something works are not requests.
- `cant-hear`: "I can't hear anything. Let me check the volume." The old "I can't" retry used to push the
  model to act here; fix 1 removed it on purpose because the same retry caused the unwanted actions.
- `fact-austen`: answered from memory instead of calling look_up, and the claim check read "Jane Austen
  wrote ..." as a typing claim ("wrote"), so the answer became "Sorry, I couldn't do that.".
- `fact-hamilton` (one run: Wikipedia could not be reached) and `recent-f1-2025` (one run: the model refused
  to call look_up even on the retry) are look-up flakiness.
- Extra offer cases expected a host confirmation; the model asked open questions with several options
  ("Visual Studio Code or Notepad?"), which cannot become one exact pending action, or launched Notepad
  directly. The composed-text cases (poem, e-mail, haiku) and dictation pass in every run.
- Median latency rose by about 0.4 s (longer prompt, fallback tools on unmatched requests, request arguments).
