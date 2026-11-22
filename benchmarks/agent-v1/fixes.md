# agent-v1: baseline failures and proposed fixes

Analysis of `baseline.jsonl` (114 cases x 2 runs, all guards on, suite SHA-256 `22503a5d…`). No engine
code was changed for this document. Counts are case runs (out of 228) unless stated otherwise.

## Failures grouped by cause

| Cause | Turns | Cases (runs) |
|---|---|---|
| Unwanted action on a request that needed none | 11 | `q-how-lock` (1), `q-spotify-good` (2), `hyp-volume` (1), `url-file-blocked` (2), `si-entry-ambiguous-literal` (1), `si-entry-missing-destination` (2), `si-entry-history-reference` (2) |
| Action not taken (model only talked) | 11 | `asr-right-hello` (2), `asr-paws` (2), `si-activity-notes` (2), `si-launch-polite` (2), `too-loud` (1), `notepad-write-minimise` (2, harness bug, below) |
| Reply check failed | 3 | `si-unsupported-compound` (2: opened Chrome, never said it cannot delete files), `blocked-installer` (1) |
| Wrong target or arguments | 2 | `chrome-search` (2: launched Chrome and typed the query into it, no search) |
| Unnecessary confirmation | 2 (+2 counted, passing) | `force-quit-other` turn 2 (2: "open notepad instead" re-asked the force quit); `q-know-restart` passed but asked "Restart the computer?" twice |
| Wrong confirmation flow | 2 | `signout-stale` (2: "sign me out" matched no keyword group, so `power` was hidden and the model refused) |

What the unwanted actions have in common: for each one a keyword showed the tool ("lock", "app", "volume",
"open", "type/write"). On the questions and hypotheticals the model first answered or asked "Would you
like me to ...?", and the `cant_retry` host guard then told it "you have a tool for this request, call it
now". Every one of these runs took 3 model requests, and with that guard off (see `ablation.md`) all of
them took 1 request and no action. So the guard turned the model's own hesitation into an action. In the
typing cases the model invented the text ("Hello, world!", "My name is Sam.") or the destination (it
launched Notepad itself).

Measurement note: `notepad-write-minimise` minimised the window correctly (`window_action=ok`), but the
harness only diffs window state for windows that existed before the turn. It should also diff windows
created during the turn. That is a harness fix and changes no expectation.

## Top 5 fixes

### 1. Replace the "I can't" retry with a host confirmation; the model says whether the user asked (unwanted actions on questions/hypotheticals)
**Kind:** guard change + confirming when unsure + tool-schema change (no wording rules).
First, stop telling the model to act when it hesitates: instead of the `cant_retry` nudge, when the
model's reply asks permission for an action whose tool was shown, the host asks the user the exact
question ("Lock the screen now? Say yes or no.") and acts only on "yes". Second, every action tool
(tier 1 and above) gets a required enum argument
`request: "asked" | "unsure"` ("asked" = the user directly asked for this action now). The policy treats
`unsure` like tier 2: a host question naming the exact action ("Lock the screen now? Say yes or no."),
which in the hands-free follow-up window costs one "yes". A tool call without the field fails schema
validation, as today. Add one prompt sentence that questions about how or whether something works, and
"if ... would ..." sentences, are not requests: use `unsure` or just answer.
**Expected impact:** the 6 unwanted-action runs on questions/hypotheticals (`q-how-lock`, `q-spotify-good`,
`hyp-volume`, `url-file-blocked`) become a question or an answer. The ablation already shows this for
the first half (no `cant_retry`: 92.1% success, 0 unwanted on no-action requests), so the no-action
categories go to about 34/34. Risk: a few extra confirmations on clear commands; measure "unnecessary confirmations" (baseline 4)
alongside it.

### 2. Ground typed text and launch targets in the request (invented text and destinations)
**Kind:** host argument check on the tool schema (confirming when unsure), no wording rules.
- `type_text.text` must come from the user: after normalizing case, punctuation and spacing, the text must
  appear in the current request (or a quoted span of it). Otherwise the host asks "What should I type?"
  (clarify), as it already does for a missing destination.
- A `launch_app` proposed in the same request as `type_text`, for an app the request does not mention
  (by name or alias), becomes a confirmation ("Open Notepad to type that?").
**Expected impact:** fixes `si-entry-ambiguous-literal` (1), `si-entry-missing-destination` (2) and
`si-entry-history-reference` (2): 5 runs, all of them unwanted actions. The dictation cases that pass
(`"Hello I am Aqua"`, `"shopping list"`, quoted literals) are unaffected because their text is in the
request.

### 3. A fallback tool set when no keyword matches, plus an ASR hint in the prompt (missed actions)
**Kind:** selection change + prompt change (no new keywords).
When no group matches, show a fixed small "likely actions" set (launch_app, type_text, volume, media,
window_action, power, look_up) instead of only the core three. The cap of 8 still holds, and visibility
still grants no permission. Add one prompt line: speech recognition may mishear words ("right" for
"write", "paws" for "pause", "note pad"); act on the most likely meaning, or ask if it is unclear.
**Expected impact:** `asr-right-hello` (2), `signout-stale` (2: shows `power`, then the normal
confirmation), `si-activity-notes` (2, where the model already said "I'll open Notepad" but had no tool) and
probably `asr-paws` (2): about 6-8 runs. Fix 1 keeps the added visibility from causing unwanted actions.

### 4. Remember declined or replaced confirmations; say what was not done (confirmation flow, partial requests)
**Kind:** engine/history change + prompt change.
When a pending action is declined, expires or is replaced by another request, store a short host record
in history ("not done: force_quit_app Spotify; the user did not confirm") so the model does not propose
it again ("open notepad instead" currently re-asks the force quit). Prompt: if part of a request cannot
be done (no tool for it), the reply must say so ("I opened Chrome, but I can't delete files").
**Expected impact:** `force-quit-other` (2, also removes 2 unnecessary confirmations) and
`si-unsupported-compound` (2): 4 runs.

### 5. A search tool that names the browser; fix the harness's window diff
**Kind:** tool-schema change + harness fix.
Give `web_search` an optional `browser` argument (default browser if absent) and say in its description
that searching never means typing into a browser. Separately, fix the effect differ for windows created
during the turn.
**Expected impact:** `chrome-search` (2) and the measured `notepad-write-minimise` (2): 4 runs.

### Together
About 25 of the 31 failed case runs are addressed, so roughly 86% -> 95-97% task success, and the
unwanted actions go from 15 to about 0-2. All five can be checked against this frozen suite: rerun
`tools/benchmark_agent.py` and compare with `baseline-summary.json`.
