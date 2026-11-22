"""power: sleep, restart, shut down, sign out. All tier 2: the host asks first.

Restart and shutdown are scheduled 30 seconds out (shutdown /r|/s /t 30), with a
spoken countdown; saying "cancel" during it aborts (shutdown /a).
"""

from aqua.agent.context import ToolContext
from aqua.agent.countdown import COUNTDOWN_SECONDS, Countdown
from aqua.agent.registry import DISRUPTIVE, Tool, ToolOutcome

QUESTIONS = {
    "sleep": "Put the computer to sleep? Say yes or no.",
    "restart": "Restart the computer? Say yes or no.",
    "shutdown": "Shut down the computer? Say yes or no.",
    "sign_out": "Sign out of Windows? Unsaved work may be lost. Say yes or no.",
}
_SPELLING = {"shut_down": "shutdown", "shut down": "shutdown", "reboot": "restart", "log_out": "sign_out",
             "log_off": "sign_out", "logout": "sign_out", "signout": "sign_out", "suspend": "sleep"}


def action_of(args: dict) -> str:
    return _SPELLING.get(args["action"], args["action"])


def _run(args: dict, context: ToolContext) -> ToolOutcome:
    action, desktop = action_of(args), context.desktop
    if action in {"restart", "shutdown"}:
        desktop.schedule_shutdown(action, COUNTDOWN_SECONDS)
        context.countdown = Countdown(action, desktop.monotonic() + COUNTDOWN_SECONDS)
        verb = "Restarting" if action == "restart" else "Shutting down"
        return ToolOutcome("ok", f"{verb} in {COUNTDOWN_SECONDS} seconds; the user can say cancel to stop it.",
                           done=f"{verb.lower()} in {COUNTDOWN_SECONDS} seconds")
    if action == "sleep":
        desktop.suspend()
        return ToolOutcome("ok", "The computer is going to sleep now.", done="putting the computer to sleep")
    desktop.sign_out()
    return ToolOutcome("ok", "Signing out of Windows now.", done="signing you out")


power = Tool(
    name="power", group="power", tier=DISRUPTIVE,
    description="Sleep, restart, shut down or sign out of the computer. Aqua asks the user first.",
    parameters={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["sleep", "restart", "shutdown", "sign_out", *_SPELLING]}},
        "required": ["action"]},
    run=_run,
    confirm=lambda args: QUESTIONS[action_of(args)])
