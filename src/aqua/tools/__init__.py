"""Milestone 1 tools for the new action core. Windows effects go through tools.adapter."""

from aqua.agent.registry import Registry


def build_registry() -> Registry:
    from .apps import launch_app
    from .input import type_text
    from .system import get_battery, get_time, lock_screen, media, volume
    from .undo import undo_last
    from .windows import list_windows, window_action
    return Registry((launch_app, type_text, undo_last, list_windows, window_action,
                     get_time, get_battery, volume, media, lock_screen))
