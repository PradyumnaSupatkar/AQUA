"""Run / confirm / deny / clarify, decided in Python from tiers and scope rules."""

from dataclasses import dataclass
from fnmatch import fnmatchcase
import ntpath
import os
from pathlib import Path
import re

from .registry import DESTRUCTIVE, DISRUPTIVE, SAFE, Tool, unsure_question

RUN, CONFIRM, DENY, CLARIFY = "run", "confirm", "deny", "clarify"

# Launching these is denied. Patterns match a lowercased executable file name,
# a normalized display name, or a Store app ID. Users can add lines to
# %LOCALAPPDATA%\Aqua\blocked-apps.txt (one pattern per line, # for comments).
BLOCKED_EXECUTABLES = (
    "cmd.exe", "powershell.exe", "powershell_ise.exe", "pwsh.exe", "wt.exe",
    "windowsterminal.exe", "regedit.exe", "regedt32.exe",
    "*.msi", "setup*.exe", "install*.exe", "uninstall*.exe", "unins*.exe",
)
BLOCKED_NAMES = (
    "cmd", "*command prompt*", "*powershell*", "pwsh", "*terminal*", "git bash",
    "registry editor", "regedit", "*installer*", "install *", "uninstall*", "setup",
)
BLOCKED_APP_IDS = ("microsoft.windowsterminal*", "microsoft.powershell*")


@dataclass(frozen=True, slots=True)
class Decision:
    action: str                 # run | confirm | deny | clarify
    tier: int = SAFE
    reason: str = ""            # deny: why (shown to the model)
    question: str = ""          # confirm / clarify: host-authored question


def normalize_name(value: str) -> str:
    return " ".join(re.sub(r"[^\w\s+#.]", " ", value.casefold()).split())


def user_blocklist_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Aqua" / "blocked-apps.txt"


def read_user_blocklist(path: Path | None = None) -> tuple[str, ...]:
    path = user_blocklist_path() if path is None else path
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ()
    return tuple(line.strip().casefold() for line in lines if line.strip() and not line.lstrip().startswith("#"))


class Policy:
    def __init__(self, *, extra_blocked: tuple[str, ...] = (), enabled_tools: frozenset[str] = frozenset()) -> None:
        self.blocked_executables = BLOCKED_EXECUTABLES + extra_blocked
        self.blocked_names = BLOCKED_NAMES + extra_blocked
        self.blocked_app_ids = BLOCKED_APP_IDS + extra_blocked
        self.enabled_tools = enabled_tools

    @classmethod
    def from_user_config(cls, path: Path | None = None) -> "Policy":
        return cls(extra_blocked=read_user_blocklist(path))

    def blocked(self, *, name: str = "", executable: str | None = None, app_id: str | None = None) -> bool:
        """True when an app must never be launched, by display name, exe or Store ID."""
        if name:
            normalized = normalize_name(name)
            if any(fnmatchcase(normalized, pattern) for pattern in self.blocked_names):
                return True
        if executable:
            base = ntpath.basename(executable.replace("/", "\\")).casefold()
            if any(fnmatchcase(base, pattern) for pattern in self.blocked_executables):
                return True
        if app_id:
            folded = app_id.casefold()
            if any(fnmatchcase(folded, pattern) for pattern in self.blocked_app_ids):
                return True
            # Desktop apps in the Apps folder use a path-like ID ending in the exe.
            if "\\" in folded and self.blocked(executable=folded):
                return True
        return False

    def decide(self, tool: Tool, args: dict, context) -> Decision:
        """The tier comes from the tool's own code; model arguments never set it."""
        tier = tool.tier_for(args, context)
        if tool.off_by_default and tool.name not in self.enabled_tools:
            return Decision(DENY, tier, reason="That action is turned off in Aqua's settings.")
        if tool.check is not None:
            scoped = tool.check(args, context)
            if scoped is not None:
                return Decision(scoped.action, tier, scoped.reason, scoped.question)
        if tier >= DISRUPTIVE:
            question = tool.confirm(args) if tool.confirm is not None else f"Run {tool.name}? Say yes or no."
            return Decision(CONFIRM, tier, question=question)
        if args.get("request") == "unsure":
            # The model was not sure the user asked for this: ask, never act on a guess.
            return Decision(CONFIRM, tier, question=unsure_question(tool, args))
        return Decision(RUN, tier)


def deny(reason: str) -> Decision:
    return Decision(DENY, reason=reason)


def clarify(question: str) -> Decision:
    return Decision(CLARIFY, question=question)


__all__ = ["CLARIFY", "CONFIRM", "DENY", "DESTRUCTIVE", "DISRUPTIVE", "Decision", "Policy", "RUN",
           "clarify", "deny", "normalize_name", "read_user_blocklist"]
