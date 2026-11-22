"""The single agent loop: select tools, let the model propose, enforce, run, repeat.

request -> select tool groups -> model proposes call -> schema check -> policy
-> run -> verify -> result to model -> ... -> the model answers in words.
"""

from dataclasses import dataclass, field
from datetime import datetime
import json
import re
from time import perf_counter

from aqua.brain.conversation import Conversation
from aqua.brain.filtering import AnswerChannelGuard
from aqua.brain.ollama import ChatEvent, GenerationSettings, OllamaError, OllamaProtocolError

from .confirm import ConfirmationGate, PendingAction
from .context import ToolContext
from .policy import CLARIFY, CONFIRM, DENY, Policy
from .prompt import SYSTEM_PROMPT, user_message
from .registry import Registry, SchemaError, ToolOutcome, validate_arguments
from .selection import matched_groups, select_tools

MAX_OPERATIONS = 12
MAX_DECISIONS = 14

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002B00-\U00002BFF"
    "\U0000FE0F\U0000200D\U000020E3]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Statuses of operations that actually ran; only these count toward MAX_OPERATIONS.
_EXECUTED = frozenset({"ok", "unverified", "failed", "empty"})

# Past-tense action claims and the tools whose results can back them up. Used
# only to catch a reply that says something happened when no tool did it; it
# never authorizes or blocks an action.
_CLAIMS = (
    (re.compile(r"\b(?:opened|launched|started|brought)\b", re.I), {"launch_app", "window_action"}),
    (re.compile(r"\b(?:typed|wrote|written|entered)\b", re.I), {"type_text"}),
    (re.compile(r"\b(?:muted|unmuted|lowered|raised|increased|decreased|turned\s+(?:the\s+)?(?:volume|sound|it)|"
                r"set\s+the\s+volume|volume\s+(?:is|has\s+been)\s+(?:now|set|turned))\b", re.I), {"volume"}),
    (re.compile(r"\b(?:paused|resumed|skipped)\b", re.I), {"media"}),
    (re.compile(r"\blocked\b", re.I), {"lock_screen"}),
    (re.compile(r"\b(?:minimi[sz]ed|maximi[sz]ed|restored|focused|switched)\b", re.I), {"window_action", "undo_last"}),
    (re.compile(r"\b(?:undid|undone|reverted|closed|put\s+\w+\s+back)\b", re.I), {"undo_last"}),
)
_NUDGE = ("[Aqua host: no tool was called for that, so it has not happened. If the user asked for an action, "
          "call the tool now. If you cannot, say plainly that you did not do it.]")


_TEXT_CALL = re.compile(r"^\s*(?:<tool_call>\s*)?(\{.*\})\s*(?:</tool_call>)?\s*$", re.S)


def parse_text_tool_call(content: str, visible: tuple[str, ...]) -> dict | None:
    """Qwen sometimes writes a tool call as plain JSON instead of a native call.
    Accept only a whole-reply JSON object naming a tool shown for this request."""
    match = _TEXT_CALL.match(content or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
    except ValueError:
        return None
    if not isinstance(value, dict) or set(value) - {"name", "arguments", "parameters"}:
        return None
    name, arguments = value.get("name"), value.get("arguments", value.get("parameters", {}))
    if name not in visible or not isinstance(arguments, dict):
        return None
    return {"function": {"name": name, "arguments": arguments}}


def unsupported_claim(reply: str, attempted: set[str]) -> bool:
    """True when the reply claims an action that no attempted tool in this turn covers."""
    return any(pattern.search(reply) and not (tools & attempted) for pattern, tools in _CLAIMS)


def strip_emoji(text: str) -> str:
    return _EMOJI.sub("", text)


def first_sentence(text: str) -> str:
    text = " ".join(strip_emoji(text).split())
    return _SENTENCE_END.split(text, 1)[0].strip() if text else ""


def join_phrases(phrases: list[str]) -> str:
    """["opened Notepad", "typed your text"] -> "Opened Notepad and typed your text." """
    phrases = [p for p in phrases if p]
    if not phrases:
        return ""
    text = phrases[0] if len(phrases) == 1 else ", ".join(phrases[:-1]) + " and " + phrases[-1]
    return text[0].upper() + text[1:] + "."


@dataclass(frozen=True, slots=True)
class Operation:
    tool: str
    args: dict
    status: str                 # ok | unverified | failed | empty | denied | invalid | not_run
    message: str = ""
    done: str = ""


@dataclass(frozen=True, slots=True)
class AgentTask:
    request: str
    decisions: int = 0
    operations: tuple[Operation, ...] = ()
    status: str = "running"
    stop_reason: str | None = None

    @property
    def completed_operations(self):
        return tuple(op for op in self.operations if op.status in {"ok", "empty"})

    @property
    def failed_operations(self):
        return tuple(op for op in self.operations if op.status in {"failed", "denied", "invalid", "not_run"})

    @property
    def unknown_operations(self):
        return tuple(op for op in self.operations if op.status == "unverified")


@dataclass(frozen=True, slots=True)
class AgentTurnResult:
    """Field-compatible with the legacy TurnResult that the front ends render."""
    ok: bool
    text: str
    outcome: str                # answer | action | confirm | clarify | declined | limit | error
    requests: int
    total_seconds: float
    first_token_seconds: float | None
    completion: ChatEvent | None
    stage: str
    error: str | None = None
    task: AgentTask | None = None
    tool_result: None = None

    @property
    def asked_question(self) -> bool:
        return self.outcome in {"confirm", "clarify"}


@dataclass(slots=True)
class _Turn:
    request: str
    tool_names: tuple
    messages: list
    decisions: int = 0
    operations: list = field(default_factory=list)
    start: int = 0              # Index of this request's first tool exchange in ``messages``.


class AgentEngine:
    def __init__(self, client, *, model: str, settings: GenerationSettings, registry: Registry,
                 context: ToolContext, conversation: Conversation | None = None,
                 clock=perf_counter, now=None) -> None:
        self.client, self.model, self.settings = client, model, settings
        self.registry, self.context = registry, context
        self.conversation = conversation if conversation is not None else Conversation(system_prompt=SYSTEM_PROMPT)
        self.gate = ConfirmationGate()
        self.clock = clock
        self.now = now if now is not None else (lambda: datetime.now().astimezone())

    @property
    def pending(self) -> PendingAction | None:
        return self.gate.pending

    def cancel_pending(self) -> None:
        self.gate.clear()

    def clear(self) -> None:
        self.gate.clear()
        self.conversation.clear()

    # -- one user utterance -------------------------------------------------

    def respond(self, text: str, *, on_text=None) -> AgentTurnResult:
        started = self.clock()
        state = {"requests": 0, "first": None, "completion": None, "spoken": []}

        def emit(chunk: str, *, segment: bool = False) -> None:
            """``segment`` marks a whole host or buffered sentence, spaced from earlier text."""
            chunk = strip_emoji(chunk)
            if not chunk:
                return
            if segment and state["spoken"] and not state["spoken"][-1][-1:].isspace():
                chunk = " " + chunk
            if state["first"] is None:
                state["first"] = self.clock() - started
            state["spoken"].append(chunk)
            if on_text is not None:
                on_text(chunk)

        def result(outcome, stage, turn, *, ok=True, error=None, status="done", reason=None):
            task = None if turn is None else AgentTask(turn.request, turn.decisions, tuple(turn.operations), status, reason)
            return AgentTurnResult(ok, "".join(state["spoken"]).strip(), outcome, state["requests"],
                                   self.clock() - started, state["first"], state["completion"], stage, error, task)

        verdict, pending = self.gate.answer(text, self.clock())
        if verdict == "no" and pending is not None:
            emit("Okay, I won't.")
            self._remember(text, "Okay, I won't.")
            turn = _Turn(pending.request, pending.tool_names, [], pending.decisions, list(pending.operations))
            return result("declined", "confirmation", turn, status="declined")
        if verdict == "yes" and pending is not None:
            turn = _Turn(pending.request, pending.tool_names, pending.messages, pending.decisions,
                         list(pending.operations), pending.start)
            tool = self.registry.get(pending.tool)
            decision = self.context.policy.decide(tool, pending.args, self.context)
            if decision.action == DENY:
                outcome = ToolOutcome("failed", "Not done: " + decision.reason)
                turn.operations.append(Operation(tool.name, pending.args, "denied", decision.reason))
            else:
                outcome = self._run(tool, pending.args)
                turn.operations.append(Operation(tool.name, pending.args, outcome.status, outcome.message, outcome.done))
            turn.messages.append(self._tool_message(tool.name, outcome))
            return self._loop(turn, text, emit, result, state)

        text = text.strip()
        self.context.begin_request()
        names = select_tools(text, self.registry)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *self.conversation.history,
                    {"role": "user", "content": user_message(text, self.now(), undoable=self._undoable())}]
        return self._loop(_Turn(text, names, messages, start=len(messages)), text, emit, result, state)

    # -- the loop -------------------------------------------------------------

    def _loop(self, turn: _Turn, utterance: str, emit, result, state) -> AgentTurnResult:
        definitions = self.registry.definitions(turn.tool_names) or None
        first_new = len(turn.operations)  # Operations before this point were reported earlier.
        # A request that looks like a command is answered in one checked sentence,
        # so it is buffered; plain conversation streams live.
        command_like = bool(turn.tool_names) and bool(matched_groups(turn.request))
        nudged = False
        while True:
            acted = bool(turn.operations)
            live = not acted and not command_like
            if turn.decisions >= MAX_DECISIONS:
                return self._stop_at_limit(turn, utterance, emit, result, state)
            turn.decisions += 1
            try:
                content, completion = self._model_round(turn.messages, definitions, emit if live else None)
            except OllamaError as exc:
                state["completion"] = None
                summary = join_phrases([op.done for op in turn.operations])
                if summary:
                    emit(summary, segment=True)  # Say what already happened before the failure.
                return result("error", "model", turn, ok=False, error=str(exc), status="error")
            finally:
                state["requests"] += 1
            state["completion"] = completion
            calls = list(completion.tool_calls)
            if not calls and not live:
                text_call = parse_text_tool_call(content, turn.tool_names)
                if text_call is not None:
                    calls, content = [text_call], ""
            if not calls:
                if live:
                    if not "".join(state["spoken"]).strip():
                        emit("Sorry, I didn't get a reply. Please try again.")
                        return result("error", "model", turn, ok=False, error="The model returned an empty reply.",
                                      status="error")
                else:
                    reply = first_sentence(content) if acted else " ".join(strip_emoji(content).split())
                    attempted = {op.tool for op in turn.operations if op.status != "invalid"}
                    if turn.tool_names and unsupported_claim(reply, attempted):
                        if not nudged:
                            # The model said it did something no tool did. Ask once more.
                            nudged = True
                            turn.messages += [{"role": "assistant", "content": content}, {"role": "user", "content": _NUDGE}]
                            continue
                        done = join_phrases([op.done for op in turn.operations])
                        reply = (done + " " if done else "") + "Sorry, I couldn't do " + ("the rest." if done else "that.")
                    if not reply:
                        reply = join_phrases([op.done for op in turn.operations]) or (
                            "Done." if acted else "Sorry, I didn't get a reply. Please try again.")
                    emit(reply, segment=True)
                self._remember(utterance, "".join(state["spoken"]), turn)
                return result("action" if acted else "answer", "complete", turn)
            for index, call in enumerate(calls):
                function = call.get("function", {})
                name, raw_args = function.get("name"), function.get("arguments")
                # One assistant message per call keeps every result unambiguous
                # for Ollama, even when the model repeats the same tool.
                turn.messages.append({"role": "assistant", "content": content if index == 0 else "",
                                      "tool_calls": [call]})
                if sum(op.status in _EXECUTED for op in turn.operations) >= MAX_OPERATIONS:
                    turn.messages.append(self._tool_message(name, ToolOutcome("failed", "Not run: the step limit was reached.")))
                    turn.operations.append(Operation(str(name), {}, "not_run", "operation limit"))
                    return self._stop_at_limit(turn, utterance, emit, result, state)
                stop = self._handle_call(turn, name, raw_args)
                if stop is not None:
                    kind, question = stop
                    done = join_phrases([op.done for op in turn.operations[first_new:]])
                    emit((done + " " if done else "") + question, segment=True)
                    self._remember(utterance, "".join(state["spoken"]))
                    return result(kind, kind, turn, status="waiting")
            self._remind(turn)

    def _undoable(self) -> str | None:
        record = self.context.undo.peek()
        return record.description if record is not None else None

    @staticmethod
    def _remind(turn: _Turn) -> None:
        """Restate the request on the newest result, so a small model finishes
        multi-step requests instead of stopping after the first step."""
        message = turn.messages[-1]
        if message.get("role") != "tool":
            return
        body = json.loads(message["content"])
        body["next"] = (f"The user's request was: {turn.request!r}. If any part of it is not done yet, "
                        "call the tool for it now. Otherwise reply in one sentence.")
        message["content"] = json.dumps(body, ensure_ascii=False)

    def _handle_call(self, turn: _Turn, name, raw_args):
        tool = self.registry.get(name)
        if tool is None or tool.name not in turn.tool_names:
            # Only tools shown for this request can run; visibility is not permission,
            # but invisibility is a firm no.
            turn.messages.append(self._tool_message(str(name), ToolOutcome("failed", f"Unknown tool {name}.")))
            turn.operations.append(Operation(str(name), {}, "invalid", "unknown tool"))
            return None
        try:
            args = validate_arguments(tool.parameters, raw_args)
        except SchemaError as exc:
            turn.messages.append(self._tool_message(tool.name, ToolOutcome("failed", f"Invalid arguments: {exc}")))
            turn.operations.append(Operation(tool.name, {}, "invalid", str(exc)))
            return None
        if any(op.tool == tool.name and op.args == args and op.status in {"ok", "unverified"} for op in turn.operations):
            # Small models sometimes repeat a finished step; never do the same thing twice in one request.
            turn.messages.append(self._tool_message(tool.name, ToolOutcome("ok", "Already done; not repeated.")))
            turn.operations.append(Operation(tool.name, args, "duplicate", "repeated call skipped"))
            return None
        decision = self.context.policy.decide(tool, args, self.context)
        if decision.action == DENY:
            turn.messages.append(self._tool_message(tool.name, ToolOutcome("failed", "Not done: " + decision.reason)))
            turn.operations.append(Operation(tool.name, args, "denied", decision.reason))
            return None
        if decision.action == CLARIFY:
            turn.messages.pop()  # The question replaces this call; the user's answer is a new request.
            return "clarify", decision.question
        if decision.action == CONFIRM:
            self.gate.offer(PendingAction(tool.name, args, decision.question, self.clock(), turn.request,
                                          turn.messages, turn.tool_names, turn.decisions, list(turn.operations),
                                          start=turn.start))
            return "confirm", decision.question
        outcome = self._run(tool, args)
        turn.messages.append(self._tool_message(tool.name, outcome))
        turn.operations.append(Operation(tool.name, args, outcome.status, outcome.message, outcome.done))
        return None

    def _run(self, tool, args) -> ToolOutcome:
        try:
            outcome = tool.run(args, self.context)
        except Exception:
            return ToolOutcome("failed", f"{tool.name} failed unexpectedly.")
        if outcome.undo is not None and outcome.succeeded:
            self.context.undo.push(outcome.undo)
        return outcome

    def _model_round(self, messages, definitions, stream_to):
        """One model decision. Text streams live only before any tool has run;
        afterwards it is buffered so only one sentence is spoken."""
        guard, parts, completion = AnswerChannelGuard(), [], None
        iterator = self.client.stream_chat(messages, model=self.model, settings=self.settings, tools=definitions)
        try:
            for event in iterator:
                if event.content:
                    visible = guard.feed(event.content)
                    if visible:
                        parts.append(visible)
                        if stream_to is not None:
                            stream_to(visible)
                if event.done:
                    completion = event
            tail = guard.finish()
            if tail:
                parts.append(tail)
                if stream_to is not None:
                    stream_to(tail)
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()
        if completion is None:
            raise OllamaProtocolError("Ollama ended the reply without completing it.")
        return "".join(parts), completion

    def _stop_at_limit(self, turn, utterance, emit, result, state):
        reason = "I stopped there because the task needed too many steps."
        done = join_phrases([op.done for op in turn.operations])
        emit((done + " " if done else "") + reason, segment=True)
        self._remember(utterance, "".join(state["spoken"]))
        return result("limit", "limit", turn, status="limit", reason=reason)

    @staticmethod
    def _tool_message(name: str, outcome: ToolOutcome) -> dict:
        body = {"status": outcome.status, "message": outcome.message, **outcome.data}
        return {"role": "tool", "tool_name": name, "content": json.dumps(body, ensure_ascii=False)}

    def _remember(self, user_text: str, reply: str, turn: _Turn | None = None) -> None:
        """Keep the turn in history. Action turns keep their real tool calls and
        results, so later requests show the model that actions happen through tools."""
        if not user_text.strip() or not reply.strip():
            return
        exchanges = [] if turn is None else self._exchanges(turn)
        try:
            if not exchanges:
                self.conversation.commit(user_text, reply)
                return
            self.conversation.commit_tool_turn(user_text, *exchanges[0])
            for exchange in exchanges[1:]:
                self.conversation.append_tool_step(*exchange)
            self.conversation.complete_last_turn(reply)
        except ValueError:
            pass  # An oversized turn is simply not retained.

    @staticmethod
    def _exchanges(turn: _Turn) -> list[tuple[dict, list[dict]]]:
        """(assistant call, [result]) pairs of this request, without the step reminders."""
        messages, pairs = turn.messages, []
        for index in range(turn.start, len(messages) - 1):
            call, answer = messages[index], messages[index + 1]
            if call["role"] == "assistant" and call.get("tool_calls") and answer["role"] == "tool":
                body = json.loads(answer["content"])
                body.pop("next", None)
                pairs.append(({"role": "assistant", "content": "", "tool_calls": call["tool_calls"]},
                              [{**answer, "content": json.dumps(body, ensure_ascii=False)}]))
        return pairs


def build_engine(client, *, model: str, settings: GenerationSettings, max_turns: int = 8,
                 max_chars: int = 12000, desktop=None, policy: Policy | None = None) -> AgentEngine:
    """The real session engine: Windows adapter, app index, M1 tools."""
    from aqua.tools import build_registry
    from aqua.tools.app_index import AppIndex
    if desktop is None:
        from aqua.tools.win32 import Win32Desktop
        desktop = Win32Desktop()
    apps = AppIndex(desktop.app_entries)
    apps.prewarm()
    context = ToolContext(desktop=desktop, apps=apps,
                          policy=policy if policy is not None else Policy.from_user_config())
    conversation = Conversation(max_turns=max_turns, max_chars=max_chars, system_prompt=SYSTEM_PROMPT)
    return AgentEngine(client, model=model, settings=settings, registry=build_registry(),
                       context=context, conversation=conversation)


__all__ = ["AgentEngine", "AgentTask", "AgentTurnResult", "MAX_DECISIONS", "MAX_OPERATIONS",
           "Operation", "build_engine", "first_sentence", "join_phrases", "strip_emoji"]
