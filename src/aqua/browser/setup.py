"""`aqua setup-browser`: register (or remove) the native host for Brave or Chrome, per user.

Per-user registration needs no admin rights: the browser reads the default value of
HKCU\\<browser key>\\NativeMessagingHosts\\com.aqua.browser_bridge, which is the path of a host
manifest JSON. Each browser gets its own manifest and launcher, so Brave and Chrome can be set up
(and removed) side by side. Brave does not read Chrome's key, so each needs its own entry.
"""

import json
import os
import sys
from pathlib import Path

from . import protocol

REGISTRY_KEYS = {
    "chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "brave": r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
}
EXTENSION_PAGES = {"chrome": "chrome://extensions", "brave": "brave://extensions"}
BROWSER_NAMES = {"chrome": "Chrome", "brave": "Brave"}


def install_folder() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Aqua" / "browser-bridge"


def extension_folder() -> Path:
    return Path(__file__).resolve().parents[3] / "extension"


class WindowsRegistry:
    """HKEY_CURRENT_USER only."""

    def set_default(self, path: str, value: str) -> None:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, value)

    def get_default(self, path: str) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                return winreg.QueryValueEx(key, "")[0]
        except OSError:
            return None

    def delete(self, path: str) -> bool:
        import winreg
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            return True
        except FileNotFoundError:
            return False


def _key(browser: str) -> str:
    return REGISTRY_KEYS[browser] + "\\" + protocol.HOST_NAME


def _paths(browser: str, folder: Path) -> tuple[Path, Path]:
    return folder / f"{protocol.HOST_NAME}.{browser}.json", folder / f"aqua-browser-host-{browser}.cmd"


def launcher_text(python: str, browser: str) -> str:
    # -I: isolated mode (ignores PYTHON* variables, the user site and the current folder).
    return f'@echo off\r\n"{python}" -I -m aqua.browser.host --browser {browser} %*\r\n'


def host_manifest(launcher: Path) -> dict:
    return {"name": protocol.HOST_NAME, "description": "Aqua browser bridge (local only)",
            "path": str(launcher), "type": "stdio", "allowed_origins": [protocol.ORIGIN]}


def install(browser: str, *, registry=None, folder: Path | None = None, python: str | None = None) -> Path:
    """Write this browser's launcher and host manifest and register it; returns the manifest path."""
    if browser not in REGISTRY_KEYS:
        raise ValueError(f"Unknown browser {browser!r}.")
    registry = registry if registry is not None else WindowsRegistry()
    folder = folder if folder is not None else install_folder()
    folder.mkdir(parents=True, exist_ok=True)
    manifest, launcher = _paths(browser, folder)
    launcher.write_text(launcher_text(python or sys.executable, browser), encoding="ascii", newline="")
    manifest.write_text(json.dumps(host_manifest(launcher), indent=2) + "\n", encoding="utf-8")
    registry.set_default(_key(browser), str(manifest))
    return manifest


def uninstall(browser: str, *, registry=None, folder: Path | None = None) -> bool:
    """Remove this browser's registration and files; the other browser's are left alone."""
    if browser not in REGISTRY_KEYS:
        raise ValueError(f"Unknown browser {browser!r}.")
    registry = registry if registry is not None else WindowsRegistry()
    folder = folder if folder is not None else install_folder()
    removed = registry.delete(_key(browser))
    for path in _paths(browser, folder):
        if path.exists():
            path.unlink()
            removed = True
    try:
        folder.rmdir()  # Only when empty, i.e. no other browser is set up.
    except OSError:
        pass
    return removed


def load_steps(browser: str) -> str:
    name, page = BROWSER_NAMES[browser], EXTENSION_PAGES[browser]
    return "\n".join((
        f"Native host registered for {name}. Now load the Aqua extension:",
        f"  1. Open {page} in {name}.",
        "  2. Turn on Developer mode (top right).",
        f"  3. Click 'Load unpacked' and choose {extension_folder()}",
        f"  4. Check that the extension ID is {protocol.EXTENSION_ID}.",
        "  5. Restart Aqua (aqua chat --engine new or aqua handsfree --engine new) and open a YouTube tab.",
        f"To remove: aqua setup-browser --browser {browser} --uninstall, then remove the extension in {page}.",
    ))


def run(browser: str, *, remove: bool = False, registry=None, folder: Path | None = None, out=print) -> int:
    if remove:
        removed = uninstall(browser, registry=registry, folder=folder)
        out(f"Removed the Aqua native host for {BROWSER_NAMES[browser]}." if removed
            else f"The Aqua native host was not set up for {BROWSER_NAMES[browser]}.")
        out(f"Also remove the Aqua extension in {EXTENSION_PAGES[browser]} if it is loaded.")
        return 0
    manifest = install(browser, registry=registry, folder=folder)
    out(f"Wrote {manifest}")
    out(load_steps(browser))
    return 0
