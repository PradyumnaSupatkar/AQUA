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
from .howto import how_to_answer, is_refusal
from .recency import beyond_model_memory, claims_not_happened
from .policy import CLARIFY, CONFIRM, DENY, Policy
from .prompt import SYSTEM_PROMPT, user_message
from .registry import SAFE, Registry, SchemaError, ToolOutcome, validate_arguments
from .selection import (MAX_VISIBLE_TOOLS, direct_command, matched_groups, select_tools, selected_groups,
                        wants_explanation, wants_no_action)

MAX_OPERATIONS = 12
MAX_DECISIONS = 14

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002B00-\U00002BFF"
    "\U0000FE0F\U0000200D\U000020E3]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_SENTENCE_BREAK = re.compile(r"[.!?]+[\"')\]]*\s+")  # End of a sentence while streaming
# Statuses of operations that actually ran; only these count toward MAX_OPERATIONS.
_EXECUTED = frozenset({"ok", "unverified", "failed", "empty"})

# Past-tense action claims and the tools whose results can back them up. Used
# only to catch a reply that says something happened when no tool did it; it
# never authorizes or blocks an action.
_ACTION_CLAIMS = (
    (re.compile(r"\b(?:opened|launched|started|brought)\b|\b(?:is|are)\s+now\s+open\b", re.I),
     {"launch_app", "window_action", "open_url", "web_search", "youtube"}),
    (re.compile(r"\b(?:typed|wrote|written|entered)\b", re.I), {"type_text"}),
    (re.compile(r"\b(?:muted|unmuted|lowered|raised|increased|decreased|turned\s+(?:the\s+)?(?:volume|sound|it)|"
                r"set\s+the\s+volume|volume\s+(?:is|has\s+been)\s+(?:now|set|turned))\b", re.I), {"volume", "brightness", "youtube"}),
    (re.compile(r"\b(?:paused|resumed|skipped)\b", re.I), {"media", "youtube"}),
    (re.compile(r"\b(?:fullscreen|full\s+screen|captions|subtitles|playback\s+speed|quality)\b[^.?!]*"
                r"\b(?:on|off|set|now|changed|turned)\b|\b(?:rewound|fast-forwarded|jumped\s+to|moved\s+the\s+video)\b",
                re.I), {"youtube"}),
    (re.compile(r"\blocked\b", re.I), {"lock_screen"}),
    (re.compile(r"\b(?:minimi[sz]ed|maximi[sz]ed|restored|focused|switched)\b", re.I), {"window_action", "undo_last"}),
    (re.compile(r"\b(?:undid|undone|reverted|put\s+\w+\s+back)\b", re.I), {"undo_last"}),
    (re.compile(r"\bclosed\b", re.I), {"close_app", "undo_last", "force_quit_app"}),
    (re.compile(r"\b(?:force[- ]quit|killed|terminated|ended)\b", re.I), {"force_quit_app"}),
    (re.compile(r"\b(?:brightness\s+(?:is|has\s+been)\s+(?:now|set|turned)|dimmed|brightened)\b", re.I), {"brightness"}),
    (re.compile(r"\b(?:restarting|shutting\s+down|signing\s+(?:you\s+)?out|signed\s+out|going\s+to\s+sleep|"
                r"put\s+(?:the\s+)?(?:computer|pc)\s+to\s+sleep)\b", re.I), {"power"}),
    (re.compile(r"\bsearched\b", re.I), {"web_search", "youtube"}),
)
_STATE_CLAIMS = (
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
                r"(?:playing|paused|stopped)\b|\bnothing\s+is\s+playing\b|\bnow\s+playing\b", re.I),
     {"device_status", "media", "youtube"}),
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
    r"\b(?:i'?ll|i\s+will|let\s+me|i'?m\s+going\s+to|i\s+am\s+going\s+to|i\s+can(?!['’]t|not)(?:\s+also)?)\b[^.?!]{0,40}?"
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
# The host's own confirmation phrasing. In a model reply it is a dead question: nothing is pending,
# so a "yes" would get "There's nothing waiting for a yes." (live, after an earlier real one).
_HOST_ASK = re.compile(r"\b(?:please\s+)?(?:say|answer|reply)\s+(?:with\s+)?(?:a\s+)?yes\s+or\s+(?:a\s+)?no\b", re.I)
_HOST_ASK_SENTENCE = re.compile(r"[^.!?]*\b(?:say|answer|reply)\s+(?:with\s+)?(?:a\s+)?yes\s+or\s+(?:a\s+)?no\b"
                                r"[^.!?]*[.!?]*\s*", re.I)
_HOST_ASK_NUDGE = ("[Aqua host: you asked the user yourself, but nothing is waiting for their answer, so a yes "
                   "would do nothing. Only Aqua asks for a yes. The user asked for this: call the tool now with "
                   "request \"asked\"; Aqua asks the user itself when the action needs it.]")


def without_host_ask(text: str) -> str:
    """The text without its "Say yes or no." sentences."""
    return " ".join(_HOST_ASK_SENTENCE.sub(" ", text).split())
# "I cannot jump to a specific time as that is not a function available to me" for a command a
# shown tool can do (live: "jump to one minute thirty", 0 tools): a capability denial, nudged once.
_DENIAL = re.compile(
    r"\bi\s+(?:cannot|can't|can\s+not|am\s+unable\s+to|am\s+not\s+able\s+to|"
    r"(?:do\s+not|don't)\s+have\s+(?:the\s+)?(?:ability|capability|a\s+(?:tool|function|way)|access))\b|"
    r"\bi'm\s+(?:unable|not\s+able)\s+to\b|"
    r"\bnot\s+(?:a\s+)?(?:function|tool|feature|capability)\s+(?:that\s+is\s+|that's\s+)?available\b|"
    r"\b(?:function|tool|feature|capability)\s+(?:is\s+)?not\s+available\b|"
    r"\bno\s+(?:tool|function)\s+(?:is\s+)?(?:available|for\s+(?:that|this))\b", re.I)


def _denial_nudge(tools: list[str]) -> str:
    return (f"[Aqua host: you do have a tool for this: {', '.join(tools)}. If it does what the user asked, call "
            "it now with request \"asked\". If it really cannot, say plainly which part you could not do.]")


def statement_part(text: str) -> str:
    """The reply without its questions ("Spotify is popular. Want me to open it?" -> "Spotify is popular.")."""
    return " ".join(_QUESTION.sub(" ", text).split())


def without_unbacked_claims(text: str, succeeded: set[str]) -> str:
    """The text without sentences claiming an action no tool of this request performed, e.g. an
    earlier turn's "I have typed 'this is a test' into Notepad" recapped as if it were new."""
    kept = [s for s in _SENTENCE_END.split(" ".join(text.split())) if s and not unsupported_claim(s, succeeded)]
    return " ".join(kept)


def _names_request(raw_args) -> bool:
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args) if raw_args.strip() else {}
        except ValueError:
            return True  # Malformed: the schema check reports it.
    return not isinstance(raw_args, dict) or raw_args.get("request") not in (None, "")


def _with_request(raw_args, value: str):
    """Model arguments with request set to ``value``."""
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args) if raw_args.strip() else {}
        except ValueError:
            return raw_args  # Left for the schema check to report.
    if not isinstance(raw_args, dict):
        return raw_args
    return {**raw_args, "request": value}


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


_PLAIN_NUDGE = ("[Aqua host: No tool can be called for this. Answer the user in plain words, in one or two "
                "sentences, without writing any tool call.]")
_TEXT_CALL = re.compile(r"^\s*(?:<tool_call>\s*)?(\{.*\})\s*(?:</tool_call>)?\s*$", re.S)


def looks_like_tool_text(content: str) -> bool:
    """A reply that is (or contains) a written-out tool call; never displayed or spoken."""
    text = (content or "").lstrip()
    return "<tool_call>" in text or "</tool_call>" in text or text.startswith(_TOOL_TEXT)


def parse_text_tool_call(content: str, visible: tuple[str, ...]) -> dict | None:
    """Qwen sometimes writes a tool call as plain JSON instead of a native call.
    Accept only a whole-reply JSON object naming a tool shown for this request."""
    content = content or ""
    if "<tool_call>" in content:
        content = content[content.index("<tool_call>"):]  # "Sure. <tool_call>{...}": the call part.
    match = _TEXT_CALL.match(content)
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


# An action claim is about the user's PC: Aqua speaking ("I opened ..."), a state change
# ("has been muted", "is now open"), or the PC, a device part or an app named. "Jane Austen
# wrote Pride and Prejudice" is a fact, not a claim.
_ON_PC = re.compile(
    r"\b(?:i|i've|i'd|i'm|we|aqua|you|windows)\b|\b(?:has|have)\s+been\b|\bis\s+now\b|"
    r"\b(?:your|the|my)\s+(?:computer|pc|laptop|screen|desktop|volume|sound|audio|brightness|window|windows|browser|"
    r"music|media|playback|song|track|app|apps|file|files|text)\b|"
    r"\b(?:notepad|chrome|spotify|calculator|vs\s+code|visual\s+studio|edge|file\s+explorer|word|excel|paint|"
    r"youtube|settings)\b", re.I)


def unsupported_claim(reply: str, succeeded: set[str]) -> bool:
    """True when a sentence of the reply claims something no successful tool call this turn
    backs. Negated sentences ("couldn't open it", "nothing was typed") are not claims, and an
    action verb counts only in a sentence about the user's PC."""
    for sentence in _SENTENCE_END.split(" ".join(reply.split())):
        if any(pattern.search(sentence) and not (tools & succeeded) for pattern, tools in _STATE_CLAIMS):
            return True  # Device state ("nothing is playing" too) always needs a reading.
        if _NEGATION.search(sentence):
            continue
        on_pc = bool(_ON_PC.search(sentence))
        if on_pc and any(pattern.search(sentence) and not (tools & succeeded) for pattern, tools in _ACTION_CLAIMS):
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


def _steps(outcome: ToolOutcome) -> int:
    steps = outcome.data.get("steps", 1) if isinstance(outcome.data, dict) else 1
    return steps if isinstance(steps, int) and 1 <= steps <= 4 else 1


# Steps a request asks for, counted generously (clauses and action verbs), so the host writes
# the reply only when the tool calls clearly covered all of them; otherwise the model answers.
_CLAUSE = re.compile(r"\s*(?:[,;]|\band\s+then\b|\bthen\b|\band\b|\balso\b|\bafter\s+that\b|\bplus\b)\s*", re.I)
_STEP_VERB = re.compile(
    r"\b(?:open|launch|start|lock|mute|unmute|turn|set|close|quit|exit|minimi[sz]e|maximi[sz]e|restore|bring|"
    r"switch|pause|play|resume|skip|search|undo|raise|lower|dim|brighten|increase|decrease|type|write|put|show|go|"
    r"delete|remove|send|email|call|move|copy|save|download|install|read|tell|check|find|look|schedule|remind|"
    r"sleep|restart|reboot|shut|sign|log|force|kill|focus|snap)\b", re.I)


# Words in the user's own request that ask for each YouTube action. After page text arrived in a
# request, an action whose words the user did not say is asked about first (it may be injected).
_YOUTUBE_ASKS = {action: re.compile(pattern, re.I) for action, pattern in {
    "play": r"\b(?:play|resume|unpause|continue|start)\b",
    "pause": r"\b(?:pause|stop|hold)\b",
    "seek": r"\b(?:seek|skip|forward|back|rewind|jump|go\s+to|minutes?|seconds?|percent|halfway|beginning)\b|"
            r"\d+:\d\d|%",
    "next": r"\b(?:next|skip)\b",
    "previous": r"\b(?:previous|last|back)\b",
    "fullscreen": r"\bfull\s?screen\b",
    "captions": r"\b(?:captions?|subtitles?|cc)\b",
    "speed": r"\b(?:speed|faster|slower)\b|\b\d(?:\.\d+)?\s?x\b",
    "quality": r"\b(?:quality|resolution|4k|hd|\d{3,4}\s?p)\b",
    "volume": r"\b(?:volume|louder|quieter|softer)\b",
    "mute": r"\b(?:mute|unmute|silence)\b",
    "search": r"\b(?:search|find|look\s+(?:up|for))\b",
    "play_result": r"\b(?:play|first|second|third|fourth|fifth|one|result|number)\b",
    "skip_ad": r"\b(?:ads?|advert|skip)\b",
}.items()}
_NOW_PLAYING = re.compile(r"\bwhat(?:'s|\s+is|\s+was)\s+(?:this\s+|currently\s+|now\s+)?(?:playing|on)\b|"
                          r"\bnow\s+playing\b|\bwhat\s+(?:video|song|track)\s+is\s+(?:this|playing|on)\b|"
                          r"\bwho(?:'s|\s+is)\s+(?:this|the\s+channel|playing)\b", re.I)
_OPEN_VERB = re.compile(r"\b(?:open|launch|start|run|bring\s+up|fire\s+up)\b", re.I)


def steps_asked(request: str) -> int:
    clauses = [part for part in _CLAUSE.split(request) if part.strip()]
    return max(len(clauses), len(_STEP_VERB.findall(request)), 1)


def host_summary(phrases: list[str]) -> str:
    """["opened Brave", "opened YouTube"] -> "Opened Brave and YouTube." (a repeated verb is said once)."""
    merged, last, opened = [], None, set()
    for phrase in (p for p in phrases if p):
        head, into, place = phrase.rpartition(" into ")
        if into and place.casefold() in opened:
            phrase = head  # "opened Notepad and typed your text", not "... into Notepad".
        if phrase.startswith("opened "):
            opened.add(phrase[len("opened "):].casefold())
        verb, _, rest = phrase.partition(" ")
        merged.append(rest if verb == last and rest else phrase)
        last = verb
    return join_phrases(merged)


# Sites and browsers a web reply may name; each must match what the tools reported.
_WEB_NAMES = re.compile(r"\b(you\s?tube|google|wikipedia|amazon|git\s?hub|duckduckgo|chrome|brave|firefox|"
                        r"microsoft\s+edge|opera|vivaldi)\b", re.I)


def web_mismatch(reply: str, operations) -> bool:
    """The reply names a site or browser that the web tools did not use ("searched on YouTube"
    when the search went to DuckDuckGo)."""
    if not any(op.tool in {"open_url", "web_search"} and op.status == "ok" for op in operations):
        return False
    facts = " ".join(f"{op.message} {op.done}" for op in operations
                     if op.status in {"ok", "unverified", "duplicate"}).casefold().replace(" ", "")
    return any(name.casefold().replace(" ", "") not in facts for name in _WEB_NAMES.findall(reply))


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
    steps: int = 1              # Request steps this call covered ("open youtube and search X" = 2)


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
    detail: str | None = None   # Shown on screen after the reply, never spoken (e.g. text to type).

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
    detail: str = ""            # Display-only text for the host question this request ended in.
    tainted: bool = False       # A tool returned untrusted page text (YouTube titles) this request.


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
        self._awaiting_text: str | None = None  # Dictation waiting for the answer to "Which app?".
        self.clock = clock
        self.now = now if now is not None else (lambda: datetime.now().astimezone())

    @property
    def awaiting_app(self) -> bool:
        """Aqua asked "Which app should I type that into?" and keeps the text for the answer."""
        return self._awaiting_text is not None

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
                                   self.clock() - started, state["first"], state["completion"], stage, error, task,
                                   detail=(turn.detail or None) if turn is not None else None)

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
                turn.operations.append(Operation(tool.name, pending.args, outcome.status, outcome.message, outcome.done,
                                                 _steps(outcome)))
            turn.messages.append(self._tool_message(tool.name, outcome))
            return self._loop(turn, text, emit, result, state, host_first=True)

        text = text.strip()
        self.context.begin_request(text, self._asked_after)
        awaiting, self._awaiting_text = self._awaiting_text, None
        app = self._named_app(text) if awaiting is not None else None
        if app is not None:
            # "write meeting at noon" -> "Which app?" -> "Notepad": the host opens (or brings
            # forward) that app and types the kept text, so the dictation cannot get lost.
            return self._type_into_answer(text, awaiting, app, emit, result)
        if "type_text" in self.registry.names:
            from aqua.tools.input import unplaced_dictation
            dictation = unplaced_dictation(self.context)
            if dictation is not None:
                # "write meeting at noon" with nothing to type into: ask where (and keep the words),
                # rather than letting the model guess at a meaning ("schedule a meeting").
                self._awaiting_text = dictation
                emit("Which app should I type that into?")
                self._remember(text, "Which app should I type that into?")
                return result("clarify", "clarify", None, status="waiting")
        names = select_tools(text, self.registry, negation_guard=self.guards.negation_guard,
                             question_guard=self.guards.question_guard, previous=self._last_groups)
        bare = classify_reply(text)
        if bare is not None and not self._asked_after:
            # A bare yes/no with nothing pending and no question from Aqua before it: the host
            # answers, and the model never sees it (so it can never act on it).
            reply = "There's nothing waiting for a yes." if bare == "yes" else "Okay, there's nothing to cancel."
            emit(reply)
            self._remember(text, reply)
            return result("answer", "nothing-pending", None)
        if bare is not None:
            # A yes/no to Aqua's own conversational question ("Would you like a joke?"): the
            # model answers it, but it can never act on it ("no": no tools, "yes": read-only).
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

    def _loop(self, turn: _Turn, utterance: str, emit, result, state, *, host_first: bool = False) -> AgentTurnResult:
        definitions = self.registry.definitions(turn.tool_names) or None
        if host_first:
            reply = self._host_reply(turn)  # A confirmed action that finished the request.
            if reply is not None:
                return self._finish_host(turn, utterance, reply, emit, result)
        first_new = len(turn.operations)  # Operations before this point were reported earlier.
        # A request that looks like a command is answered in one checked sentence,
        # so it is buffered; plain conversation streams live.
        command_like = turn.explain or (bool(turn.tool_names) and any(
            self.registry.in_group(group) for group in (turn.groups or matched_groups(turn.request))))
        nudged = False
        already = ""  # Text of this reply already streamed before a held claim
        offer = None  # (the model's offer text, whether it was already spoken)
        plain = False  # The model was asked for plain words after a written-out tool call.
        while True:
            acted = bool(turn.operations)
            live = not acted and not command_like
            if turn.decisions >= MAX_DECISIONS:
                return self._stop_at_limit(turn, utterance, emit, result, state)
            turn.decisions += 1
            try:
                content, completion, held, streamed, claimed = self._model_round(
                    turn.messages, definitions, emit if live else None)
                if live and claimed:
                    # A streamed sentence claimed an action: check it like a buffered reply; what
                    # was already spoken (before that sentence) is not repeated.
                    live, command_like, already = False, True, " ".join(streamed.split())
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
                elif held or looks_like_tool_text(content):
                    # A written-out call to a tool that was not shown: never displayed or spoken.
                    hidden = parse_text_tool_call(content, self.registry.names)
                    if hidden is not None and not plain and self._may_surface(turn):
                        name = hidden["function"]["name"]
                        turn.tool_names = (name, *turn.tool_names)[:MAX_VISIBLE_TOOLS]
                        definitions = self.registry.definitions(turn.tool_names)
                        if not self._read_only(name):
                            # An action the request did not select: the host asks first, as for an offer.
                            turn.offer_mode, command_like = True, True
                        calls, content = [hidden], ""
                    elif not nudged:
                        # Ask once for a plain answer, with no tools to call.
                        nudged, plain, live, command_like, definitions = True, True, False, True, None
                        turn.messages += [{"role": "assistant", "content": ""},
                                          {"role": "user", "content": _PLAIN_NUDGE}]
                        continue
                    else:
                        emit("Okay.", segment=True)
                        self._remember(utterance, "Okay.", turn)
                        return result("answer", "complete", turn)
            if not calls:
                asks = offer is None and not nudged and not acted and self._is_offer(turn, content)
                promised = offer is None and not nudged and not acted and not asks and self._promise_offer(turn, content)
                if (asks or promised) and not turn.explain and self._commanded_request(turn):
                    # "would you be able to open Chrome?" answered "Would you like me to?": the user
                    # already asked, so one nudge to do it now instead of asking back. A copy of the
                    # host's own "...? Say yes or no." (live: "Putting the PC to sleep? Say yes or no.")
                    # is a dead question: nothing is pending, so the model must call the tool.
                    nudged, command_like = True, True
                    note = _HOST_ASK_NUDGE if _HOST_ASK.search(content) else _PROMISE_NUDGE
                    turn.messages += [{"role": "assistant", "content": content},
                                      {"role": "user", "content": note}]
                    continue
                if asks or promised:
                    # One round for the model to name the exact tool of its offer; that call is
                    # forced to "unsure", so a plain "yes" from the user runs it, never the model.
                    # A promise to a request that was not a command ("I'd like somewhere to jot
                    # down thoughts" -> "I'll open Notepad") is an offer too, never a reason to act.
                    # Claims of actions no tool did in this request (an earlier turn's typing recapped as
                    # new) never reach the user, not even as the lead-in to an offer.
                    said = " ".join(strip_emoji(content).split())
                    offer_text = (without_unbacked_claims(said, set()) if self.guards.claim_retry else said)
                    if _HOST_ASK.search(offer_text):
                        # Only a real pending action may say "Say yes or no."; the model's question
                        # survives only as the lead-in to the host's own question, without it.
                        offer_text = statement_part(without_host_ask(offer_text))
                    offer, nudged = (offer_text, live and offer_text == said, promised), True
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
                    if offer[2]:
                        # A promise that named no action: never repeat it; say the new reply if it is safe.
                        plain = " ".join(strip_emoji(content).split())
                        safe = plain and not looks_like_tool_text(plain) and not self._risky(plain)
                        emit(plain if safe else "Okay.", segment=True)
                    elif not offer[1]:
                        # No specific action: keep the model's question (never a copied "Say yes or no.").
                        emit(offer[0] or "Okay.", segment=True)
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
                    if turn.explain and is_refusal(reply):
                        # "how do I lock my screen?" -> "I don't know how": give the steps instead.
                        wider = select_tools(turn.request, self.registry, negation_guard=self.guards.negation_guard,
                                             question_guard=False, previous=turn.groups)
                        groups = matched_groups(turn.request)
                        named = [n for n in wider if self.registry.get(n).group in groups]  # Not the fallback set.
                        reply = how_to_answer(named) or reply
                    if web_mismatch(reply, turn.operations):
                        # "Searched on YouTube" when the tool searched DuckDuckGo: say what really happened.
                        reply = host_summary([op.done for op in turn.operations if op.done])
                    if self.guards.citation_strip and not looked_up and _WIKI_CLAIM.search(reply):
                        reply = _WIKI_CLAIM.sub("", reply, count=1).strip()  # Never cite an unchecked source.
                        reply = reply[:1].upper() + reply[1:]
                    undoable = self._undoable()
                    if (not nudged and undoable and "undo" in matched_groups(turn.request)
                            and "undo_last" in turn.tool_names and "undo_last" not in attempted):
                        # "undo that" answered without undoing: one nudge naming what would be undone.
                        nudged = True
                        turn.messages += [{"role": "assistant", "content": content}, {"role": "user", "content":
                                          f"[Aqua host: undo_last can undo {undoable}. The user asked to undo it: "
                                          "call undo_last now.]"}]
                        continue
                    if (not nudged and "youtube" in turn.tool_names and "youtube" not in attempted
                            and _NOW_PLAYING.search(turn.request)):
                        # "what's playing?" answered without looking: one nudge to read it from the tab.
                        nudged = True
                        turn.messages += [{"role": "assistant", "content": content}, {"role": "user", "content":
                                          "[Aqua host: you can read it. Call youtube with action now_playing, then "
                                          "answer from its result.]"}]
                        continue
                    fitting = self._fitting_tools(turn) if not acted and not nudged else []
                    if fitting and self.guards.cant_retry and _DENIAL.search(reply.replace("’", "'")):
                        # "that is not a function available to me" while a shown tool does exactly
                        # this kind of action: one nudge to use it (the policy still decides).
                        nudged = True
                        turn.messages += [{"role": "assistant", "content": content},
                                          {"role": "user", "content": _denial_nudge(fitting)}]
                        continue
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
                    if _HOST_ASK.search(reply):
                        # The host's "Say yes or no." with nothing pending (a yes would do nothing):
                        # the model's yes/no question goes too, and the reply says what really happened.
                        reply = statement_part(without_host_ask(reply)) or (
                            join_phrases([op.done for op in turn.operations]) if acted else
                            "Sorry, I didn't manage to do that." if self._commanded_request(turn) else "Okay.")
                    if not reply:
                        reply = join_phrases([op.done for op in turn.operations]) or (
                            "Done." if acted else "Sorry, I didn't get a reply. Please try again.")
                    reply = self._with_countdown(reply)
                    if already and reply.startswith(already):
                        reply = reply[len(already):].strip()  # The streamed start was already spoken.
                    if reply:
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
                    lead = ""
                    if offer is not None and offer[2]:
                        # "Press Windows + L. I can lock it for you." keeps the explanation, not the promise.
                        lead = " ".join(s for s in _SENTENCE_END.split(offer[0])
                                        if s.strip() and not self._risky(s) and s.strip() not in already)
                    elif offer is not None and not offer[1]:
                        lead = statement_part(offer[0])
                    emit(" ".join(part for part in (done, lead, question) if part), segment=True)
                    self._remember(utterance, "".join(state["spoken"]))
                    return result(kind, kind, turn, status="waiting")
            reply = self._host_reply(turn)
            if reply is not None:
                # Every step succeeded and nothing needs explaining: the host says what was done,
                # without another model call that would only write "I've done X".
                return self._finish_host(turn, utterance, reply, emit, result)
            if self.guards.restate_request:
                self._remind(turn)

    def _host_reply(self, turn: _Turn) -> str | None:
        """The reply the host can write itself, or None when the model must answer: a look-up,
        a reading, a failure or partial result, a tool's own review, or a request with steps left."""
        ops = turn.operations
        if not ops or turn.reviews or turn.explain:
            return None
        if any(op.status not in {"ok", "duplicate"} or op.tool in _KNOWLEDGE_TOOLS or self._reading(op)
               for op in ops):
            return None
        done = [op.done for op in ops if op.status == "ok" and op.done]
        # Opening an app only to type into it ("write hi in notepad") is not a step of its own.
        opens = bool(_OPEN_VERB.search(turn.request))
        covered = sum(op.steps for op in ops if op.status == "ok" and (opens or op.tool != "launch_app"))
        if not done or covered < steps_asked(turn.request):
            return None
        return self._with_countdown(host_summary(done))

    def _reading(self, op: Operation) -> bool:
        """A read-only call (a tool or, for youtube, an action) whose result the model must report."""
        if self._read_only(op.tool):
            return True
        tool = self.registry.get(op.tool)
        try:
            return tool is not None and callable(tool.tier) and tool.tier_for(op.args, self.context) == 0
        except Exception:
            return True

    def _with_countdown(self, reply: str) -> str:
        countdown = self.context.countdown
        if countdown is not None and countdown.fresh:
            countdown.fresh = False
            if "cancel" not in reply.casefold():
                reply += " Say cancel to stop it."
        return reply

    def _finish_host(self, turn: _Turn, utterance: str, reply: str, emit, result) -> AgentTurnResult:
        emit(reply, segment=True)
        self._remember(utterance, reply, turn)
        return result("action", "host-reply", turn)

    def _commanded_request(self, turn: _Turn) -> bool:
        """The request is a direct command (polite forms included), not negated."""
        return direct_command(turn.request) and not (self.guards.negation_guard and wants_no_action(turn.request))

    # Groups whose tools a capability denial is never nudged towards: a search or a look-up is
    # not what "I can't change the default browser" was refusing.
    _NO_DENIAL_NUDGE = frozenset({"web", "knowledge"})

    def _fitting_tools(self, turn: _Turn) -> list[str]:
        """Shown action tools of the groups the request's own words name, for a direct command."""
        if turn.explain or not self._commanded_request(turn):
            return []
        groups = set(matched_groups(turn.request)) - self._NO_DENIAL_NUDGE
        return [name for name in turn.tool_names
                if not self._read_only(name) and self.registry.get(name).group in groups]

    def _promise_offer(self, turn: _Turn, content: str) -> bool:
        """The model promised an action ("I'll open Notepad for you") without a tool call."""
        text = " ".join(strip_emoji(content).split())
        return (self.guards.claim_retry and self.guards.cant_retry and (bool(turn.tool_names) or turn.explain)
                and bool(_PROMISE.search(text)) and classify_reply(turn.request) is None
                and not (self.guards.negation_guard and wants_no_action(turn.request)))

    def _commanded(self, turn: _Turn, tool) -> bool:
        """The request is a direct command for this tool's group ("lock the screen")."""
        return (direct_command(turn.request) and tool.group in matched_groups(turn.request)
                and not (self.guards.negation_guard and wants_no_action(turn.request)))

    def _may_surface(self, turn: _Turn) -> bool:
        """A written call to a tool that was not shown may become a host question (or a read):
        not after "don't ..." requests or a bare yes/no, which must never lead to an action."""
        return classify_reply(turn.request) is None and not (
            self.guards.negation_guard and wants_no_action(turn.request))

    _ANSWER_FILLER = frozenset({"in", "into", "to", "on", "use", "the", "open", "it", "please", "window", "app",
                                "thanks", "thank", "you", "just"})

    def _named_app(self, text: str) -> str | None:
        """The installed app a short answer names ("Notepad", "in notepad please"), if any."""
        words = [w for w in re.sub(r"[^\w\s+#]", " ", text.casefold()).split() if w not in self._ANSWER_FILLER]
        if not words or len(words) > 3:
            return None
        for name in (" ".join(words), "".join(words)):  # Speech splits names: "note pad".
            match = self.context.apps.find(name)
            if match.kind == "found" and match.entry is not None:
                return match.entry.name
        return None

    def _type_into_answer(self, text: str, dictation: str, app: str, emit, result) -> AgentTurnResult:
        turn = _Turn(text, ("launch_app", "type_text"), [], groups=("apps", "input"))
        self._last_groups = turn.groups
        for name, args in (("launch_app", {"app": app}), ("type_text", {"text": dictation, "app": app})):
            call = {"function": {"name": name, "arguments": {**args, "request": "asked"}}}
            turn.messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
            stop = self._handle_call(turn, name, call["function"]["arguments"])
            if stop is not None:
                emit(" ".join(part for part in (join_phrases([op.done for op in turn.operations]), stop[1]) if part))
                self._remember(text, stop[1], turn)
                return result(stop[0], stop[0], turn, status="waiting")
            if turn.operations[-1].status not in {"ok", "unverified", "duplicate"}:
                break
        done = host_summary([op.done for op in turn.operations])
        failed = next((op.message for op in turn.operations if op.status not in {"ok", "unverified", "duplicate"}), "")
        reply = " ".join(part for part in (done, failed) if part) or "Sorry, I couldn't do that."
        emit(reply)
        self._remember(text, reply, turn)
        return result("action" if done else "answer", "complete", turn)

    def _is_offer(self, turn: _Turn, content: str) -> bool:
        """The model asked the user whether to do something while action tools were shown."""
        text = " ".join(strip_emoji(content).split())
        if self.guards.cant_retry and (bool(turn.tool_names) or turn.explain) and _HOST_ASK.search(text):
            return True  # "Putting the PC to sleep? Say yes or no.": the host's phrasing, so always an offer.
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
        if "request" in tool.parameters.get("properties", {}):
            if not turn.offer_mode and self._commanded(turn, tool):
                # "lock the screen": the user asked for exactly this, so it is never "unsure"
                # (the tier still decides whether the host asks first).
                raw_args = _with_request(raw_args, "asked")
            elif turn.offer_mode or not _names_request(raw_args):
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
        if "request" in args and args["request"] != "unsure" and tool.tier_for(args, self.context) >= SAFE:
            if turn.explain:
                args = {**args, "request": "unsure"}  # An explanation question never acts directly.
            elif turn.tainted and not self._asked_for(turn, tool, args):
                # Page text arrived this request and the user's own words did not ask for this
                # action: it may have come from the page, so the host asks first.
                args = {**args, "request": "unsure"}
        decision = self.context.policy.decide(tool, args, self.context)
        if decision.action == DENY:
            turn.messages.append(self._tool_message(tool.name, ToolOutcome("failed", "Not done: " + decision.reason)))
            turn.operations.append(Operation(tool.name, args, "denied", decision.reason))
            return None
        if decision.action == CLARIFY:
            if tool.name == "type_text" and not args.get("app"):
                self._awaiting_text = args["text"]  # Typed once the user names the app.
            turn.messages.pop()  # The question replaces this call; the user's answer is a new request.
            return "clarify", decision.question
        if decision.action == CONFIRM:
            turn.detail = decision.detail
            summary = tool.describe(args) if tool.describe is not None else tool.name.replace("_", " ")
            self.gate.offer(PendingAction(tool.name, args, decision.question, self.clock(), turn.request,
                                          turn.messages, turn.tool_names, turn.decisions, list(turn.operations),
                                          start=turn.start, summary=summary))
            return "confirm", decision.question
        outcome = self._run(tool, args)
        turn.messages.append(self._tool_message(tool.name, outcome))
        turn.operations.append(Operation(tool.name, args, outcome.status, outcome.message, outcome.done, _steps(outcome)))
        if isinstance(outcome.data, dict) and "untrusted_page_data" in outcome.data:
            turn.tainted = True
        if tool.review is not None:
            turn.reviews.append((tool, outcome))
        return None

    def _asked_for(self, turn: _Turn, tool, args: dict) -> bool:
        """The user's own words ask for this action (not just text a page returned)."""
        if tool.name == "youtube":
            words = _YOUTUBE_ASKS.get(args.get("action"))
            return bool(words and words.search(turn.request))
        # Other tools: the user named this kind of action and the request is a command, not a question.
        return tool.group in matched_groups(turn.request) and direct_command(turn.request)

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

    def _risky(self, sentence: str) -> bool:
        """A sentence that claims or promises an action; while streaming it is held back
        until the claim check has run, because spoken words cannot be taken back."""
        if "<tool_call" in sentence or '"name"' in sentence:
            return True  # A written-out tool call after some text is never spoken either.
        if _HOST_ASK.search(sentence):
            return True  # Only a real pending action says "Say yes or no."; checked before it is spoken.
        return self.guards.claim_retry and (unsupported_claim(sentence, set()) or bool(_PROMISE.search(sentence)))

    def _model_round(self, messages, definitions, stream_to):
        """One model decision. Text streams live only before any tool has run, one sentence
        at a time; afterwards it is buffered so only one sentence is spoken.

        Returns (content, completion, held, streamed, claimed): ``held`` means the reply
        starts like a written-out tool call and nothing was streamed; ``claimed`` means a
        sentence claimed or promised an action, so it and the rest were held back and only
        ``streamed`` was spoken.
        """
        guard, parts, completion = AnswerChannelGuard(), [], None
        decided, held, claimed = stream_to is None, False, False
        pending, streamed = "", []

        def release(final: bool) -> None:
            nonlocal pending, claimed
            while pending and not claimed:
                match = _SENTENCE_BREAK.search(pending)
                if match is None and not final:
                    return
                chunk = pending[:match.end()] if match else pending
                if self._risky(chunk):
                    claimed = True
                    return
                stream_to(chunk)
                streamed.append(chunk)
                pending = pending[len(chunk):]

        iterator = self.client.stream_chat(messages, model=self.model, settings=self.settings, tools=definitions)
        try:
            for event in iterator:
                if event.content:
                    visible = guard.feed(event.content)
                    if visible:
                        parts.append(visible)
                        if stream_to is None or held:
                            pass
                        elif not decided:
                            start = "".join(parts).lstrip()
                            if any(prefix.startswith(start) for prefix in _TOOL_TEXT) and len(start) < 16:
                                continue  # Could still become "<tool_call>": wait.
                            decided = True
                            held = start.startswith(_TOOL_TEXT)
                            if not held:
                                pending = "".join(parts)
                                release(False)
                        else:
                            pending += visible
                            release(False)
                if event.done:
                    completion = event
            tail = guard.finish()
            if tail:
                parts.append(tail)
            if stream_to is not None and not held:
                if not decided:
                    held = "".join(parts).lstrip().startswith(_TOOL_TEXT)
                    pending = "" if held else "".join(parts)
                else:
                    pending += tail
                if not held:
                    release(True)
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()
        if completion is None:
            raise OllamaProtocolError("Ollama ended the reply without completing it.")
        return "".join(parts), completion, held, "".join(streamed), claimed

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
        if looks_like_tool_text(reply):
            reply = "Okay."  # Raw tool-call text never enters history (the model would copy it).
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
                if body.pop("untrusted_page_data", None) is not None:
                    # Page text (titles, channels) is never kept: it cannot steer later requests.
                    body.pop("note", None)
                    body["untrusted_page_data"] = "[not kept]"
                pairs.append(({"role": "assistant", "content": "", "tool_calls": call["tool_calls"]},
                              [{**answer, "content": json.dumps(body, ensure_ascii=False)}]))
        return pairs


def build_engine(client, *, model: str, settings: GenerationSettings, max_turns: int = 8,
                 max_chars: int = 12000, desktop=None, policy: Policy | None = None,
                 lookup: bool = True, browser=None) -> AgentEngine:
    """The real session engine: Windows adapter, app index, all tools (look_up unless disabled),
    and the browser bridge (real sessions only; tests pass a desktop and get none)."""
    from aqua.tools import build_registry
    from aqua.tools.app_index import AppIndex
    real_desktop = desktop is None
    if desktop is None:
        from aqua.tools.win32 import Win32Desktop
        desktop = Win32Desktop()
    apps = AppIndex(desktop.app_entries)
    apps.prewarm()
    from aqua.tools.web import read_preferred_browser
    if browser is None and real_desktop:
        from aqua.browser.bridge import BrowserBridge
        browser = BrowserBridge().start()  # Never fails: without the pipe it just stays disconnected.
    context = ToolContext(desktop=desktop, apps=apps,
                          policy=policy if policy is not None else Policy.from_user_config(),
                          preferred_browser=read_preferred_browser(), browser=browser)
    conversation = Conversation(max_turns=max_turns, max_chars=max_chars, system_prompt=SYSTEM_PROMPT)
    return AgentEngine(client, model=model, settings=settings, registry=build_registry(lookup=lookup),
                       context=context, conversation=conversation)


__all__ = ["AgentEngine", "AgentTask", "AgentTurnResult", "MAX_DECISIONS", "MAX_OPERATIONS",
           "Operation", "build_engine", "first_sentence", "join_phrases", "strip_emoji"]
