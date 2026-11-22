"""Switches for the host-side guards, for evaluation only (ablation runs).

Production always uses DEFAULT_GUARDS (everything on); no CLI option exposes
these. tools/benchmark_agent.py --disable-guard NAME turns one off.
"""

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True, slots=True)
class Guards:
    negation_guard: bool = True     # "don't ..." / hypotheticals get no tools (selection)
    claim_retry: bool = True        # a reply claiming an action no tool did: one retry, then an honest reply
    cant_retry: bool = True         # the model's own "Would you like me to ...?" becomes a host confirmation
    citation_strip: bool = True     # "According to Wikipedia" without a look-up: retry, then the citation is removed
    knowledge_cutoff: bool = True   # years after the model's training (2024) mean: look it up
    restate_request: bool = True    # each tool result restates the request so multi-step requests finish


DEFAULT_GUARDS = Guards()
GUARD_NAMES = tuple(field.name for field in fields(Guards))


def without(*names: str) -> Guards:
    """DEFAULT_GUARDS with the named guards turned off."""
    unknown = set(names) - set(GUARD_NAMES)
    if unknown:
        raise ValueError(f"Unknown guard(s): {', '.join(sorted(unknown))}. Known: {', '.join(GUARD_NAMES)}.")
    return replace(DEFAULT_GUARDS, **{name: False for name in names})
