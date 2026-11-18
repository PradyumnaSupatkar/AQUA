"""Launch only fixed, approved executables without a shell or model arguments."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType


@dataclass(frozen=True)
class ActionResult:
    success: bool
    message: str


LOCAL_APPDATA = Path(
    os.environ.get(
        "LOCALAPPDATA",
        str(Path.home() / "AppData" / "Local"),
    )
)

PROGRAM_FILES = Path(
    os.environ.get("ProgramFiles", r"C:\Program Files")
)
SYSTEM_DIRECTORY = Path(
    os.environ.get("SystemRoot", r"C:\Windows")
) / "System32"


ALLOWED_APPS = MappingProxyType({
    "notepad": (str(SYSTEM_DIRECTORY / "notepad.exe"),),
    "calculator": (str(SYSTEM_DIRECTORY / "calc.exe"),),
    "vs code": (
        str(
            LOCAL_APPDATA
            / "Programs"
            / "Microsoft VS Code"
            / "Code.exe"
        ),
    ),
    "chrome": (
        str(
            PROGRAM_FILES
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe"
        ),
    ),
})


APP_ALIASES = MappingProxyType({
    "visual studio code": "vs code",
    "vscode": "vs code",
    "google chrome": "chrome",
    "calc": "calculator",
})


def normalize_app_name(name: str) -> str:
    normalized = " ".join(name.casefold().split())
    return APP_ALIASES.get(normalized, normalized)


def open_application(app_name: str) -> ActionResult:
    if not isinstance(app_name, str):
        return ActionResult(False, "That application is not approved.")
    name = normalize_app_name(app_name)
    command = ALLOWED_APPS.get(name)

    if command is None:
        return ActionResult(
            success=False,
            message="That application is not approved.",
        )

    try:
        subprocess.Popen(
            list(command),
            shell=False,
        )
    except OSError as exc:
        return ActionResult(
            success=False,
            message=f"Could not open {name}: {exc}",
        )

    return ActionResult(
        success=True,
        message=f"Opening {name}.",
    )
