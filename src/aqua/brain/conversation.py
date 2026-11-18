"""Bounded session context, including indivisible tool-call/result turns."""

from copy import deepcopy
import json


DEFAULT_SYSTEM_PROMPT = (
    "You are Aqua, a local, privacy-first Windows voice assistant using Qwen. "
    "Be natural, helpful, concise, and honest about uncertainty. Reply in the "
    "user's language without internal reasoning or thought tags. Available "
    "capabilities are supplied by the host. Tool calls are proposals: only "
    "verified host results establish what happened. Never invent completed "
    "actions or claim application readiness from process creation alone. "
    "Answer well-supported stable facts directly. For changing, private, or "
    "unavailable information, state what you cannot verify instead of inventing "
    "details. User statements are session context, not independently verified "
    "external facts; correct mistakes when given new evidence. Never claim web, "
    "screen, or file access without a verified result establishing that access."
)


def _json_copy(value):
    """Only retain JSON values; no shared nested arguments or NaN/Infinity."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("Tool history must contain finite JSON data.") from exc


def _result_object(raw: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate tool-result field.")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError("Nonfinite tool-result value.")

    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise ValueError("Tool-result content must be a structured host result.") from exc
    if type(value) is not dict:
        raise ValueError("Tool-result content must be a structured host result.")
    return value


def _message_size(message: dict) -> int:
    # Keep the original plain-chat character heuristic, also counting tool data.
    return len(message["content"]) + sum(
        len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        for key, value in message.items() if key not in {"role", "content"}
    )


class Conversation:
    """Keep whole user turns, never orphaning a tool request or its result.

    Plain commit remains transactional and omits an oversized answer as before.
    Tool turns must fit before execution using can_store_tool_turn; their commit
    raises instead of silently losing an external action record. A later final
    answer failure cannot erase an already committed tool result.
    """

    def __init__(self, max_turns: int = 8, max_chars: int = 12000,
                 system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> None:
        for name, value in (("max_turns", max_turns), ("max_chars", max_chars)):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("The system prompt must contain text.")
        prompt = system_prompt.strip()
        if max_chars <= len(prompt):
            raise ValueError("max_chars must leave space beyond the system prompt.")
        self._max_turns, self._max_chars = max_turns, max_chars
        self._system_prompt = prompt
        self._turns: list[list[dict]] = []
        self._action_state = None
        # The shared controller owns this in-memory task-progress value. It is
        # deliberately independent of model messages and never persisted.
        self.last_task = None
        self._desktop_observer = None
        self._desktop_interaction = None
        self.text_entry_apps = ()  # Explicit session opt-in; no default write capability.
        self.text_append_apps = ()  # Separate permission; does not broaden empty entry.

    @property
    def desktop_interaction(self):
        if self._desktop_interaction is None:
            from aqua.desktop.interaction_session import InteractionSession
            self._desktop_interaction = InteractionSession(self.desktop_observer)
        return self._desktop_interaction

    @property
    def desktop_observer(self):
        """Lazy, in-memory session references; importing chat never loads UIA."""
        if self._desktop_observer is None:
            from aqua.desktop.session import ObservationSession
            self._desktop_observer = ObservationSession()
        return self._desktop_observer

    @desktop_observer.setter
    def desktop_observer(self, observer):
        self._desktop_observer = observer

    @property
    def action_state(self):
        """One shared authorization state, without importing tools for plain chat."""
        if self._action_state is None:
            from aqua.actions.tools import ActionState
            self._action_state = ActionState()
        return self._action_state

    def bind_action_state(self, state) -> None:
        """Use the caller's existing state without copying or rearming it."""
        from aqua.actions.tools import ActionState
        if not isinstance(state, ActionState):
            raise ValueError("The conversation requires the shared action state type.")
        if self._action_state is not None and self._action_state is not state:
            self._action_state.clear()
        self._action_state = state

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    @property
    def history(self) -> list[dict]:
        """Return independent nested dictionaries, excluding the system prompt."""
        return deepcopy([message for turn in self._turns for message in turn])

    @property
    def verified_results(self) -> list[dict]:
        """Host-authored results, including denials and pending proposals."""
        return [_result_object(message["content"]) for turn in self._turns
                for message in turn if message["role"] == "tool"]

    def _prompt(self, system_context: str | None) -> str:
        if system_context is None:
            return self._system_prompt
        if not isinstance(system_context, str):
            raise ValueError("Trusted system context must be text.")
        context = system_context.strip()
        return self._system_prompt + "\n\n" + context if context else self._system_prompt

    def _user_text(self, user_text: str) -> str:
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError("A conversation turn requires nonblank text.")
        text = user_text.strip()
        if len(self._system_prompt) + len(text) > self._max_chars:
            raise ValueError("The message exceeds the conversation character limit.")
        return text

    @staticmethod
    def _turn_size(turn: list[dict]) -> int:
        return sum(_message_size(message) for message in turn)

    def _prune(self) -> None:
        size = len(self._system_prompt) + sum(self._turn_size(turn) for turn in self._turns)
        while len(self._turns) > self._max_turns or size > self._max_chars:
            size -= self._turn_size(self._turns.pop(0))

    def messages_for(self, user_text: str, *, system_context: str | None = None,
                     current_task_only: bool = False) -> list[dict]:
        """Build a bounded request; task-local review never erases session history."""
        text, prompt = self._user_text(user_text), self._prompt(system_context)
        if len(prompt) + len(text) > self._max_chars:
            raise ValueError("The message and system context exceed the conversation character limit.")
        turns = [] if current_task_only else list(self._turns)
        size = len(prompt) + len(text) + sum(self._turn_size(turn) for turn in turns)
        while turns and size > self._max_chars:
            size -= self._turn_size(turns.pop(0))
        return deepcopy([{"role": "system", "content": prompt},
                         *[message for turn in turns for message in turn],
                         {"role": "user", "content": text}])

    def messages_for_tool_result(self, *, system_context: str | None = None,
                                 current_task_only: bool = False) -> list[dict]:
        """Continue the latest tool turn without inventing another user message."""
        if not self._turns or self._turns[-1][-1]["role"] != "tool":
            raise ValueError("There is no completed tool result awaiting an answer.")
        prompt = self._prompt(system_context)
        # A recovered self-contained task retains its ENTIRE current tool turn.
        # Earlier completed turns remain in memory and normal chat still sees them.
        turns = list(self._turns[-1:] if current_task_only else self._turns)
        size = len(prompt) + sum(self._turn_size(turn) for turn in turns)
        while len(turns) > 1 and size > self._max_chars:
            size -= self._turn_size(turns.pop(0))
        if size > self._max_chars:
            raise ValueError("The tool result and system context exceed the conversation character limit.")
        return deepcopy([{"role": "system", "content": prompt},
                         *[message for turn in turns for message in turn]])

    def commit(self, user_text: str, assistant_text: str) -> None:
        """Store a complete plain response, preserving legacy oversize behavior."""
        user = self._user_text(user_text)
        if not isinstance(assistant_text, str) or not assistant_text.strip():
            raise ValueError("Only a nonblank completed answer can be retained.")
        turn = [{"role": "user", "content": user},
                {"role": "assistant", "content": assistant_text.strip()}]
        if len(self._system_prompt) + self._turn_size(turn) > self._max_chars:
            return
        self._turns.append(turn)
        self._prune()

    def _tool_bundle(self, user_text, assistant_message, tool_results, final_text=None) -> list[dict]:
        user = self._user_text(user_text)
        if type(assistant_message) is not dict or set(assistant_message) - {"role", "content", "tool_calls"}:
            raise ValueError("A tool turn requires an assistant tool-call message.")
        assistant = _json_copy(assistant_message)
        calls = assistant.get("tool_calls")
        if assistant.get("role") != "assistant" or not isinstance(assistant.get("content"), str) or type(calls) is not list or not 1 <= len(calls) <= 16:
            raise ValueError("A tool turn requires assistant text and bounded tool calls.")
        if type(tool_results) is not list or len(tool_results) != len(calls):
            raise ValueError("Every tool call must have exactly one matching result.")
        results = []
        for call, result in zip(calls, tool_results):
            if type(call) is not dict or set(call) - {"function", "type", "id", "index"} or call.get("type", "function") != "function":
                raise ValueError("Invalid tool-call history envelope.")
            function = call.get("function")
            if type(function) is not dict or set(function) - {"name", "arguments", "index"}:
                raise ValueError("Invalid tool-call function history.")
            name = function.get("name")
            if not isinstance(name, str) or not name.strip() or type(function.get("arguments")) is not dict:
                raise ValueError("Tool-call history requires a name and argument object.")
            if "id" in call and (not isinstance(call["id"], str) or not call["id"].strip()):
                raise ValueError("Invalid tool-call history identifier.")
            for envelope in (call, function):
                if "index" in envelope and (type(envelope["index"]) is not int or envelope["index"] < 0):
                    raise ValueError("Invalid tool-call history index.")
            if type(result) is not dict or set(result) - {"role", "content", "tool_name", "tool_call_id"}:
                raise ValueError("Invalid tool-result history envelope.")
            copied = _json_copy(result)
            if copied.get("role") != "tool" or copied.get("tool_name") != name or not isinstance(copied.get("content"), str):
                raise ValueError("Tool results must match their assistant calls in order.")
            _result_object(copied["content"])
            if "tool_call_id" in copied and copied["tool_call_id"] != call.get("id"):
                raise ValueError("Tool-result identifiers must match their assistant calls.")
            results.append(copied)
        turn = [{"role": "user", "content": user}, assistant, *results]
        if final_text is not None:
            if not isinstance(final_text, str) or not final_text.strip():
                raise ValueError("Only a nonblank completed final answer can be retained.")
            turn.append({"role": "assistant", "content": final_text.strip()})
        return turn

    def can_store_tool_turn(self, user_text, assistant_message: dict, tool_results: list[dict], final_text=None, *, system_context=None) -> bool:
        """Preflight bounded host results before authorizing an external effect."""
        turn = self._tool_bundle(user_text, assistant_message, tool_results, final_text)
        return len(self._prompt(system_context)) + self._turn_size(turn) <= self._max_chars

    def commit_tool_turn(self, user_text, assistant_message: dict, tool_results: list[dict], final_text=None) -> None:
        """Commit call/result atomically; never silently omit an actual action."""
        turn = self._tool_bundle(user_text, assistant_message, tool_results, final_text)
        if len(self._system_prompt) + self._turn_size(turn) > self._max_chars:
            raise ValueError("The complete tool turn exceeds the conversation character limit.")
        self._turns.append(turn)
        self._prune()

    def _extended_tool_turn(self, assistant_message: dict, tool_results: list[dict]) -> list[dict]:
        if not self._turns or self._turns[-1][-1]["role"] != "tool":
            raise ValueError("There is no active tool turn to extend.")
        # Reuse the complete call/result validator, removing only the synthetic
        # user prefix used for validation. A task always retains its real user
        # message exactly once, followed by all paired exchanges in order.
        exchange = self._tool_bundle(self._turns[-1][0]["content"], assistant_message, tool_results)[1:]
        return [*self._turns[-1], *exchange]

    def can_append_tool_step(self, assistant_message: dict, tool_results: list[dict], *, system_context=None) -> bool:
        """Reserve the entire active task before another external operation.

        Older turns may later be pruned, but no part of the current task can be
        dropped merely to fit its next operation or fresh trusted context.
        """
        turn = self._extended_tool_turn(assistant_message, tool_results)
        return len(self._prompt(system_context)) + self._turn_size(turn) <= self._max_chars

    def append_tool_step(self, assistant_message: dict, tool_results: list[dict]) -> None:
        """Commit another paired tool exchange to the same bounded user turn."""
        turn = self._extended_tool_turn(assistant_message, tool_results)
        if len(self._system_prompt) + self._turn_size(turn) > self._max_chars:
            raise ValueError("The active tool turn exceeds the conversation character limit.")
        self._turns[-1] = turn
        self._prune()

    def complete_last_turn(self, assistant_text: str) -> bool:
        """Append a final reply, retaining the action record if the reply is huge."""
        if not isinstance(assistant_text, str) or not assistant_text.strip():
            raise ValueError("Only a nonblank completed final answer can be retained.")
        if not self._turns or self._turns[-1][-1]["role"] != "tool":
            raise ValueError("There is no tool turn awaiting a final answer.")
        answer = {"role": "assistant", "content": assistant_text.strip()}
        if len(self._system_prompt) + self._turn_size(self._turns[-1]) + _message_size(answer) > self._max_chars:
            return False
        self._turns[-1].append(answer)
        self._prune()
        return True

    def replace_last_tool_result(self, message: dict) -> None:
        """Finalize a pre-journaled write outcome without orphaning its call."""
        if not self._turns or self._turns[-1][-1]["role"] != "tool":
            raise ValueError("No active tool outcome can be finalized.")
        turn = self._turns[-1]
        assistant = turn[-2]
        if assistant.get("role") != "assistant":
            raise ValueError("The latest outcome must have one paired call.")
        validated = self._tool_bundle(turn[0]["content"], assistant, [message])[-1]
        candidate = [*turn[:-1], validated]
        if len(self._system_prompt) + self._turn_size(candidate) > self._max_chars:
            raise ValueError("Final tool outcome exceeds the reserved history budget.")
        self._turns[-1] = candidate

    def clear(self) -> None:
        """Forget conversation, action records, and pending authorization together."""
        self._turns.clear()
        self.last_task = None
        if self._desktop_observer is not None:
            self._desktop_observer.clear()
        if self._desktop_interaction is not None:
            self._desktop_interaction.clear()
        if self._action_state is not None:
            self._action_state.clear()
