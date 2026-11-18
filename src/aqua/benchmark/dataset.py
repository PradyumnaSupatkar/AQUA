"""Load and fingerprint the frozen, reference-separated benchmark fixtures."""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path


DEFAULT_DATASET = Path(__file__).resolve().parents[3] / "benchmarks" / "model_intelligence" / "dataset.json"
CATEGORIES = {"knowledge": 10, "reasoning": 10, "memory": 8,
              "instruction": 7, "tool": 10, "uncertainty": 5}


def dataset_hash(path=None) -> str:
    """Hash the exact auditable file, including any later accidental edit."""
    return sha256(Path(path or DEFAULT_DATASET).read_bytes()).hexdigest()


def load_dataset(path=None) -> dict:
    """Validate fixture structure, not any model answer or benchmark outcome.

    A custom suite may contain a different number of cases. The distributed
    suite's requested category distribution is separately regression-tested.
    Only turns[*].text is intended as model input, never references or checks.
    """
    value = json.loads(Path(path or DEFAULT_DATASET).read_text(encoding="utf-8"))
    if type(value) is not dict or value.get("schema_version") != 1:
        raise ValueError("Unsupported benchmark dataset schema.")
    cases = value.get("cases")
    if type(cases) is not list or not cases:
        raise ValueError("A benchmark dataset needs at least one case.")
    ids = set()
    for case in cases:
        if type(case) is not dict:
            raise ValueError("Each benchmark case must be an object.")
        identifier = case.get("id")
        if type(identifier) is not str or not identifier or identifier in ids:
            raise ValueError("Benchmark case IDs must be nonempty and unique.")
        ids.add(identifier)
        if case.get("category") not in CATEGORIES or case.get("temporal") not in {"static", "current", "private"}:
            raise ValueError("Invalid benchmark category or temporal scope.")
        if "reference" not in case or not isinstance(case.get("grading_notes"), str):
            raise ValueError("Each benchmark case needs an auditable reference and grading notes.")
        turns = case.get("turns")
        if type(turns) is not list or not turns:
            raise ValueError("Each case needs at least one user turn.")
        for turn in turns:
            if type(turn) is not dict or not isinstance(turn.get("text"), str) or not turn["text"].strip():
                raise ValueError("Each benchmark turn needs nonempty user text.")
            if "check" in turn and (type(turn["check"]) is not dict or not isinstance(turn["check"].get("kind"), str)):
                raise ValueError("Benchmark answer checks must be typed objects.")
            if "expected_tool" in turn and type(turn["expected_tool"]) is not dict:
                raise ValueError("Benchmark tool expectations must be objects.")
    if any(identifier not in ids for identifier in value.get("pilot_case_ids", [])):
        raise ValueError("Pilot cases must be present in the frozen suite.")
    return value


def category_counts(dataset: dict) -> dict[str, int]:
    return dict(Counter(case["category"] for case in dataset["cases"]))
