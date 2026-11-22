"""Tool declarations, a small JSON-schema check, and the registry the loop uses.

A tool declares its risk tier in Python. Model-supplied arguments are only
validated data; nothing the model sends can change a tool's tier.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
import json

READ, SAFE, DISRUPTIVE, DESTRUCTIVE = 0, 1, 2, 3
TIERS = (READ, SAFE, DISRUPTIVE, DESTRUCTIVE)


class SchemaError(ValueError):
    """Model arguments do not match a tool's parameter schema."""


@dataclass(frozen=True, slots=True)
class UndoRecord:
    """How to reverse one completed action. ``run`` returns a short past-tense phrase."""
    description: str
    run: Callable[[], str]


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """What a tool reports back. ``done`` is a short past-tense phrase for replies."""
    status: str                     # ok | unverified | failed | empty
    message: str                    # one short line for the model
    done: str = ""                  # e.g. "opened Notepad"; empty when nothing happened
    data: dict = field(default_factory=dict)
    undo: UndoRecord | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in {"ok", "unverified", "empty"}


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    group: str
    description: str
    parameters: dict
    run: Callable[[dict, object], ToolOutcome]
    tier: int | Callable[[dict, object], int] = SAFE
    # Scope rules separate from the tier: return a policy Decision (deny or
    # clarify) or None. Used for blocked apps, ambiguous targets and similar.
    check: Callable[[dict, object], object] | None = None
    # Host-authored confirmation question for tier 2 and 3 actions.
    confirm: Callable[[dict], str] | None = None
    # Tier 3 tools that stay unavailable until the user enables them.
    off_by_default: bool = False
    # Arguments holding the user's own text (e.g. what to type); never sent off the PC.
    private_args: tuple[str, ...] = ()
    # Verify the final reply against this tool's result: return a corrected reply, or None.
    review: Callable[[str, "ToolOutcome"], str | None] | None = None

    def tier_for(self, args: dict, context) -> int:
        tier = self.tier(args, context) if callable(self.tier) else self.tier
        if tier not in TIERS:
            raise ValueError(f"Tool {self.name} returned an invalid risk tier.")
        return tier

    def definition(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


def _coerce(name: str, value, spec: dict):
    kind = spec.get("type", "string")
    if kind == "string":
        if not isinstance(value, str):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value = str(value)
            else:
                raise SchemaError(f"{name} must be text.")
        if "enum" in spec:
            folded = value.strip().casefold()
            for option in spec["enum"]:
                if option.casefold() == folded:
                    return option
            raise SchemaError(f"{name} must be one of: {', '.join(spec['enum'])}.")
        if len(value) > spec.get("maxLength", 2000):
            raise SchemaError(f"{name} is too long.")
        if len(value.strip()) < spec.get("minLength", 0):
            raise SchemaError(f"{name} must not be empty.")
        return value
    if kind in {"integer", "number"}:
        if isinstance(value, str):
            try:
                value = float(value.strip().rstrip("%"))
            except ValueError:
                raise SchemaError(f"{name} must be a number.") from None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
            raise SchemaError(f"{name} must be a number.")
        if kind == "integer":
            if float(value) != int(value):
                raise SchemaError(f"{name} must be a whole number.")
            value = int(value)
        if "minimum" in spec and value < spec["minimum"] or "maximum" in spec and value > spec["maximum"]:
            raise SchemaError(f"{name} must be between {spec.get('minimum')} and {spec.get('maximum')}.")
        return value
    if kind == "boolean":
        if isinstance(value, str) and value.strip().casefold() in {"true", "false"}:
            return value.strip().casefold() == "true"
        if not isinstance(value, bool):
            raise SchemaError(f"{name} must be true or false.")
        return value
    raise SchemaError(f"{name} has an unsupported type.")


def validate_arguments(schema: dict, arguments) -> dict:
    """Check and lightly normalize arguments. Unknown keys are dropped, and an
    empty optional string counts as missing (small models often send "")."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except ValueError:
            raise SchemaError("Arguments must be a JSON object.") from None
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise SchemaError("Arguments must be a JSON object.")
    properties = schema.get("properties", {})
    required = set(schema.get("required", ()))
    result = {}
    for name, spec in properties.items():
        if name not in arguments or arguments[name] is None:
            continue
        value = arguments[name]
        if name not in required and isinstance(value, str) and not value.strip():
            continue
        result[name] = _coerce(name, value, spec)
    missing = sorted(required - set(result))
    if missing:
        raise SchemaError("Missing " + ", ".join(missing) + ".")
    return result


class Registry:
    def __init__(self, tools=()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name} is already registered.")
        if tool.parameters.get("type") != "object":
            raise ValueError(f"Tool {tool.name} parameters must be an object schema.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name) if isinstance(name, str) else None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def in_group(self, group: str) -> tuple[str, ...]:
        return tuple(name for name, tool in self._tools.items() if tool.group == group)

    def definitions(self, names) -> list[dict]:
        return [self._tools[name].definition() for name in names if name in self._tools]
