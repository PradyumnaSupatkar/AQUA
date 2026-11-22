"""Tools for the new action core. Windows effects go through tools.adapter."""

from aqua.agent.registry import Registry


def build_registry(*, lookup: bool = True, wikipedia=None) -> Registry:
    """All milestone 1 and 2 tools; ``lookup=False`` leaves out the Wikipedia look_up."""
    from .apps import close_app, force_quit_app, launch_app
    from .input import type_text
    from .power import power
    from .system import brightness, device_status, get_battery, get_time, lock_screen, media, volume
    from .undo import undo_last
    from .web import open_url, web_search
    from .windows import inspect_window, list_windows, window_action
    tools = [launch_app, type_text, undo_last, close_app, force_quit_app, list_windows, window_action,
             inspect_window, get_time, get_battery, device_status, volume, media, brightness, lock_screen, power,
             open_url, web_search]
    if lookup:
        from .knowledge import make_look_up
        tools.append(make_look_up(wikipedia))
    return Registry(tools)
