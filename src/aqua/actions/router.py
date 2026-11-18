"""Retired pre-semantic router; historical compatibility only, not a V0.7 path."""
from .intents import parse_launch_request, is_launch_request
from .launcher import ActionResult, open_application


def route_launch_request(text: str) -> ActionResult | None:
    app_name = parse_launch_request(text)

    if app_name is not None:
        return open_application(app_name)

    if is_launch_request(text):
        return ActionResult(
            success=False,
            message=(
                "That launch request is not approved or is ambiguous. "
                "I can open Notepad, Calculator, VS Code, and Chrome."
            ),
        )

    return None
