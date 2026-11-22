"""Time, battery, volume, media, brightness and lock screen."""

from datetime import datetime

from aqua.agent.context import ToolContext
from aqua.agent.policy import clarify
from aqua.agent.registry import READ, SAFE, Tool, ToolOutcome, UndoRecord

VOLUME_STEP = 10
BRIGHTNESS_STEP = 10
_MEDIA_DONE = {"play_pause": "pressed play/pause", "pause": "paused the playback", "play": "resumed the playback",
               "next": "skipped to the next track", "previous": "went back to the previous track"}


def _time(args: dict, context: ToolContext) -> ToolOutcome:
    now = datetime.now().astimezone()
    hour = now.strftime("%I").lstrip("0") or "12"
    spoken = f"{hour}:{now:%M} {now:%p}"
    return ToolOutcome("ok", f"It is {spoken} on {now:%A}, {now.day} {now:%B %Y}.", data={"time": spoken})


def _battery(args: dict, context: ToolContext) -> ToolOutcome:
    battery = context.desktop.battery()
    if not battery.present:
        return ToolOutcome("ok", "This PC has no battery; it runs on mains power.", data={"battery": None})
    if battery.percent is None:
        return ToolOutcome("failed", "Windows did not report the battery level.")
    state = "charging" if battery.charging else "not charging" if battery.charging is False else "unknown"
    message = f"Battery is at {battery.percent}% and {state}."
    if battery.minutes_left and not battery.charging:
        message += f" About {battery.minutes_left // 60} h {battery.minutes_left % 60} min left."
    return ToolOutcome("ok", message, data={"percent": battery.percent, "charging": battery.charging})


def _volume_check(args: dict, context: ToolContext):
    if args["action"] == "set" and "level" not in args:
        return clarify("What volume should I set, from 0 to 100?")
    return None


def _volume(args: dict, context: ToolContext) -> ToolOutcome:
    desktop, action = context.desktop, args["action"]
    level, muted = desktop.get_volume()

    def undo() -> str:
        desktop.set_volume(level)
        desktop.set_mute(muted)
        return f"put the volume back to {level}%" + (" and muted" if muted else "")
    record = UndoRecord("the volume change", undo)
    if action == "mute":
        desktop.set_mute(True)
        return ToolOutcome("ok", "Muted the sound.", done="muted the sound", undo=record)
    if action == "unmute":
        desktop.set_mute(False)
        return ToolOutcome("ok", f"Unmuted; volume is {level}%.", done="unmuted the sound", undo=record)
    if action == "set":
        new = args["level"]
    else:
        step = args.get("level", VOLUME_STEP)
        new = level + step if action == "up" else level - step
    new = max(0, min(100, int(new)))
    desktop.set_volume(new)
    if muted and new > 0 and action in {"up", "set"}:
        desktop.set_mute(False)
    verb = {"up": "turned the volume up to", "down": "turned the volume down to", "set": "set the volume to"}[action]
    return ToolOutcome("ok", f"Volume is now {new}%.", done=f"{verb} {new}%", data={"level": new}, undo=record)


def _media(args: dict, context: ToolContext) -> ToolOutcome:
    """pause only pauses and play only plays when Windows reports the playback
    state; with no state available the play/pause key is a toggle."""
    desktop, action = context.desktop, args["action"]
    if action in {"pause", "play"}:
        state = desktop.media_state()
        if action == "pause" and state in {"paused", "stopped", "none"}:
            return ToolOutcome("ok", "Nothing is playing, so nothing was paused.", data={"playback": state})
        if action == "play" and state == "playing":
            return ToolOutcome("ok", "Something is already playing.", data={"playback": state})
        if action == "play" and state == "none":
            return ToolOutcome("failed", "No media player is open to resume.", data={"playback": state})
        desktop.media_key("play_pause")
        if state is None:
            return ToolOutcome("unverified", "Pressed play/pause; Windows did not say whether anything was playing.",
                               done=_MEDIA_DONE["play_pause"])
        return ToolOutcome("ok", "Paused the playback." if action == "pause" else "Resumed the playback.",
                           done=_MEDIA_DONE[action])
    desktop.media_key(action)
    return ToolOutcome("ok", "Sent the media key: " + action.replace("_", "/") + ".", done=_MEDIA_DONE[action])


def _brightness_check(args: dict, context: ToolContext):
    if args["action"] == "set" and "level" not in args:
        return clarify("What brightness should I set, from 0 to 100?")
    return None


def _brightness(args: dict, context: ToolContext) -> ToolOutcome:
    desktop, action = context.desktop, args["action"]
    level = desktop.get_brightness()
    if level is None:
        return ToolOutcome("failed", "This display's brightness cannot be changed from Windows; "
                           "external monitors usually need their own buttons.")
    new = args["level"] if action == "set" else level + (1 if action == "up" else -1) * args.get("level", BRIGHTNESS_STEP)
    new = max(0, min(100, int(new)))
    desktop.set_brightness(new)

    def undo() -> str:
        desktop.set_brightness(level)
        return f"put the brightness back to {level}%"
    verb = {"up": "turned the brightness up to", "down": "turned the brightness down to",
            "set": "set the brightness to"}[action]
    return ToolOutcome("ok", f"Brightness is now {new}%.", done=f"{verb} {new}%", data={"level": new},
                       undo=UndoRecord("the brightness change", undo))


def _lock(args: dict, context: ToolContext) -> ToolOutcome:
    context.desktop.lock()
    return ToolOutcome("ok", "Locked the screen.", done="locked the screen")


get_time = Tool(
    name="get_time", group="system", tier=READ, description="Get the current local time and date.",
    parameters={"type": "object", "properties": {}}, run=_time)

get_battery = Tool(
    name="get_battery", group="system", tier=READ, description="Get the battery level and charging state.",
    parameters={"type": "object", "properties": {}}, run=_battery)

volume = Tool(
    name="volume", group="audio", tier=SAFE,
    description="Change the speaker volume: up, down, mute, unmute, or set to a level.",
    parameters={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["up", "down", "mute", "unmute", "set"]},
        "level": {"type": "integer", "minimum": 0, "maximum": 100,
                  "description": "Target level for set, or step size for up/down (default 10)"}},
        "required": ["action"]},
    run=_volume, check=_volume_check)

media = Tool(
    name="media", group="audio", tier=SAFE,
    description="Control the current music or video: pause, play (resume), play_pause, next, previous.",
    parameters={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["pause", "play", "play_pause", "next", "previous"]}},
        "required": ["action"]},
    run=_media)

brightness = Tool(
    name="brightness", group="display", tier=SAFE,
    description="Change the built-in screen brightness: up, down, or set to a level.",
    parameters={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["up", "down", "set"]},
        "level": {"type": "integer", "minimum": 0, "maximum": 100,
                  "description": "Target level for set, or step size for up/down (default 10)"}},
        "required": ["action"]},
    run=_brightness, check=_brightness_check)

# With no arguments at all, Qwen3 4B proposes lock_screen two or more times per
# reply (sometimes until the call limit); one required flag makes it call once.
lock_screen = Tool(
    name="lock_screen", group="power", tier=SAFE, description="Lock the Windows screen.",
    parameters={"type": "object", "properties": {"now": {"type": "boolean", "description": "true to lock now"}},
                "required": ["now"]},
    run=_lock)
