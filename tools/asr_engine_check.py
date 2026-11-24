"""Do the 30 recorded commands produce the right tool arguments with Parakeet + Aqua's fixes?

For each recording in .cache/asr-set: transcribe with the installed Parakeet model, apply the
transcript fixes (aqua.speech.postprocess: 'write' homophones, spoken numbers), then run the real
engine (local Qwen through Ollama) on a fake desktop and a fake YouTube bridge, and check the
tool call it makes. The raw transcript is run too when the fixes change it. Look-up is off (no
network). Results go to benchmarks/asr-v1/engine-check.md and .json (no audio).

    .venv\\Scripts\\python.exe tools\\asr_engine_check.py
"""

import json
import re
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from aqua.agent.context import ToolContext  # noqa: E402
from aqua.agent.loop import AgentEngine  # noqa: E402
from aqua.agent.policy import Policy  # noqa: E402
from aqua.agent.prompt import SYSTEM_PROMPT  # noqa: E402
from aqua.brain.conversation import Conversation  # noqa: E402
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings, OllamaClient  # noqa: E402
from aqua.browser.fake import FakeBrowserBridge  # noqa: E402
from aqua.speech.parakeet import ParakeetRecognizer  # noqa: E402
from aqua.speech.postprocess import prepare  # noqa: E402
from aqua.tools import build_registry  # noqa: E402
from aqua.tools.adapter import AppEntry  # noqa: E402
from aqua.tools.app_index import AppIndex  # noqa: E402
from aqua.tools.fake import DEFAULT_APPS, FakeDesktop  # noqa: E402

SET = ROOT / ".cache" / "asr-set"
OUT = ROOT / "benchmarks" / "asr-v1"
BRAVE = AppEntry("Brave", r"C:\Start Menu\Brave.lnk", "shortcut",
                 r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe")
PAUSE = [("youtube", {"action": "pause"}), ("media", {"action": "pause|play_pause"})]
# prompt id -> acceptable (tool, {arg: regex}) calls; "clarify" also accepts the host asking which app.
EXPECT = {
    "write-second-line": [("type_text", {"text": r"this is (a|the) second line"})],
    "write-hello": [("type_text", {"text": r"hello,? i am aqua"}), "clarify"],
    "write-meeting": [("type_text", {"text": r"meeting at noon"}), "clarify"],
    "write-shopping": [("type_text", {"text": r"buy milk and eggs"})],
    "open-notepad": [("launch_app", {"app": r"notepad"})],
    "open-vscode": [("launch_app", {"app": r"vs ?code|visual studio code"})],
    "open-spotify": [("launch_app", {"app": r"spotify"})],
    "open-calculator": [("launch_app", {"app": r"calculator"})],
    "open-chrome": [("launch_app", {"app": r"chrome"})],
    "open-brave": [("launch_app", {"app": r"brave"})],
    "open-visual-studio-code": [("launch_app", {"app": r"visual studio code|vs ?code"})],
    "pause-video": PAUSE, "pause-music": PAUSE, "pause-it": PAUSE,
    "volume-down": [("volume", {"action": r"down"})],
    "volume-30": [("volume", {"action": r"set", "level": r"30"})],
    "lock": [("lock_screen", {})],
    "quality-1080": [("youtube", {"action": "quality", "quality": r"1080p?"})],
    "back-10": [("youtube", {"action": "seek", "seconds": r"-10"})],
    "jump-230": [("youtube", {"action": "seek", "to": r"2:30|150"}), ("youtube", {"action": "seek", "seconds": "150"})],
    "search-lofi": [("youtube", {"action": "search", "query": r"lo-?fi hip hop"}),
                    ("web_search", {"query": r"lo-?fi hip hop"})],
    "play-second": [("youtube", {"action": "play_result", "n": r"2"})],
    "whats-playing": [("youtube", {"action": "now_playing"})],
    # The engine gives the model the local time in every request, so answering directly is right too.
    "question-time": [("get_time", {}), r"answer~\b\d{1,2}:\d{2}\b"],
    "question-fact": ["answer:austen"],
    "negation-chrome": ["none"],
    "close-notepad": [("close_app", {"app": r"notepad"})],
    "minimise-spotify": [("window_action", {"action": r"minimi[sz]e", "window": r"spotify"})],
    "brightness-up": [("brightness", {"action": r"up|set"})],
    "long-sentence": [("type_text", {"text": r"remember to call the dentist"})],
}


def audio(path: Path) -> bytes:
    with wave.open(str(path)) as handle:
        return handle.readframes(handle.getnframes())


def engine_for(prompt_id: str, client) -> tuple[AgentEngine, FakeDesktop]:
    desktop = FakeDesktop(apps=(*DEFAULT_APPS, BRAVE))
    desktop.add_window("Untitled - Notepad", "notepad.exe", front=False)
    desktop.add_window("Spotify Premium", "spotify.exe", front=False)
    bridge = FakeBrowserBridge()
    if prompt_id == "play-second":
        bridge.add_tab("chrome", 100, page="results", results=[(f"lofi hip hop {n}", f"Channel {n}") for n in range(1, 6)])
    else:
        bridge.add_tab("chrome", 100)
    context = ToolContext(desktop=desktop, apps=AppIndex(desktop.app_entries, clock=desktop.monotonic),
                          policy=Policy(), browser=bridge)
    engine = AgentEngine(client, model=DEFAULT_CHAT_MODEL, settings=GenerationSettings(),
                         registry=build_registry(lookup=False), context=context,
                         conversation=Conversation(system_prompt=SYSTEM_PROMPT))
    return engine, desktop


def judge(prompt_id: str, result, engine) -> tuple[bool, str]:
    ops = list(result.task.operations) if result.task else []
    calls = [(op.tool, op.args, op.status) for op in ops]
    pending = engine.pending
    if pending is not None:
        calls.append((pending.tool, pending.args, "asks first"))
    shown = "; ".join(f"{tool}({', '.join(f'{k}={v}' for k, v in args.items() if k != 'request')}) {status}"
                      for tool, args, status in calls) or f"no tool ({result.outcome})"
    for expected in EXPECT[prompt_id]:
        if expected == "clarify" and result.outcome == "clarify":
            return True, shown + " [asked which app]"
        if expected == "none" and not any(status in {"ok", "unverified"} for _t, _a, status in calls):
            return True, shown
        if isinstance(expected, str) and expected.startswith("answer~"):
            if re.search(expected[7:], result.text):
                return True, shown + f" [reply: {result.text[:60]}]"
            continue
        if isinstance(expected, str) and expected.startswith("answer:"):
            if expected[7:] in result.text.casefold():
                return True, shown + f" [reply: {result.text[:60]}]"
            continue
        if isinstance(expected, tuple):
            tool, args = expected
            for got_tool, got_args, status in calls:
                if got_tool == tool and status in {"ok", "unverified", "duplicate", "asks first"} and all(
                        re.fullmatch(rx, str(got_args.get(key, "")), re.I) for key, rx in args.items()):
                    return True, shown
    return False, shown


def run_text(prompt_id: str, text: str, client) -> dict:
    engine, _desktop = engine_for(prompt_id, client)
    started = time.perf_counter()
    result = engine.respond(text)
    ok, shown = judge(prompt_id, result, engine)
    return {"text": text, "ok": ok, "calls": shown, "reply": result.text, "seconds": round(time.perf_counter() - started, 2)}


def main() -> int:
    prompts = json.loads((SET / "prompts.json").read_text(encoding="utf-8"))["prompts"]
    recognizer = ParakeetRecognizer()
    client = OllamaClient(timeout=180, keep_alive="30m")
    client.check_model(DEFAULT_CHAT_MODEL)
    rows = []
    for prompt in prompts:
        heard = recognizer.transcribe(audio(SET / prompt["file"]), 16000).text
        engine, _ = engine_for(prompt["id"], client)
        apps = engine.context.apps
        fixed, notes = prepare(heard, is_app=lambda name: apps.find(name).kind == "found")
        row = {"id": prompt["id"], "prompt": prompt["text"], "heard": heard, "fixed": fixed, "notes": notes}
        row["with_fixes"] = run_text(prompt["id"], fixed, client)
        row["raw"] = run_text(prompt["id"], heard, client) if fixed != heard else row["with_fixes"]
        rows.append(row)
        mark = "ok " if row["with_fixes"]["ok"] else "BAD"
        print(f"{mark} {prompt['id']:24} {heard!r} -> {row['with_fixes']['calls']}", flush=True)
    raw_ok = sum(r["raw"]["ok"] for r in rows)
    fixed_ok = sum(r["with_fixes"]["ok"] for r in rows)
    lines = ["# asr-v1 engine check: Parakeet + transcript fixes -> tool arguments", "",
             f"{len(rows)} recorded commands, Parakeet TDT 0.6B v3 int8 (installed model, 0.5 s padding), "
             "transcript fixes from aqua.speech.postprocess, the real engine with local Qwen on a fake desktop "
             "(Notepad and Spotify open) and a fake YouTube bridge; look-up off. "
             f"{time.strftime('Run %Y-%m-%d %H:%M')}.", "",
             f"Right tool and arguments: **{fixed_ok}/{len(rows)} with the fixes**, {raw_ok}/{len(rows)} on the raw "
             "transcript.", "",
             "| Prompt | Parakeet heard | After fixes | Tool call (with fixes) | OK | Raw OK |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        fixed = r["fixed"] if r["fixed"] != r["heard"] else "(same)"
        lines.append(f"| {r['prompt']} | {r['heard']} | {fixed} | {r['with_fixes']['calls']} | "
                     f"{'yes' if r['with_fixes']['ok'] else '**no**'} | {'yes' if r['raw']['ok'] else 'no'} |")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "engine-check.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "engine-check.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwith fixes {fixed_ok}/{len(rows)}, raw {raw_ok}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
