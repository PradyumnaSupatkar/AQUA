"""Small, immutable task ledger. It is evidence, never execution permission."""

from dataclasses import dataclass, replace

MAX_TOOL_OPERATIONS = 5
MAX_MODEL_DECISIONS = 6


@dataclass(frozen=True, slots=True)
class Operation:
    app_id: str
    status: str
    process_created: bool | None
    call_id: str | None = None
    tool_name: str = "open_application"
    target: str | None = None
    attempted: bool | None = None
    verified: bool = False


@dataclass(frozen=True, slots=True)
class TaskProgress:
    request: str
    authorized_apps: tuple[str, ...] = ()
    decisions: int = 0
    operations: tuple[Operation, ...] = ()
    status: str = "running"
    stop_reason: str | None = None
    observation_apps: tuple[str, ...] = ()
    observation_requires_inspection: bool = False
    text_entry_app: str | None = None
    launch_before_entry: bool = False

    @property
    def attempted_apps(self) -> tuple[str, ...]:
        return tuple(operation.app_id for operation in self.operations if operation.tool_name == "open_application")

    @property
    def remaining_apps(self) -> tuple[str, ...]:
        return tuple(app for app in self.authorized_apps if app not in self.attempted_apps)

    @property
    def completed_operations(self) -> tuple[Operation, ...]:
        return tuple(op for op in self.operations if (op.status == "launch_attempt_succeeded" and op.process_created is True)
                     or op.status in {"observation_succeeded", "text_entry_verified"})

    @property
    def failed_operations(self) -> tuple[Operation, ...]:
        return tuple(op for op in self.operations if op.status in {"launch_failed", "observation_failed", "text_entry_failed"})

    @property
    def unknown_operations(self) -> tuple[Operation, ...]:
        return tuple(op for op in self.operations if op.status in {"launch_outcome_unknown", "text_entry_unknown"})

    def decided(self):
        return replace(self, decisions=self.decisions + 1)

    def recorded(self, operation: Operation):
        return replace(self, operations=(*self.operations, operation))

    def stopped(self, status: str, reason: str | None = None):
        return replace(self, status=status, stop_reason=reason)

    def summary(self) -> str:
        """Only host records may describe the outcome of an action task."""
        if self.text_entry_app:
            writes = [op for op in self.operations if op.tool_name == "set_control_text"]
            if any(op.verified for op in writes):
                message = f"Text entry into the observed empty Edit or Document control in {self.text_entry_app} was verified by UIA readback. No save, keys or other action was performed."
            elif not writes:
                message = "No text-entry operation was performed; no entry was verified. An observed unique editable ValuePattern Edit or Document control is required."
            elif writes[-1].attempted is False:
                message = "Text entry was rejected before SetValue; no write was attempted. The destination may be nonempty, stale, protected or unsupported."
            else:
                message = "Text entry may have been attempted, but its outcome was not verified. No automatic retry was made; check the destination manually."
            launches = [op for op in self.operations if op.tool_name == "open_application"]
            if launches:
                launch = launches[-1]
                detail = ("process creation reported; readiness was not established by launch" if launch.status == "launch_attempt_succeeded"
                          else "launch failed" if launch.status == "launch_failed" else "launch outcome unknown")
                message = f"Launch attempt for {launch.app_id}: {detail}.\n" + message
            return message + ("\nStopped: " + self.stop_reason if self.stop_reason else "")
        if self.observation_apps:
            lines = [f"Observation: {len(self.completed_operations)} successful read(s), {len(self.failed_operations)} failed."]
            if self.stop_reason:
                lines.append("Stopped: " + self.stop_reason)
            return "\n".join(lines)
        if not self.operations:
            lines = ["Task result: No tool operations were performed."]
        else:
            lines = [f"Task result: {len(self.completed_operations)} successful launch attempt(s), "
                     f"{len(self.failed_operations)} failed, {len(self.unknown_operations)} unknown."]
            for operation in self.operations:
                if operation.status == "launch_attempt_succeeded":
                    detail = "process creation reported; application readiness was not checked"
                elif operation.status == "launch_failed":
                    detail = "launch failed; no successful process creation was reported"
                else:
                    detail = "launch outcome unknown; no automatic retry"
                lines.append(f"- {operation.app_id}: {detail}.")
        if self.remaining_apps:
            lines.append("Not attempted: " + ", ".join(self.remaining_apps) + ".")
        if self.stop_reason:
            lines.append("Stopped: " + self.stop_reason)
        return "\n".join(lines)
