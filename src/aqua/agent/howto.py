"""Short how-to answers for "how do I ...?" questions the model refused to answer.

Each entry is (how the user does it, what Aqua can do instead). The host uses one only when
the model's reply is a refusal ("I don't know how to lock the screen"), never otherwise.
"""

import re

HOW_TO = {
    "lock_screen": ("Press Windows + L.", "lock it"),
    "volume": ("Use the volume keys, or click the speaker icon on the taskbar.", "change the volume"),
    "brightness": ("Use the brightness keys, or open Quick Settings with Windows + A.", "change the brightness"),
    "media": ("Press the play/pause key, or use the media controls in Quick Settings.", "control the music"),
    "power": ("Open Start and click the power button.", "do that"),
    "close_app": ("Press Alt + F4 in that app.", "close it"),
    "force_quit_app": ("Press Ctrl + Shift + Esc to open Task Manager, select the app and click End task.",
                       "force it to quit"),
    "window_action": ("Use the buttons at the top right of the window, or Windows + the arrow keys.", "do that"),
    "launch_app": ("Press the Windows key, type the app's name and press Enter.", "open it"),
    "type_text": ("Click where the text should go and type it.", "type it"),
    "web_search": ("Open your browser and type what you want into the address bar.", "search for it"),
    "open_url": ("Open your browser and type the address into the address bar.", "open it"),
}

_REFUSAL = re.compile(
    r"\b(?:i\s+(?:do\s+not|don'?t)\s+know\s+how|i'?m\s+not\s+sure\s+how|i\s+am\s+not\s+sure\s+how|"
    r"i\s+(?:can(?:not|'t|’t)|am\s+unable\s+to|am\s+not\s+able\s+to)\s+(?:help|explain|tell|answer|show)|"
    r"i\s+(?:don'?t|do\s+not)\s+have\s+(?:information|instructions|a\s+way))\b", re.I)


def is_refusal(reply: str) -> bool:
    return bool(_REFUSAL.search(reply))


def how_to_answer(tool_names) -> str | None:
    """The answer for the first shown tool that has one: "Press Windows + L. I can also lock it
    for you if you want." """
    for name in tool_names:
        if name in HOW_TO:
            how, can = HOW_TO[name]
            return f"{how} I can also {can} for you if you want."
    return None
