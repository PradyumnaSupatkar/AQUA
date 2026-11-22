"""undo_last: reverse the most recent reversible action."""

from aqua.agent.context import ToolContext
from aqua.agent.registry import SAFE, Tool, ToolOutcome


def _undo(args: dict, context: ToolContext) -> ToolOutcome:
    return context.undo.undo_last()


undo_last = Tool(
    name="undo_last", group="undo", tier=SAFE,
    description="Undo Aqua's most recent action (volume, window change, or an app it opened). Use for 'undo that'.",
    parameters={"type": "object", "properties": {}}, run=_undo)
