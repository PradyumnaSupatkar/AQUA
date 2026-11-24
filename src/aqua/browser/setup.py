"""`aqua setup-browser`: register (or remove) the native host for Brave or Chrome, per user.

Per-user registration needs no admin rights. On Windows, Chromium looks native hosts up under one
hard-coded key, HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\<host>, and Brave uses that
same code: a live test showed Brave reports "Specified native messaging host not found" with only
its own BraveSoftware key set. So both browsers share the Chrome key, one host manifest and one
launcher; the host tells the browsers apart by its parent process. Brave's own key is written too
(harmless, and right if Brave ever reads it). Each browser leaves a marker file, so removing one
keeps the shared registration until the last browser is removed.
"""

import json
import os
import sys
from pathlib import Path

from . import protocol

SHARED_KEY = r"Software\Google\Chrome\NativeMessagingHosts"  # What Chromium (and Brave) read on Windows.
OWN_KEYS = {"brave": r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts"}
REGISTRY_KEYS = {"chrome": SHARED_KEY, "brave": OWN_KEYS["brave"]}
EXTENSION_PAGES = {"chrome": "chrome://extensions", "brave": "brave://extensions"}
BROWSER_NAMES = {"chrome": "Chrome", "brave": "Brave"}
# Files of the first version (one manifest and launcher per browser), removed by install.
_OLD_FILES = tuple(f"{name}" for browser in ("brave", "chrome")
                   for name in (f"{protocol.HOST_NAME}.{browser}.json", f"aqua-browser-host-{browser}.cmd"))


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


def _key(base: str) -> str:
    return base + "\\" + protocol.HOST_NAME


def _paths(folder: Path) -> tuple[Path, Path]:
    """The shared host manifest and launcher."""
    return folder / f"{protocol.HOST_NAME}.json", folder / "aqua-browser-host.cmd"


def _marker(browser: str, folder: Path) -> Path:
    return folder / f"installed-{browser}"


def installed_browsers(folder: Path | None = None) -> list[str]:
    folder = folder if folder is not None else install_folder()
    return [b for b in protocol.BROWSERS if _marker(b, folder).exists()]


def launcher_text(python: str) -> str:
    # -I: isolated mode (ignores PYTHON* variables, the user site and the current folder).
    return f'@echo off\r\n"{python}" -I -m aqua.browser.host %*\r\n'


def host_manifest(launcher: Path) -> dict:
    return {"name": protocol.HOST_NAME, "description": "Aqua browser bridge (local only)",
            "path": str(launcher), "type": "stdio", "allowed_origins": [protocol.ORIGIN]}


def install(browser: str, *, registry=None, folder: Path | None = None, python: str | None = None) -> Path:
    """Write the shared launcher and host manifest and register them; returns the manifest path."""
    if browser not in REGISTRY_KEYS:
        raise ValueError(f"Unknown browser {browser!r}.")
    registry = registry if registry is not None else WindowsRegistry()
    folder = folder if folder is not None else install_folder()
    folder.mkdir(parents=True, exist_ok=True)
    for name in _OLD_FILES:
        (folder / name).unlink(missing_ok=True)
    manifest, launcher = _paths(folder)
    launcher.write_text(launcher_text(python or sys.executable), encoding="ascii", newline="")
    manifest.write_text(json.dumps(host_manifest(launcher), indent=2) + "\n", encoding="utf-8")
    registry.set_default(_key(SHARED_KEY), str(manifest))
    if browser in OWN_KEYS:
        registry.set_default(_key(OWN_KEYS[browser]), str(manifest))
    _marker(browser, folder).write_text("", encoding="ascii")
    return manifest


def uninstall(browser: str, *, registry=None, folder: Path | None = None) -> bool:
    """Remove this browser's registration. The shared key, manifest and launcher go only when no
    other browser is still set up."""
    if browser not in REGISTRY_KEYS:
        raise ValueError(f"Unknown browser {browser!r}.")
    registry = registry if registry is not None else WindowsRegistry()
    folder = folder if folder is not None else install_folder()
    marker = _marker(browser, folder)
    removed = marker.exists()
    marker.unlink(missing_ok=True)
    if browser in OWN_KEYS:
        removed = registry.delete(_key(OWN_KEYS[browser])) or removed
    if not installed_browsers(folder):
        removed = registry.delete(_key(SHARED_KEY)) or removed
        for path in (*_paths(folder), *(folder / name for name in _OLD_FILES),
                     *(folder / f"host-{b}.log" for b in (*protocol.BROWSERS, "unknown"))):
            if path.exists():
                path.unlink()
                removed = True
        try:
            folder.rmdir()
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


def _run_launcher(launcher: Path) -> tuple[bool, str]:
    import subprocess
    try:
        done = subprocess.run(["cmd.exe", "/d", "/c", str(launcher), "--check"], capture_output=True, timeout=30,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    text = (done.stdout + done.stderr).decode(errors="replace").strip()
    return done.returncode == 0 and "aqua browser host: ok" in text, text[-300:]


def _pipe_reachable() -> tuple[bool, str]:
    try:
        from . import winpipe
        pipe = winpipe.connect(protocol.pipe_name(winpipe.current_user_sid()))
    except OSError as exc:
        return False, f"not reachable ({exc}); start Aqua with --engine new (aqua chat or aqua handsfree)"
    pipe.close()
    return True, "Aqua is running and its pipe accepts this user"


def _last_seen(browser: str, folder: Path) -> tuple[bool, str]:
    path = folder / f"host-{browser}.log"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False, ("never: the extension has not started the host yet (reload the extension, or open "
                       "a YouTube tab)")
    started = [line for line in lines if "started by the extension" in line]
    linked = [line for line in lines if line.endswith("connected to Aqua")]
    problems = [line for line in lines[-5:] if "crashed" in line or "refused" in line]
    text = f"last started {started[-1].split(' ', 1)[0]}" if started else "never started"
    text += f"; last reached Aqua {linked[-1].split(' ', 1)[0]}" if linked else "; has not reached Aqua yet"
    if problems:
        text += f"; recent problem: {problems[-1]}"
    return bool(started) and not problems, text


def check(browser: str, *, registry=None, folder: Path | None = None, launcher_check=_run_launcher,
          pipe_check=_pipe_reachable) -> list[tuple[str, bool, str]]:
    """(item, ok, detail) for each part of the bridge, in the order a message travels."""
    registry = registry if registry is not None else WindowsRegistry()
    folder = folder if folder is not None else install_folder()
    expected, launcher = _paths(folder)
    results = []
    # The key the browser actually reads on Windows (Brave included) is the shared Chrome key.
    value = registry.get_default(_key(SHARED_KEY))
    detail = (f"HKCU\\{_key(SHARED_KEY)} = {value}" if value else
              f"HKCU\\{_key(SHARED_KEY)} is missing; run aqua setup-browser --browser {browser}")
    if value and browser not in installed_browsers(folder):
        detail += f" (set up for {', '.join(installed_browsers(folder)) or 'another browser'}, not marked for {browser})"
    results.append(("registry key", value is not None and Path(value) == expected, detail))
    manifest_ok, detail = False, f"{expected} is missing"
    try:
        manifest = json.loads(expected.read_text(encoding="utf-8"))
        problems = []
        if manifest.get("name") != protocol.HOST_NAME or manifest.get("type") != "stdio":
            problems.append("wrong name or type")
        if manifest.get("allowed_origins") != [protocol.ORIGIN]:
            problems.append(f"allowed_origins is {manifest.get('allowed_origins')}, not [{protocol.ORIGIN}]")
        if not Path(manifest.get("path", "")).exists():
            problems.append(f"host path {manifest.get('path')} does not exist")
        manifest_ok, detail = not problems, "; ".join(problems) or f"{expected} (origin {protocol.ORIGIN})"
    except (OSError, ValueError) as exc:
        detail = f"{expected}: {exc}"
    results.append(("host manifest", manifest_ok, detail))
    if launcher.exists():
        results.append(("host script runs", *launcher_check(launcher)))
    else:
        results.append(("host script runs", False, f"{launcher} is missing"))
    results.append(("Aqua pipe", *pipe_check()))
    results.append(("extension last seen", *_last_seen(browser, folder)))
    return results


def run(browser: str, *, remove: bool = False, registry=None, folder: Path | None = None, out=print,
        check_only: bool = False) -> int:
    if check_only:
        results = check(browser, registry=registry, folder=folder)
        for item, ok, detail in results:
            out(f"[{'ok' if ok else '!!'}] {item}: {detail}")
        return 0 if all(ok for _item, ok, _detail in results) else 1
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
