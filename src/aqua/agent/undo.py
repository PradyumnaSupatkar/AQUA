"""A short in-memory stack of reversible actions."""

from .registry import ToolOutcome, UndoRecord

MAX_UNDO = 10


class UndoStack:
    def __init__(self, limit: int = MAX_UNDO) -> None:
        self._records: list[UndoRecord] = []
        self.limit = limit

    def __len__(self) -> int:
        return len(self._records)

    def push(self, record: UndoRecord) -> None:
        self._records.append(record)
        del self._records[:-self.limit]

    def peek(self) -> UndoRecord | None:
        return self._records[-1] if self._records else None

    def clear(self) -> None:
        self._records.clear()

    def undo_last(self) -> ToolOutcome:
        if not self._records:
            return ToolOutcome("empty", "There is nothing to undo.")
        record = self._records.pop()
        try:
            done = record.run()
        except Exception:
            return ToolOutcome("failed", f"Could not undo the last action ({record.description}).")
        return ToolOutcome("ok", f"Undid: {record.description}.", done=done or f"undid {record.description}")
