"""Tools for the new action core. Windows effects go through tools.adapter."""

from aqua.agent.registry import Registry


def build_registry(*, lookup: bool = True, wikipedia=None, memory: bool = True) -> Registry:
    """All milestone 1 and 2 tools; ``lookup=False`` leaves out the Wikipedia look_up and
    ``memory=False`` (--no-memory) the remember/forget/recall tools."""
    from .apps import close_app, force_quit_app, launch_app
    from .input import type_text
    from .power import power
    from .settings import open_settings
    from .system import brightness, device_status, get_battery, get_time, lock_screen, media, volume
    from .undo import undo_last
    from .web import open_url, web_search
    from .windows import inspect_window, list_windows, window_action
    from .youtube import youtube
    tools = [launch_app, type_text, undo_last, close_app, force_quit_app, list_windows, window_action,
             inspect_window, get_time, get_battery, device_status, volume, media, brightness, lock_screen, power,
             open_url, web_search, youtube, open_settings]
    if lookup:
        from .knowledge import make_look_up
        tools.append(make_look_up(wikipedia))
    if memory:
        from .memory import forget, recall, remember
        tools += [remember, forget, recall]
    return Registry(tools)
