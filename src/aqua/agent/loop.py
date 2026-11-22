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

from .confirm import ConfirmationGate, PendingAction, classify_reply
from .context import ToolContext
from .countdown import is_cancel
from .guards import DEFAULT_GUARDS, Guards
from .recency import beyond_model_memory, claims_not_happened
from .policy import CLARIFY, CONFIRM, DENY, Policy
from .prompt import SYSTEM_PROMPT, user_message
from .registry import Registry, SchemaError, ToolOutcome, validate_arguments
from .selection import (MAX_VISIBLE_TOOLS, matched_groups, select_tools, selected_groups, wants_explanation,
                        wants_no_action)

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
    (re.compile(r"\b(?:opened|launched|started|brought)\b", re.I), {"launch_app", "window_action", "open_url",
                                                                     "web_search"}),
    (re.compile(r"\b(?:typed|wrote|written|entered)\b", re.I), {"type_text"}),
    (re.compile(r"\b(?:muted|unmuted|lowered|raised|increased|decreased|turned\s+(?:the\s+)?(?:volume|sound|it)|"
                r"set\s+the\s+volume|volume\s+(?:is|has\s+been)\s+(?:now|set|turned))\b", re.I), {"volume", "brightness"}),
    (re.compile(r"\b(?:paused|resumed|skipped)\b", re.I), {"media"}),
    (re.compile(r"\blocked\b", re.I), {"lock_screen"}),
    (re.compile(r"\b(?:minimi[sz]ed|maximi[sz]ed|restored|focused|switched)\b", re.I), {"window_action", "undo_last"}),
    (re.compile(r"\b(?:undid|undone|reverted|put\s+\w+\s+back)\b", re.I), {"undo_last"}),
    (re.compile(r"\bclosed\b", re.I), {"close_app", "undo_last", "force_quit_app"}),
    (re.compile(r"\b(?:force[- ]quit|killed|terminated|ended)\b", re.I), {"force_quit_app"}),
    (re.compile(r"\b(?:brightness\s+(?:is|has\s+been)\s+(?:now|set|turned)|dimmed|brightened)\b", re.I), {"brightness"}),
    (re.compile(r"\b(?:restarting|shutting\s+down|signing\s+(?:you\s+)?out|signed\s+out|going\s+to\s+sleep|"
                r"put\s+(?:the\s+)?(?:computer|pc)\s+to\s+sleep)\b", re.I), {"power"}),
    (re.compile(r"\bsearched\b", re.I), {"web_search"}),
    # Device state must come from a live reading this turn, never from earlier turns.
    (re.compile(r"\bbattery\b[^.?!]*\b\d{1,3}\s?(?:%|percent)|\b\d{1,3}\s?(?:%|percent)[^.?!]*\bbattery\b|"
                r"\b(?:is|are|it's)\s+(?:currently\s+|still\s+)?(?:charging|plugged\s+in|on\s+battery)\b|"
                r"\b\d{1,3}\s?(?:%|percent)[^.?!]*\b(?:charging|charged|plugged\s+in)\b", re.I),
     {"get_battery"}),
    (re.compile(r"\bvolume\b[^.?!]*\b(?:is|at|of)\b[^.?!]*\d{1,3}\s?(?:%|percent)|\b(?:sound|audio|volume)\s+is\s+"
                r"(?:currently\s+)?(?:muted|on|off)\b", re.I), {"device_status", "volume"}),
    (re.compile(r"\bbrightness\b[^.?!]*\b(?:is|at|of)\b[^.?!]*\d{1,3}\s?(?:%|percent)", re.I),
     {"device_status", "brightness"}),
    (re.compile(r"\b(?:music|song|track|video|playback|media|something)\b[^.?!]*\b(?:is|are)\s+(?:currently\s+|still\s+)?"
                r"(?:playing|paused|stopped)\b|\bnothing\s+is\s+playing\b", re.I), {"device_status", "media"}),
    (re.compile(r"\b(?:open\s+windows\s+are|windows\s+(?:that\s+are\s+)?open\s+(?:are|include))\b", re.I),
     {"list_windows"}),
)
# Answer text that is really a tool call written out; it is never spoken.
_TOOL_TEXT = ("<tool_call>", '{"name"', "{ \"name\"")
# Tools whose results are facts for the reply, not actions; their turns skip the claim check.
_KNOWLEDGE_TOOLS = frozenset({"look_up"})
# Tool result keys holding window titles and app names, kept for the look_up privacy guard.
_OBSERVED_KEYS = ("app", "window", "title", "process")
_NUDGE = ("[Aqua host: no tool was called for that, so it has not happened. If the user asked for an action, "
          "call the tool now. If you cannot, say plainly that you did not do it.]")
# "I'll open Notepad for you" with no tool call: a promise is not an action.
_PROMISE = re.compile(
    r"\b(?:i'?ll|i\s+will|let\s+me|i'?m\s+going\s+to|i\s+am\s+going\s+to|i\s+can\s+do\s+that)\b[^.?!]{0,40}?"
    r"\b(?:open|launch|start|type|write|put|mute|unmute|turn|set|lock|close|quit|minimi[sz]e|maximi[sz]e|restore|"
    r"bring|switch|pause|play|resume|skip|search|undo|adjust|raise|lower|reduce|increase|dim|brighten|do\s+that|"
    r"do\s+it)\b", re.I)
_PROMISE_NUDGE = ("[Aqua host: you said you would do it, but no tool was called, so nothing happened. If the user "
                  "asked for it, call the tool now with request \"asked\". Otherwise answer without promising an "
                  "action.]")
# The model offered an action itself ("Would you like me to ...?"). The host turns the
# offer into a real pending confirmation instead of letting the model act on it.
_ASKS_PERMISSION = re.compile(r"\b(?:would|do)\s+you\s+(?:like|want)\s+(?:me\s+)?to\b|\bshall\s+i\b|\bshould\s+i\b|"
                              r"\bwant\s+me\s+to\b|\bgo\s+ahead\?|\bproceed\?|\bshall\s+we\b|"
                              r"\b(?:is\s+that|that's|sound|does\s+that\s+sound)\s+(?:ok|okay|alright|all\s+right|good|fine)\?|"
                              r"\b(?:ok|okay)\?\s*$|\bready\?\s*$", re.I)
_OFFER_NOTE = ("[Aqua host: the user has not answered your question yet. If it offers one specific action, call "
               "that tool now with request \"unsure\": Aqua asks the user and only acts on a yes. If it offers no "
               "specific action, repeat your answer.]")
_QUESTION = re.compile(r"[^.!?]*\?\s*")


def statement_part(text: str) -> str:
    """The reply without its questions ("Spotify is popular. Want me to open it?" -> "Spotify is popular.")."""
    return " ".join(_QUESTION.sub(" ", text).split())


def _names_request(raw_args) -> bool:
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args) if raw_args.strip() else {}
        except ValueError:
            return True  # Malformed: the schema check reports it.
    return not isinstance(raw_args, dict) or raw_args.get("request") not in (None, "")


def _with_unsure(raw_args):
    """Model arguments with request forced to "unsure" (an offer is never a direct request)."""
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args) if raw_args.strip() else {}
        except ValueError:
            return raw_args  # Left for the schema check to report.
    if not isinstance(raw_args, dict):
        return raw_args
    return {**raw_args, "request": "unsure"}
# Citing a source that was never consulted is a false claim too.
_WIKI_CLAIM = re.compile(r"\b(?:according\s+to|from|on)\s+wikipedia\b,?\s*|\bwikipedia\s+(?:says|states|reports|lists)"
                         r"\b(?:\s+that)?,?\s*", re.I)
_WIKI_NUDGE = ("[Aqua host: you did not call look_up, so you have not checked Wikipedia. Call look_up now and answer "
               "from its result, or answer without mentioning Wikipedia.]")


def _recent_nudge(now: datetime) -> str:
    return (f"[Aqua host: today is {now:%A} {now.day} {now:%B %Y}. This is about something after your training data, "
            "and it may well have happened already. Call look_up now with a short query and answer from its result.]")


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


_NEGATION = re.compile(r"\b(?:not|never|no|nothing|cannot|unable|failed|blocked)\b|n't\b", re.I)


def unsupported_claim(reply: str, succeeded: set[str]) -> bool:
    """True when a sentence of the reply claims something no successful tool call this turn
    backs. Negated sentences ("couldn't open it", "nothing was typed") are not claims."""
    for sentence in _SENTENCE_END.split(" ".join(reply.split())):
        if _NEGATION.search(sentence):
            continue
        if any(pattern.search(sentence) and not (tools & succeeded) for pattern, tools in _CLAIMS):
            return True
    return False


def strip_emoji(text: str) -> str:
    return _EMOJI.sub("", text)


_UNABLE = re.compile(r"\b(?:can't|cannot|can\s+not|couldn't|could\s+not|unable|not\s+able|no\s+tool|don't\s+have|"
                     r"isn't\s+something|is\s+not\s+something|not\s+allowed|blocked)\b", re.I)


def with_unable_part(reply: str, content: str) -> str:
    """The one-sentence reply plus the first later sentence saying what could not be done."""
    if _UNABLE.search(reply):
        return reply
    sentences = _SENTENCE_END.split(" ".join(strip_emoji(content).split()))
    extra = next((sentence for sentence in sentences[1:] if _UNABLE.search(sentence)), "")
    return f"{reply} {extra}".strip() if extra else reply


def first_sentence(text: str) -> str:
    return first_sentences(text, 1)


def first_sentences(text: str, count: int) -> str:
    text = " ".join(strip_emoji(text).split())
    return " ".join(_SENTENCE_END.split(text, count)[:count]).strip() if text else ""


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
    reviews: list = field(default_factory=list)  # (tool, outcome) whose results check the final reply
    offer_mode: bool = False    # The model is naming the tool for its own offer; calls become "unsure".
    groups: tuple = ()          # Keyword groups used for this request (inherited by follow-ups).
    explain: bool = False       # An explanation-only question: action tools hidden, offers still allowed.


class AgentEngine:
    def __init__(self, client, *, model: str, settings: GenerationSettings, registry: Registry,
                 context: ToolContext, conversation: Conversation | None = None,
                 clock=perf_counter, now=None, guards: Guards = DEFAULT_GUARDS) -> None:
        self.client, self.model, self.settings = client, model, settings
        self.guards = guards  # Host guards; all on outside evaluation runs.
        self.registry, self.context = registry, context
        self.conversation = conversation if conversation is not None else Conversation(system_prompt=SYSTEM_PROMPT)
        self.gate = ConfirmationGate()
        self._last_groups: tuple = ()  # A follow-up with no keyword ("check again") reuses these.
        self._asked_after: tuple = ()  # Requests that ended in a question from Aqua (newest last).
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

    @property
    def countdown_active(self) -> bool:
        countdown = self.context.countdown
        return countdown is not None and countdown.active(self.context.desktop.monotonic())

    @property
    def awaiting_answer(self) -> bool:
        """A yes/no or a "cancel" is expected next (hands-free opens its follow-up window)."""
        return self.pending is not None or self.countdown_active

    def countdown_announcement(self) -> str | None:
        """The next spoken countdown reminder, if one is due (polled by hands-free)."""
        countdown = self.context.countdown
        if countdown is None:
            return None
        now = self.context.desktop.monotonic()
        if not countdown.active(now):
            self.context.countdown = None
            return None
        return countdown.due_announcement(now)

    def _cancel_countdown(self, text: str) -> str | None:
        """Abort a running restart/shutdown countdown when the user says cancel."""
        countdown = self.context.countdown
        if countdown is None:
            return None
        if not countdown.active(self.context.desktop.monotonic()):
            self.context.countdown = None
            return None
        if not is_cancel(text):
            return None
        self.context.countdown = None
        try:
            self.context.desktop.abort_shutdown()
        except OSError:
            return f"I could not cancel the {countdown.kind}. Run shutdown /a now to stop it."
        return f"Cancelled the {countdown.kind}."

    # -- one user utterance -------------------------------------------------

    def respond(self, text: str, *, on_text=None) -> AgentTurnResult:
        result = self._respond(text, on_text=on_text)
        # When Aqua ended with a question, the next request answers it: keep this request (and
        # what it answered) so dictated text in it still counts as the user's own words.
        asked = result.outcome in {"clarify", "confirm"} or result.text.rstrip().endswith("?")
        self._asked_after = ((*self._asked_after[-1:], text.strip()) if asked else ())
        return result

    def _respond(self, text: str, *, on_text=None) -> AgentTurnResult:
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

        cancelled = self._cancel_countdown(text)
        if cancelled is not None:
            emit(cancelled)
            self._remember(text, cancelled)
            return result("cancelled", "countdown", None)
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
        self.context.begin_request(text, self._asked_after)
        names = select_tools(text, self.registry, negation_guard=self.guards.negation_guard,
                             question_guard=self.guards.question_guard, previous=self._last_groups)
        bare = classify_reply(text)
        if bare is not None:
            # A bare yes/no with nothing pending answers no host question, so it can never act:
            # "no" gets no tools, "yes" only read-only ones.
            names = () if bare == "no" else tuple(n for n in names if self._read_only(n))
        groups = selected_groups(text, self._last_groups) if names else ()
        self._last_groups = groups
        not_done = tuple(dict.fromkeys(action.summary for action in self.gate.take_dropped() if action.summary))
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *self.conversation.history,
                    {"role": "user", "content": user_message(text, self.now(), undoable=self._undoable(),
                                                             not_done=not_done)}]
        explain = self.guards.question_guard and wants_explanation(text) and not (
            self.guards.negation_guard and wants_no_action(text))
        return self._loop(_Turn(text, names, messages, start=len(messages), groups=groups, explain=explain),
                          text, emit, result, state)

    # -- the loop -------------------------------------------------------------

    def _loop(self, turn: _Turn, utterance: str, emit, result, state) -> AgentTurnResult:
        definitions = self.registry.definitions(turn.tool_names) or None
        first_new = len(turn.operations)  # Operations before this point were reported earlier.
        # A request that looks like a command is answered in one checked sentence,
        # so it is buffered; plain conversation streams live.
        command_like = turn.explain or (bool(turn.tool_names) and any(
            self.registry.in_group(group) for group in (turn.groups or matched_groups(turn.request))))
        nudged = False
        offer = None  # (the model's offer text, whether it was already spoken)
        while True:
            acted = bool(turn.operations)
            live = not acted and not command_like
            if turn.decisions >= MAX_DECISIONS:
                return self._stop_at_limit(turn, utterance, emit, result, state)
            turn.decisions += 1
            try:
                content, completion, held = self._model_round(turn.messages, definitions, emit if live else None)
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
            if not calls and (held or not live):
                text_call = parse_text_tool_call(content, turn.tool_names)
                if text_call is not None:
                    calls, content = [text_call], ""
                elif held and live:
                    # A written-out call to a tool that is not available: never speak it.
                    emit("Okay.")
                    self._remember(utterance, "Okay.", turn)
                    return result("answer", "complete", turn)
            if not calls:
                if offer is None and not nudged and not acted and self._is_offer(turn, content):
                    # One round for the model to name the exact tool of its offer; that call is
                    # forced to "unsure", so a plain "yes" from the user runs it, never the model.
                    offer, nudged = (" ".join(strip_emoji(content).split()), live), True
                    turn.offer_mode, command_like = True, True
                    # The offer round may name any action the request could involve, even after an
                    # explanation-only question: every call it makes is "unsure", so the host asks.
                    wider = select_tools(turn.request, self.registry, negation_guard=self.guards.negation_guard,
                                         question_guard=False, previous=turn.groups)
                    turn.tool_names = tuple(dict.fromkeys((*turn.tool_names, *wider)))[:MAX_VISIBLE_TOOLS]
                    definitions = self.registry.definitions(turn.tool_names) or None
                    turn.messages += [{"role": "assistant", "content": content}, {"role": "user", "content": _OFFER_NOTE}]
                    continue
                if offer is not None:
                    if not offer[1]:
                        emit(offer[0], segment=True)  # No specific action: keep the model's own question.
                    self._remember(utterance, "".join(state["spoken"]), turn)
                    return result("answer", "complete", turn)
                if live:
                    if not "".join(state["spoken"]).strip():
                        emit("Sorry, I didn't get a reply. Please try again.")
                        return result("error", "model", turn, ok=False, error="The model returned an empty reply.",
                                      status="error")
                else:
                    attempted = {op.tool for op in turn.operations if op.status != "invalid"}
                    succeeded = {op.tool for op in turn.operations if op.status in {"ok", "unverified", "empty",
                                                                                     "duplicate"}}
                    looked_up = bool(attempted & _KNOWLEDGE_TOOLS)
                    # A looked-up answer may add one caveat ("...may be out of date").
                    reply = (first_sentences(content, 2 if looked_up else 1) if acted
                             else " ".join(strip_emoji(content).split()))
                    if acted and not looked_up:
                        reply = with_unable_part(reply, content)  # "... but I can't delete files." stays.
                    can_look_up = "look_up" in turn.tool_names and not looked_up
                    recent = beyond_model_memory(turn.request, reply, self.now(),
                                                 knowledge_cutoff=self.guards.knowledge_cutoff)
                    cited = self.guards.citation_strip and bool(_WIKI_CLAIM.search(reply))
                    if not nudged and can_look_up and (recent or cited):
                        # Recent events, or a Wikipedia citation without a look-up: look it up first.
                        # Only look_up is offered for this one retry, so the model cannot just talk again.
                        nudged = True
                        definitions = self.registry.definitions(("look_up",))
                        note = _recent_nudge(self.now()) if recent else _WIKI_NUDGE
                        turn.messages += [{"role": "assistant", "content": content}, {"role": "user", "content": note}]
                        continue
                    if not looked_up and nudged and recent and can_look_up:
                        reply = ("I can't check that right now, and it may be too recent for me to know. "
                                 "I don't want to guess.")
                    found = any(op.tool in _KNOWLEDGE_TOOLS and op.status == "ok" for op in turn.operations)
                    if looked_up and not found and claims_not_happened(reply):
                        reply = "I couldn't find that on Wikipedia, so I don't know."  # Not a stale-memory guess.
                    for tool, outcome in turn.reviews:
                        reply = tool.review(reply, outcome) or reply  # The tool's own check of the answer.
                    if self.guards.citation_strip and not looked_up and _WIKI_CLAIM.search(reply):
                        reply = _WIKI_CLAIM.sub("", reply, count=1).strip()  # Never cite an unchecked source.
                        reply = reply[:1].upper() + reply[1:]
                    acted_for_real = any(op.status in {"ok", "unverified", "empty"} and not self._read_only(op.tool)
                                         for op in turn.operations)
                    if (self.guards.claim_retry and turn.tool_names and not looked_up and not acted_for_real
                            and _PROMISE.search(reply)):
                        if not nudged:
                            # "I'll open Notepad for you" and no call: one nudge to actually do it.
                            nudged = True
                            turn.messages += [{"role": "assistant", "content": content},
                                              {"role": "user", "content": _PROMISE_NUDGE}]
                            continue
                        reply = "Sorry, I didn't manage to do that."  # Never promise what did not happen.
                    if (self.guards.claim_retry and turn.tool_names and not looked_up
                            and unsupported_claim(reply, succeeded)):
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
                    countdown = self.context.countdown
                    if countdown is not None and countdown.fresh:
                        countdown.fresh = False
                        if "cancel" not in reply.casefold():
                            reply += " Say cancel to stop it."
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
                    lead = statement_part(offer[0]) if offer is not None and not offer[1] else ""
                    emit(" ".join(part for part in (done, lead, question) if part), segment=True)
                    self._remember(utterance, "".join(state["spoken"]))
                    return result(kind, kind, turn, status="waiting")
            if self.guards.restate_request:
                self._remind(turn)

    def _is_offer(self, turn: _Turn, content: str) -> bool:
        """The model asked the user whether to do something while action tools were shown."""
        text = " ".join(strip_emoji(content).split())
        return (self.guards.cant_retry and (bool(turn.tool_names) or turn.explain) and text.endswith("?")
                and bool(_ASKS_PERMISSION.search(text)))

    def _read_only(self, name: str) -> bool:
        tool = self.registry.get(name)
        return tool is not None and not callable(tool.tier) and tool.tier == 0

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
                        "call the tool for it now. If a part cannot be done with the available tools, say which "
                        "part and why. Otherwise reply in one sentence.")
        message["content"] = json.dumps(body, ensure_ascii=False)

    def _handle_call(self, turn: _Turn, name, raw_args):
        tool = self.registry.get(name)
        if tool is None or tool.name not in turn.tool_names:
            # Only tools shown for this request can run; visibility is not permission,
            # but invisibility is a firm no.
            turn.messages.append(self._tool_message(str(name), ToolOutcome("failed", f"Unknown tool {name}.")))
            turn.operations.append(Operation(str(name), {}, "invalid", "unknown tool"))
            return None
        if "request" in tool.parameters.get("properties", {}) and (turn.offer_mode or not _names_request(raw_args)):
            # An offer is never a direct request, and a call that does not say whether the user
            # asked for it is treated as unsure: the host asks instead of guessing.
            raw_args = _with_unsure(raw_args)
        try:
            args = validate_arguments(tool.parameters, raw_args)
        except SchemaError as exc:
            turn.messages.append(self._tool_message(tool.name, ToolOutcome("failed", f"Invalid arguments: {exc}")))
            turn.operations.append(Operation(tool.name, {}, "invalid", str(exc)))
            return None
        for name in tool.private_args:
            if isinstance(args.get(name), str):
                self.context.typed.append(args[name])  # Kept for the look_up guard, even if not run.
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
            summary = tool.describe(args) if tool.describe is not None else tool.name.replace("_", " ")
            self.gate.offer(PendingAction(tool.name, args, decision.question, self.clock(), turn.request,
                                          turn.messages, turn.tool_names, turn.decisions, list(turn.operations),
                                          start=turn.start, summary=summary))
            return "confirm", decision.question
        outcome = self._run(tool, args)
        turn.messages.append(self._tool_message(tool.name, outcome))
        turn.operations.append(Operation(tool.name, args, outcome.status, outcome.message, outcome.done))
        if tool.review is not None:
            turn.reviews.append((tool, outcome))
        return None

    def _run(self, tool, args) -> ToolOutcome:
        try:
            outcome = tool.run(args, self.context)
        except Exception:
            return ToolOutcome("failed", f"{tool.name} failed unexpectedly.")
        if outcome.undo is not None and outcome.succeeded:
            self.context.undo.push(outcome.undo)
        if tool.group != "knowledge":
            self._observe(outcome.data)
        return outcome

    def _observe(self, data) -> None:
        """Remember window titles and app names tools reported (look_up must not send them)."""
        if isinstance(data, dict):
            for key, value in data.items():
                if key in _OBSERVED_KEYS:
                    self.context.observe(value)
                elif isinstance(value, (list, dict)):
                    self._observe(value)
        elif isinstance(data, list):
            for item in data:
                self._observe(item)

    def _model_round(self, messages, definitions, stream_to):
        """One model decision. Text streams live only before any tool has run;
        afterwards it is buffered so only one sentence is spoken. A reply that starts
        like a written-out tool call is held back (``held``) and never streamed."""
        guard, parts, completion = AnswerChannelGuard(), [], None
        undecided, held = stream_to is not None, False
        iterator = self.client.stream_chat(messages, model=self.model, settings=self.settings, tools=definitions)
        try:
            for event in iterator:
                if event.content:
                    visible = guard.feed(event.content)
                    if visible:
                        parts.append(visible)
                        if undecided:
                            start = "".join(parts).lstrip()
                            if any(prefix.startswith(start) for prefix in _TOOL_TEXT) and len(start) < 16:
                                continue  # Could still become "<tool_call>": wait.
                            undecided = False
                            held = start.startswith(_TOOL_TEXT)
                            if not held:
                                stream_to("".join(parts))
                        elif stream_to is not None and not held:
                            stream_to(visible)
                if event.done:
                    completion = event
            tail = guard.finish()
            if tail:
                parts.append(tail)
            if stream_to is not None and not held:
                pending = "".join(parts) if undecided else tail
                if undecided and pending.lstrip().startswith(_TOOL_TEXT):
                    held = True
                elif pending:
                    stream_to(pending)
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()
        if completion is None:
            raise OllamaProtocolError("Ollama ended the reply without completing it.")
        return "".join(parts), completion, held

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
                 max_chars: int = 12000, desktop=None, policy: Policy | None = None,
                 lookup: bool = True) -> AgentEngine:
    """The real session engine: Windows adapter, app index, all tools (look_up unless disabled)."""
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
    return AgentEngine(client, model=model, settings=settings, registry=build_registry(lookup=lookup),
                       context=context, conversation=conversation)


__all__ = ["AgentEngine", "AgentTask", "AgentTurnResult", "MAX_DECISIONS", "MAX_OPERATIONS",
           "Operation", "build_engine", "first_sentence", "join_phrases", "strip_emoji"]
