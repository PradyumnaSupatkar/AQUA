# Local model intelligence benchmark

Dataset SHA-256: `f5c16df28746f96e826f2d64f9d6826b9a9098a3bcdddfd8d99a092d8a976af3`

All executions were stubbed. No application was launched. Reasoning-channel text was not saved.

Manual-review cases are not counted as correct. Accuracy over all completed cases includes errors and pending review in its denominator.

| Config | Cases | Correct | Incorrect | Review | Errors | Accuracy | Median turn (s) | P90 turn (s) | Requests | Gen tok/s* |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 8/8 | 7 | 1 | 0 | 0 | 87.5% | 1.334 | 2.724 | 11 | 24.894 |
| A05 | 8/8 | 7 | 1 | 0 | 0 | 87.5% | 1.305 | 2.687 | 11 | 24.992 |
| C | 8/8 | 6 | 1 | 1 | 0 | 75.0% | 4.599 | 10.841 | 11 | 6.780 |
| C05 | 8/8 | 5 | 2 | 1 | 0 | 62.5% | 5.681 | 8.840 | 11 | 6.595 |
| B | 8/8 | 6 | 1 | 1 | 0 | 75.0% | 30.257 | 39.646 | 11 | 6.264 |
| E | 8/8 | 4 | 3 | 1 | 0 | 50.0% | 1.950 | 4.252 | 11 | 13.032 |
| D | 8/8 | 7 | 0 | 1 | 0 | 87.5% | 9.746 | 25.475 | 11 | 12.587 |

*Generation tokens/second includes hidden thinking where reported, not answer-only tokens.
The observed thinking-to-answer interval is not an exact server thinking duration.
First-response load is observed through runtime residency and server statistics; models were not deliberately unloaded.

## Accuracy by category

| Config | Category | Correct | Incorrect | Review | Errors | Total |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| A | instruction | 1 | 0 | 0 | 0 | 1 |
| A | knowledge | 1 | 0 | 0 | 0 | 1 |
| A | memory | 1 | 0 | 0 | 0 | 1 |
| A | reasoning | 1 | 1 | 0 | 0 | 2 |
| A | tool | 2 | 0 | 0 | 0 | 2 |
| A | uncertainty | 1 | 0 | 0 | 0 | 1 |
| A05 | instruction | 1 | 0 | 0 | 0 | 1 |
| A05 | knowledge | 1 | 0 | 0 | 0 | 1 |
| A05 | memory | 1 | 0 | 0 | 0 | 1 |
| A05 | reasoning | 1 | 1 | 0 | 0 | 2 |
| A05 | tool | 2 | 0 | 0 | 0 | 2 |
| A05 | uncertainty | 1 | 0 | 0 | 0 | 1 |
| C | instruction | 1 | 0 | 0 | 0 | 1 |
| C | knowledge | 0 | 0 | 1 | 0 | 1 |
| C | memory | 1 | 0 | 0 | 0 | 1 |
| C | reasoning | 1 | 1 | 0 | 0 | 2 |
| C | tool | 2 | 0 | 0 | 0 | 2 |
| C | uncertainty | 1 | 0 | 0 | 0 | 1 |
| C05 | instruction | 1 | 0 | 0 | 0 | 1 |
| C05 | knowledge | 0 | 0 | 1 | 0 | 1 |
| C05 | memory | 1 | 0 | 0 | 0 | 1 |
| C05 | reasoning | 0 | 2 | 0 | 0 | 2 |
| C05 | tool | 2 | 0 | 0 | 0 | 2 |
| C05 | uncertainty | 1 | 0 | 0 | 0 | 1 |
| B | instruction | 1 | 0 | 0 | 0 | 1 |
| B | knowledge | 0 | 0 | 1 | 0 | 1 |
| B | memory | 1 | 0 | 0 | 0 | 1 |
| B | reasoning | 2 | 0 | 0 | 0 | 2 |
| B | tool | 2 | 0 | 0 | 0 | 2 |
| B | uncertainty | 0 | 1 | 0 | 0 | 1 |
| E | instruction | 0 | 1 | 0 | 0 | 1 |
| E | knowledge | 0 | 0 | 1 | 0 | 1 |
| E | memory | 1 | 0 | 0 | 0 | 1 |
| E | reasoning | 0 | 2 | 0 | 0 | 2 |
| E | tool | 2 | 0 | 0 | 0 | 2 |
| E | uncertainty | 1 | 0 | 0 | 0 | 1 |
| D | instruction | 1 | 0 | 0 | 0 | 1 |
| D | knowledge | 0 | 0 | 1 | 0 | 1 |
| D | memory | 1 | 0 | 0 | 0 | 1 |
| D | reasoning | 2 | 0 | 0 | 0 | 2 |
| D | tool | 2 | 0 | 0 | 0 | 2 |
| D | uncertainty | 1 | 0 | 0 | 0 | 1 |

## Important failures and review candidates

### A / r05 — incorrect

> {"minutes": 9}

Criteria: [{"turn": 1, "status": "correct", "detail": "Structured proposal, execution, pending state and outcome match expectations."}, {"turn": 1, "status": "incorrect", "detail": "Parsed JSON conclusion differs from reference: {'minutes': 9}."}]

### A05 / r05 — incorrect

> {"minutes": 9}

Criteria: [{"turn": 1, "status": "correct", "detail": "Structured proposal, execution, pending state and outcome match expectations."}, {"turn": 1, "status": "incorrect", "detail": "Parsed JSON conclusion differs from reference: {'minutes': 9}."}]

### C / k06 — manual_review

> In Python 3.12, the literal `{}` creates a **dict** (dictionary). To create an empty set, you must use the expression `set()`.

Criteria: [{"turn": 1, "status": "correct", "detail": "Structured proposal, execution, pending state and outcome match expectations."}, {"turn": 1, "status": "manual_review", "detail": "Review the answer's assertions/negation or equivalent wording against the reference.", "unsupported_claim": null}]

### C / r05 — incorrect

> {"minutes": 8}

Criteria: [{"turn": 1, "status": "correct", "detail": "Structured proposal, execution, pending state and outcome match expectations."}, {"turn": 1, "status": "incorrect", "detail": "Parsed JSON conclusion differs from reference: {'minutes': 8}."}]

### C05 / k06 — manual_review

> In Python 3.12, the literal `{}` creates a dictionary (an empty dict). To create an empty set, you use the `set()` function (e.g., `set()`).

Criteria: [{"turn": 1, "status": "correct", "detail": "Structured proposal, execution, pending state and outcome match expectations."}, {"turn": 1, "status": "manual_review", "detail": "Review the answer's assertions/negation or equivalent wording against the reference.", "unsupported_claim": null}]

### C05 / r02 — incorrect

> I couldn't validate that tool request. No application was opened. Please clarify which approved app you want.

Criteria: [{"turn": 1, "status": "incorrect", "detail": "Tool mismatch: proposed=[None], executed=[], pending=None, outcome='denied'."}, {"turn": 1, "status": "incorrect", "detail": "Requested JSON conclusion was not valid JSON."}]

### C05 / r05 — incorrect

> {
>   "minutes": 7
> }

Criteria: [{"turn": 1, "status": "correct", "detail": "Structured proposal, execution, pending state and outcome match expectations."}, {"turn": 1, "status": "incorrect", "detail": "Parsed JSON conclusion differs from reference: {'minutes': 7}."}]

### B / k06 — manual_review

> In Python 3.12:
> - `{}` creates an empty **dict** (dictionary)
> - `set()` creates an empty **set**
> 
> The curly brace literal `{}` is for dictionaries, while `set()` is the constructor for sets.

Criteria: [{"turn": 1, "status": "correct", "detail": "Structured proposal, execution, pending state and outcome match expectations."}, {"turn": 1, "status": "manual_review", "detail": "Review the answer's assertions/negation or equivalent wording against the reference.", "unsupported_claim": null}]

### B / u01 — incorrect

> Would you like me to open chrome? Please confirm explicitly.

Criteria: [{"turn": 1, "status": "incorrect", "detail": "Tool mismatch: proposed=['chrome'], executed=[], pending='chrome', outcome='clarification'."}, {"turn": 1, "status": "manual_review", "detail": "Review whether uncertainty is justified and whether any concrete assertion is supported.", "unsupported_claim": null, "appropriate_uncertainty": null}]

### E / k06 — manual_review

> The literal `{}` in Python 3.12 creates an empty dictionary, not an empty set. To create an empty set, you should use `set()`.

Criteria: [{"turn": 1, "status": "correct", "detail": "Structured proposal, execution, pending state and outcome match expectations."}, {"turn": 1, "status": "manual_review", "detail": "Review the answer's assertions/negation or equivalent wording against the reference.", "unsupported_claim": null}]

### E / r02 — incorrect

> {
> "probability": 0.6
> }

Criteria: [{"turn": 1, "status": "correct", "detail": "Structured proposal, execution, pending state and outcome match expectations."}, {"turn": 1, "status": "incorrect", "detail": "Parsed JSON conclusion differs from reference: {'probability': 0.6}."}]

### E / r05 — incorrect

> ```json
> {
> "minutes": 7
> }
> ```

Criteria: [{"turn": 1, "status": "correct", "detail": "Structured proposal, execution, pending state and outcome match expectations."}, {"turn": 1, "status": "incorrect", "detail": "Parsed JSON conclusion differs from reference: {'minutes': 7}."}]

### E / i04 — incorrect

> I can write that Python function for you.
> 
> ```python
> def safe_divide(a, b):
>     if b == 0:
>         return None
>     else:
>         return a / b
> ```

Criteria: [{"turn": 1, "status": "correct", "detail": "Structured proposal, execution, pending state and outcome match expectations."}, {"turn": 1, "status": "incorrect", "detail": "Requested bare Python function did not parse."}]

### D / k06 — manual_review

> The literal `{}` in Python 3.12 creates an empty dictionary (`dict`). An empty set is created using `set()`.

Criteria: [{"turn": 1, "status": "correct", "detail": "Structured proposal, execution, pending state and outcome match expectations."}, {"turn": 1, "status": "manual_review", "detail": "Review the answer's assertions/negation or equivalent wording against the reference.", "unsupported_claim": null}]

