"""Small Ollama chat client restricted to the local IPv4 loopback service.

Uses the standard library only. No proxy settings, redirects, remote hosts,
model installation, command execution, or persistence are exposed.
"""

from collections.abc import Iterator, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from http.client import HTTPConnection, HTTPException
import json
from math import isfinite
import re
import socket
from time import perf_counter

HOST = "127.0.0.1"
PORT = 11434
DEFAULT_CHAT_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
DEFAULT_CONTEXT_SIZE = 4096
DEFAULT_TEMPERATURE = 0.2
DEFAULT_KEEP_ALIVE = "5m"
MAX_LINE_BYTES = 1_048_576
MAX_RESPONSE_CHARACTERS = 131_072
MAX_TOOL_CALLS = 8
MAX_TOOL_ARGUMENT_BYTES = 8192
MAX_TOOL_DEFINITION_BYTES = 16384

# This exact official OCI manifest identifies the dedicated non-thinking model.
# Ollama 0.40.16 can incorrectly advertise "thinking" for its Qwen3 architecture.
# Verified source: https://registry.ollama.ai/v2/library/qwen3/manifests/4b-instruct-2507-q4_K_M
# Names and tags alone are not trusted; changed manifests use normal detection.
VERIFIED_NON_THINKING_DIGESTS = frozenset({
    "0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0",
    # qwen3:4b-instruct-2507-q4_K_M as re-pulled in Nov 2026 (newer Ollama lists a "thinking"
    # capability because the template re-renders past reasoning); probes returned no thinking.
    "6214d088d3846e16c9fe00adcbabdbfddc61ce4a70daacc46cb2310c38603054",
})

# The installed models below use Ollama's built-in renderer (their public
# template is only {{ .Prompt }}). Exact manifest identities, local capability
# metadata, and execution-disabled on/off probes establish this control; a
# similarly named replacement does not inherit it.
VERIFIED_THINKING_CONTROL_DIGESTS = frozenset({
    "56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90",
    "c6eb396dbd5992bbe3f5cdb947e8bbc0ee413d7c17e2beaae69f5d569cf982eb",
})


class OllamaError(RuntimeError):
    """The local conversational backend could not complete a request."""


class OllamaConnectionError(OllamaError):
    """The loopback service is unavailable or disconnected."""


class OllamaModelError(OllamaError):
    """The requested model is not installed in the local Ollama service."""


class OllamaProtocolError(OllamaError):
    """Ollama returned malformed or incomplete stream data."""


class OllamaTimeoutError(OllamaError):
    """A local inference request exceeded its configured timeout."""


class OllamaThinkingError(OllamaModelError):
    """The selected model cannot honor the requested reasoning control."""


class OllamaGenerationLimitError(OllamaError):
    """An unusable token-limited response, with reasoning-free diagnostics.

    The diagnostic event is not stream completion and cannot authorize tools.
    It preserves the server's reported work for benchmarks without retaining
    incomplete answer text, tool proposals, or the reasoning channel.
    """

    def __init__(self, completion: "ChatEvent") -> None:
        super().__init__("Ollama reached the response token limit before completing its reply. Increase --max-tokens and retry.")
        self.completion = completion
        self.done_reason = "length"


class _UnsupportedThinking(OllamaError):
    """Internal signal for one compatible retry on an older local API."""


_KEEP_ALIVE = re.compile(r"0|[0-9]{1,6}[smh]")


def parse_keep_alive(value: object) -> str:
    """Accept only an Ollama duration such as 5m, 30m, 1h, 90s, or 0 (unload now)."""
    if type(value) is not str or not _KEEP_ALIVE.fullmatch(value):
        raise ValueError("Keep-alive must be digits followed by s, m or h (for example 5m, 30m, 1h), or 0.")
    return value


@dataclass(frozen=True, slots=True)
class GenerationSettings:
    temperature: float = DEFAULT_TEMPERATURE
    num_predict: int = 1024
    num_ctx: int = DEFAULT_CONTEXT_SIZE
    thinking: str = "auto"
    seed: int | None = None
    top_p: float | None = None
    top_k: int | None = None
    presence_penalty: float | None = None
    repeat_penalty: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float)) or not isfinite(self.temperature) or not 0 <= self.temperature <= 2:
            raise ValueError("Temperature must be finite and between 0 and 2.")
        if isinstance(self.num_predict, bool) or not isinstance(self.num_predict, int) or not 1 <= self.num_predict <= 4096:
            raise ValueError("Response token limit must be an integer from 1 to 4096.")
        if isinstance(self.num_ctx, bool) or not isinstance(self.num_ctx, int) or not 512 <= self.num_ctx <= 32768:
            raise ValueError("Context window must be an integer from 512 to 32768.")
        if type(self.thinking) is not str or self.thinking not in {"auto", "on", "off"}:
            raise ValueError("Thinking must be auto, on, or off.")
        if self.seed is not None and (type(self.seed) is not int or not 0 <= self.seed <= 2**31 - 1):
            raise ValueError("Seed must be an integer from 0 to 2147483647.")
        if self.top_k is not None and (type(self.top_k) is not int or not 0 <= self.top_k <= 1000):
            raise ValueError("Top-k must be an integer from 0 to 1000.")
        for name, value, lower, upper in (
            ("top_p", self.top_p, 0, 1),
            ("presence_penalty", self.presence_penalty, -2, 2),
            ("repeat_penalty", self.repeat_penalty, 0, 4),
        ):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not lower <= value <= upper):
                raise ValueError(f"{name} must be finite and between {lower} and {upper}.")


@dataclass(frozen=True, slots=True)
class ModelInfo:
    name: str
    version: str
    family: str = ""
    thinking_control: str = "hidden"
    thinking_options: tuple[str, ...] = ("auto",)
    digest: str | None = None


@dataclass(frozen=True, slots=True)
class ChatEvent:
    content: str = ""
    done: bool = False
    total_duration: int | None = None
    eval_count: int | None = None
    eval_duration: int | None = None
    load_duration: int | None = None
    prompt_eval_count: int | None = None
    prompt_eval_duration: int | None = None
    first_stream_seconds: float | None = None
    first_thinking_seconds: float | None = None
    first_content_seconds: float | None = None
    thinking_chunks: int = 0
    thinking_characters: int = 0
    # Proposals remain untrusted. They are emitted only on successful stream
    # completion; the separate executor validates names, arguments and consent.
    tool_calls: tuple[dict, ...] = ()


def _server_statistic(item: dict, name: str) -> int | None:
    """Ignore malformed optional counters/timings without echoing raw data."""
    value = item.get(name)
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _model_capabilities(item: dict) -> set[str]:
    """An absent/empty declaration is unknown, never proof of non-thinking."""
    capabilities = item.get("capabilities", [])
    if not isinstance(capabilities, list) or any(
        not isinstance(value, str) or not value or value != value.strip()
        for value in capabilities
    ):
        raise OllamaProtocolError("Ollama returned invalid model capabilities.")
    return {value.lower() for value in capabilities}


def _model_name(model: str) -> str:
    if not isinstance(model, str) or not model.strip() or len(model) > 200 or any(character.isspace() or ord(character) < 32 for character in model):
        raise ValueError("Choose a nonempty installed Ollama model name, such as qwen3:4b.")
    return model


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _decode_object(raw: bytes) -> dict:
    try:
        # Preserve graceful handling of malformed optional timing statistics.
        # Nonfinite tool arguments are rejected separately by _tool_call's
        # allow_nan=False serialization before they reach authorization.
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise OllamaProtocolError("The local Ollama service returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise OllamaProtocolError("The local Ollama service returned an unexpected response format.")
    return value


def _tool_call(value: object) -> dict:
    """Validate native framing, preserving argument values for the executor.

    This is deliberately not a tool allowlist: an unknown name or malformed
    argument schema can be reported safely by the trusted execution layer.
    Incomplete name/argument *fragments* cannot be mistaken for a complete call.
    """
    if not isinstance(value, dict) or not set(value) <= {"id", "type", "function"}:
        raise OllamaProtocolError("Ollama returned invalid tool-call framing.")
    function = value.get("function")
    if not isinstance(function, dict) or not {"name", "arguments"} <= set(function) or not set(function) <= {"name", "arguments", "index"}:
        raise OllamaProtocolError("Ollama returned an incomplete tool call.")
    name = function["name"]
    if not isinstance(name, str) or not name or len(name) > 128 or any(character.isspace() or ord(character) < 32 for character in name):
        raise OllamaProtocolError("Ollama returned an invalid tool name.")
    if "index" in function and (type(function["index"]) is not int or not 0 <= function["index"] < MAX_TOOL_CALLS):
        raise OllamaProtocolError("Ollama returned an invalid tool-call index.")
    if "id" in value and (not isinstance(value["id"], str) or not value["id"] or len(value["id"]) > 256 or any(ord(character) < 32 for character in value["id"])):
        raise OllamaProtocolError("Ollama returned an invalid tool-call identifier.")
    if "type" in value and value["type"] != "function":
        raise OllamaProtocolError("Ollama returned an unsupported tool-call type.")
    try:
        encoded = json.dumps(function["arguments"], allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        raise OllamaProtocolError("Ollama returned invalid tool arguments.") from exc
    if len(encoded) > MAX_TOOL_ARGUMENT_BYTES:
        raise OllamaProtocolError("Ollama tool arguments exceeded their size limit.")
    return deepcopy(value)


def _copy_messages(messages: Sequence[dict]) -> list[dict]:
    """Retain native tool history and reject orphaned/mismatched results."""
    copied, pending = [], []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise ValueError("Chat messages require a valid role and text content.")
        role = message["role"]
        content = message.get("content", "" if role == "assistant" and message.get("tool_calls") else None)
        if not isinstance(content, str):
            raise ValueError("Chat messages require text content.")
        item = {"role": role, "content": content}
        if role == "tool":
            if not pending:
                raise ValueError("A tool result requires a preceding assistant tool call.")
            name, call_id = message.get("tool_name"), message.get("tool_call_id")
            if name is not None and (not isinstance(name, str) or not name):
                raise ValueError("A tool result requires a valid tool name.")
            if call_id is not None and (not isinstance(call_id, str) or not call_id):
                raise ValueError("A tool result requires a valid call identifier.")
            candidates = [call for call in pending if
                          (name is None or call["function"]["name"] == name) and
                          (call_id is None or call.get("id") == call_id)]
            if len(candidates) != 1:
                raise ValueError("A tool result must match exactly one pending call.")
            pending.remove(candidates[0])
            for key in ("tool_name", "tool_call_id"):
                if key in message:
                    item[key] = message[key]
            if "tool_calls" in message:
                raise ValueError("Tool results cannot contain tool calls.")
        else:
            if pending:
                raise ValueError("Every assistant tool call requires a matching result.")
            if "tool_name" in message or "tool_call_id" in message:
                raise ValueError("Only a tool result can name a completed call.")
            if "tool_calls" in message:
                values = message["tool_calls"]
                if role != "assistant" or not isinstance(values, (list, tuple)) or len(values) > MAX_TOOL_CALLS:
                    raise ValueError("Only an assistant can propose a bounded tool-call list.")
                try:
                    pending = [_tool_call(value) for value in values]
                except OllamaProtocolError as exc:
                    raise ValueError("Assistant tool-call history is malformed.") from exc
                identifiers = [call["id"] for call in pending if "id" in call]
                if len(identifiers) != len(set(identifiers)):
                    raise ValueError("Assistant tool-call identifiers must be unique.")
                item["tool_calls"] = deepcopy(pending)
        copied.append(item)
    if not copied or copied[-1]["role"] not in {"user", "tool"} or pending:
        raise ValueError("A chat request must end with a user message or paired tool results.")
    return copied


def _copy_tools(tools: list[dict] | None) -> list[dict] | None:
    if tools is None:
        return None
    if not isinstance(tools, list) or len(tools) > MAX_TOOL_CALLS or any(not isinstance(tool, dict) for tool in tools):
        raise ValueError("Tools must be a bounded list of function definitions.")
    try:
        raw = json.dumps(tools, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Tool definitions must contain valid JSON.") from exc
    if len(raw) > MAX_TOOL_DEFINITION_BYTES:
        raise ValueError("Tool definitions exceeded their size limit.")
    return deepcopy(tools)


def _close(connection, response=None) -> None:
    for resource in (response, connection):
        if resource is not None:
            try:
                resource.close()
            except (OSError, HTTPException):
                pass  # Cleanup must not hide cancellation or the original error.


def _supports_disabled_thinking(template: object) -> bool:
    """Recognize a small set of explicit generation-prefix controls.

    Merely mentioning ``.Think`` is insufficient: historical-message branches
    and comments can do that while the generation prefix still opens a thought
    block unconditionally. Unknown Go-template syntax remains in hidden mode.
    This checks rendering structure, not model-produced prose.
    """
    if not isinstance(template, str):
        return False
    cleaned = re.sub(r"{{-?\s*/\*.*?\*/\s*-?}}", "", template, flags=re.DOTALL)
    header = "<|im_start|>assistant"
    if header not in cleaned:
        return False
    suffix = cleaned.rsplit(header, 1)[1]

    def condition(expression: str, think: bool) -> tuple[bool, bool] | None:
        expression = " ".join(expression.replace("$.", ".").split())
        # These are known Qwen generation-prefix forms. .IsThinkSet is true
        # because the candidate request explicitly includes think:false.
        values = {
            ".Think": (think, True),
            "not .Think": (not think, True),
            ".IsThinkSet": (True, False),
            "not .IsThinkSet": (False, False),
            "and .IsThinkSet .Think": (think, True),
            "and .IsThinkSet (not .Think)": (not think, True),
            "and .Think .IsThinkSet": (think, True),
            "and (not .Think) .IsThinkSet": (not think, True),
        }
        return values.get(expression)

    def render(think: bool) -> tuple[str, bool] | None:
        # Stack entries: outer active, conditional result, Think-controlled,
        # and whether an else branch has already occurred.
        stack: list[tuple[bool, bool, bool, bool]] = []
        active = True
        controlled_opener = False
        outer_ended = False
        output = []
        for part in re.split(r"({{.*?}})", suffix, flags=re.DOTALL):
            if not part:
                continue
            if not part.startswith("{{"):
                if outer_ended and part.strip():
                    return None
                if re.search(r"<think>", part, flags=re.IGNORECASE) and any(entry[2] for entry in stack):
                    controlled_opener = True
                if active:
                    output.append(part)
                continue
            command = part[2:-2].strip().strip("-").strip()
            if command == "end":
                if stack:
                    outer_active, _result, _controlled, _else_seen = stack.pop()
                    active = outer_active
                else:
                    # The suffix can close an enclosing last-message branch
                    # or range whose opening precedes the literal header.
                    outer_ended = True
            elif outer_ended:
                return None
            elif command.startswith("if "):
                evaluated = condition(command[3:], think)
                if evaluated is None:
                    return None
                result, controlled = evaluated
                stack.append((active, result, controlled, False))
                active = active and result
            elif command == "else" and stack:
                outer_active, result, controlled, else_seen = stack[-1]
                if else_seen:
                    return None
                stack[-1] = (outer_active, result, controlled, True)
                active = outer_active and not result
            else:
                return None
        if stack:
            return None
        return "".join(output), controlled_opener

    disabled, enabled = render(False), render(True)
    if disabled is None or enabled is None or not disabled[1]:
        return False
    false_prefix, true_prefix = disabled[0], enabled[0]
    remaining = re.sub(r"<think>\s*</think>", "", false_prefix, flags=re.IGNORECASE)
    return not remaining.strip() and false_prefix.strip() != true_prefix.strip()


class OllamaClient:
    """Stream chat only from http://127.0.0.1:11434, with bounded responses."""

    def __init__(self, *, timeout: float = 120.0, keep_alive: str = DEFAULT_KEEP_ALIVE,
                 num_thread: int | None = None) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not isfinite(timeout) or not 0 < timeout <= 600:
            raise ValueError("Ollama timeout must be finite, positive, and at most 600 seconds.")
        self.timeout = float(timeout)
        self.keep_alive = parse_keep_alive(keep_alive)
        if num_thread is not None and (type(num_thread) is not int or not 1 <= num_thread <= 64):
            raise ValueError("Ollama thread count must be an integer from 1 to 64.")
        # CPU threads for the model's CPU-resident layers; None leaves Ollama's default.
        self.num_thread = num_thread
        self._thinking_controls: dict[str, str] = {}
        self._thinking_options: dict[str, tuple[str, ...]] = {}
        self.chat_request_count = 0

    def _connection(self):
        # HTTPConnection uses this literal address directly, never HTTP_PROXY.
        return HTTPConnection(HOST, PORT, timeout=self.timeout)

    def _json(self, path: str, *, body: dict | None = None) -> dict:
        connection = self._connection()
        response = None
        try:
            headers = {"Accept": "application/json", "Connection": "close"}
            if body is None:
                connection.request("GET", path, headers=headers)
            else:
                headers["Content-Type"] = "application/json"
                connection.request("POST", path, body=json.dumps(body).encode("utf-8"), headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_LINE_BYTES + 1)
            if len(raw) > MAX_LINE_BYTES:
                raise OllamaProtocolError("The local Ollama metadata response exceeded its size limit.")
            if response.status != 200:
                self._http_error(response.status, raw)
            return _decode_object(raw)
        except (TimeoutError, socket.timeout) as exc:
            raise OllamaTimeoutError("The local Ollama service timed out. Retry or increase --timeout.") from exc
        except (OSError, HTTPException) as exc:
            raise OllamaConnectionError("Cannot reach Ollama at http://127.0.0.1:11434. Start the local Ollama application and retry.") from exc
        finally:
            _close(connection, response)

    def check_model(self, model: str) -> ModelInfo:
        """Inspect installed models; never invoke pull, create, or delete APIs."""
        name = _model_name(model)
        version = self._json("/api/version").get("version")
        tags = self._json("/api/tags").get("models")
        if not isinstance(version, str) or not isinstance(tags, list):
            raise OllamaProtocolError("Ollama version or installed-model metadata is incomplete.")
        aliases = {name, name + ":latest"} if ":" not in name.rsplit("/", 1)[-1] else {name}
        for item in tags:
            if not isinstance(item, dict):
                raise OllamaProtocolError("Ollama returned invalid installed-model metadata.")
            installed = item.get("name", item.get("model"))
            if not isinstance(installed, str):
                raise OllamaProtocolError("Ollama returned an invalid installed-model name.")
            if installed in aliases:
                details = item.get("details", {})
                family = details.get("family", "") if isinstance(details, dict) else ""
                family = family if isinstance(family, str) else ""
                capabilities = _model_capabilities(item)
                digest = item.get("digest")
                verified_non_thinking = isinstance(digest, str) and digest in VERIFIED_NON_THINKING_DIGESTS
                verified_native_control = isinstance(digest, str) and digest in VERIFIED_THINKING_CONTROL_DIGESTS
                thinking = family.lower() == "qwen3" or self._qwen_name(name) or "thinking" in capabilities
                control = "disable_requested"
                options = ("auto",)
                if verified_non_thinking or (capabilities and "thinking" not in capabilities):
                    # Dedicated Instruct variants can share the Qwen3 family
                    # and name prefix without having a thinking channel.
                    control = "non_thinking"
                    options = ("auto", "off")
                elif thinking or verified_native_control or family.lower() in {"qwen35", "gemma4"}:
                    # Thinking-only and some older Qwen templates hardcode an opening think
                    # block. think:false can disable Ollama's reasoning parser
                    # while that template still causes raw reasoning output.
                    # Inspect local template metadata before requesting it.
                    control = "hidden"
                    shown = self._json("/api/show", body={"model": installed})
                    shown_capabilities = _model_capabilities(shown)
                    if shown_capabilities and "thinking" not in shown_capabilities:
                        control = "non_thinking"
                        options = ("auto", "off")
                    elif verified_native_control and "thinking" in shown_capabilities:
                        # These exact local weights use the server's built-in
                        # renderer, not a visible Go generation-prefix branch.
                        control = "native_toggle"
                        options = ("auto", "on", "off")
                    elif _supports_disabled_thinking(shown.get("template")):
                        control = "disable_requested"
                        options = ("auto", "on", "off")
                    elif thinking or "thinking" in shown_capabilities:
                        options = ("auto", "on")
                for alias in aliases | {installed}:
                    self._thinking_controls[alias] = control
                    self._thinking_options[alias] = options
                return ModelInfo(installed, version, family, control, options, digest if isinstance(digest, str) else None)
        raise OllamaModelError(f"Ollama model {name!r} is not installed locally. Select an installed model with --model. Aqua never downloads Ollama models.")

    def validate_thinking(self, model: str, thinking: str) -> bool | None:
        """Return the supported API flag, or raise before inference starts.

        auto preserves the local model's default for unknown/thinking-only and
        native-rendered models, disables recognized switchable legacy templates,
        and omits the flag for a verified non-thinking model. Explicit requests
        require inspected local metadata and are never silently retried away.
        """
        name = _model_name(model)
        if type(thinking) is not str or thinking not in {"auto", "on", "off"}:
            raise ValueError("Thinking must be auto, on, or off.")
        if thinking != "auto" and name not in self._thinking_options:
            self.check_model(name)
        control = self._thinking_controls.get(name, "hidden" if self._qwen_name(name) else "disable_requested")
        if thinking == "auto":
            return False if control == "disable_requested" else None
        if thinking not in self._thinking_options.get(name, ("auto",)):
            raise OllamaThinkingError(
                f"The selected local model does not have verified support for --thinking {thinking}. "
                "Use --thinking auto or select a model with that supported control."
            )
        if control == "non_thinking":
            return None  # Its weights have no thinking mode to turn off.
        return thinking == "on"

    @staticmethod
    def _qwen_name(name: str) -> bool:
        return name.rsplit("/", 1)[-1].split(":", 1)[0].lower().startswith("qwen3")

    @staticmethod
    def _http_error(status: int, raw: bytes, *, allow_thinking_retry: bool = False) -> None:
        # Examine a bounded server error solely for compatibility classification;
        # never echo arbitrary response bodies or conversation text to the UI.
        message = ""
        try:
            value = _decode_object(raw)
            error = value.get("error", "")
            if isinstance(error, str):
                message = error.lower()
        except OllamaProtocolError:
            pass
        if status == 400 and "think" in message and any(word in message for word in ("unsupported", "not support", "unknown")):
            if allow_thinking_retry:
                raise _UnsupportedThinking()
            raise OllamaThinkingError("The local server rejected the requested thinking control (HTTP 400). Use --thinking auto or a supported model; no silent retry was made.")
        if status == 404:
            raise OllamaModelError("The selected Ollama model is unavailable locally. Check the model name; Aqua will not download it.")
        raise OllamaError(f"The local Ollama service rejected the chat request (HTTP {status}). Check its status and generation settings.")

    def stream_chat(
        self,
        messages: Sequence[dict],
        *,
        model: str,
        settings: GenerationSettings,
        tools: list[dict] | None = None,
    ) -> Iterator[ChatEvent]:
        """Stream answer content and completed proposals, never reasoning text."""
        payload = self._payload(messages, model=model, settings=settings, tools=tools, streaming=True)
        yield from self._request_events(payload, settings=settings)

    def chat(
        self, messages: Sequence[dict], *, model: str, settings: GenerationSettings,
        tools: list[dict] | None = None,
    ) -> ChatEvent:
        """Read one non-streaming answer using the same validation contract.

        Buffered responses cannot reveal token arrival or reasoning phase times;
        their first-stream/thinking/content timestamps therefore remain None.
        Reasoning text is discarded before the completed event is returned.
        """
        payload = self._payload(messages, model=model, settings=settings, tools=tools, streaming=False)
        events = list(self._request_events(payload, settings=settings))
        if len(events) != 1 or not events[0].done:
            raise OllamaProtocolError("The local Ollama service returned an incomplete non-streaming response.")
        # The interactive controller applies this same structural channel guard
        # to streams. A direct buffered caller must receive the same protection.
        from .filtering import AnswerChannelGuard
        guard = AnswerChannelGuard()
        answer = guard.feed(events[0].content) + guard.finish()
        return replace(events[0], content=answer)

    def _payload(self, messages, *, model, settings, tools, streaming) -> dict:
        name = _model_name(model)
        copied = _copy_messages(messages)
        definitions = _copy_tools(tools)
        think = self.validate_thinking(name, settings.thinking)
        payload = {
            "model": name,
            "messages": copied,
            "stream": streaming,
            "options": {"temperature": settings.temperature, "num_predict": settings.num_predict, "num_ctx": settings.num_ctx},
            "keep_alive": self.keep_alive,
        }
        if self.num_thread is not None:
            payload["options"]["num_thread"] = self.num_thread
        for option in ("seed", "top_p", "top_k", "presence_penalty", "repeat_penalty"):
            value = getattr(settings, option)
            if value is not None:
                payload["options"][option] = value
        if definitions is not None:
            payload["tools"] = definitions
        if think is not None:
            payload["think"] = think
        return payload

    def _request_events(self, payload: dict, *, settings: GenerationSettings) -> Iterator[ChatEvent]:
        # Only auto can fall back after a specifically unsupported API field.
        # An explicit on/off request must either be honored or fail clearly.
        try:
            yield from self._stream(payload, allow_thinking_retry=settings.thinking == "auto" and "think" in payload,
                                    require_no_thinking=settings.thinking == "off")
        except _UnsupportedThinking:
            payload.pop("think")
            yield from self._stream(payload, allow_thinking_retry=False)

    def _stream(self, payload: dict, *, allow_thinking_retry: bool, require_no_thinking: bool = False) -> Iterator[ChatEvent]:
        connection = self._connection()
        response = None
        started = perf_counter()
        characters = 0
        first_stream = first_thinking = first_content = None
        thinking_chunks = thinking_characters = 0
        tool_calls = []
        call_keys = {}
        try:
            self.chat_request_count += 1
            connection.request("POST", "/api/chat", body=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Accept": "application/x-ndjson" if payload["stream"] else "application/json", "Connection": "close"})
            # getresponse() can detach connection.sock when Connection: close
            # transfers ownership to the response. Retain the live socket so
            # subsequent reads still honor the remaining total time budget.
            stream_socket = connection.sock
            response = connection.getresponse()
            if response.status != 200:
                raw = response.read(MAX_LINE_BYTES + 1)
                self._http_error(response.status, raw[:MAX_LINE_BYTES], allow_thinking_retry=allow_thinking_retry)
            while True:
                remaining = self.timeout - (perf_counter() - started)
                if remaining <= 0:
                    raise OllamaTimeoutError("Ollama generation exceeded --timeout; the stream was closed.")
                # A completed Connection: close response can release the
                # retained socket. The wall deadline still applies after EOF.
                if stream_socket is not None and not (getattr(response, "isclosed", lambda: False)() is True):
                    stream_socket.settimeout(remaining)
                raw = response.readline(MAX_LINE_BYTES + 1) if payload["stream"] else response.read(MAX_LINE_BYTES + 1)
                received = perf_counter()
                if received - started > self.timeout:
                    raise OllamaTimeoutError("Ollama generation exceeded --timeout; the stream was closed.")
                if not raw:
                    raise OllamaProtocolError("Ollama disconnected before completing its response. The partial turn was not completed.")
                if len(raw) > MAX_LINE_BYTES:
                    raise OllamaProtocolError("An Ollama stream chunk exceeded its size limit.")
                if not raw.strip():
                    continue
                item = _decode_object(raw)
                if item.get("error"):
                    raise OllamaError("Ollama reported an inference error. Check the local service and selected model.")
                message = item.get("message", {})
                if not isinstance(message, dict):
                    raise OllamaProtocolError("Ollama returned an invalid message chunk.")
                content = message.get("content", "")
                thinking = message.get("thinking", "")
                done = item.get("done", False)
                if not isinstance(content, str) or not isinstance(thinking, str) or not isinstance(done, bool):
                    raise OllamaProtocolError("Ollama returned invalid stream content or completion status.")
                if not payload["stream"] and not done:
                    raise OllamaProtocolError("Ollama returned an incomplete non-streaming response.")
                if require_no_thinking and thinking:
                    raise OllamaThinkingError("The model returned a reasoning channel despite --thinking off. No reasoning or tool proposal from that response was exposed.")
                incoming = message.get("tool_calls", [])
                if not isinstance(incoming, list) or len(incoming) > MAX_TOOL_CALLS:
                    raise OllamaProtocolError("Ollama returned invalid tool-call framing.")
                if incoming and not payload.get("tools"):
                    raise OllamaProtocolError("Ollama proposed tools while tool use was disabled.")
                for value in incoming:
                    call = _tool_call(value)
                    keys = []
                    if "id" in call:
                        keys.append(("id", call["id"]))
                    if "index" in call["function"]:
                        keys.append(("index", call["function"]["index"]))
                    seen = {call_keys[key] for key in keys if key in call_keys}
                    if seen:
                        if len(seen) != 1 or tool_calls[next(iter(seen))] != call:
                            raise OllamaProtocolError("Ollama changed an already proposed tool call.")
                        continue
                    if len(tool_calls) >= MAX_TOOL_CALLS:
                        raise OllamaProtocolError("Ollama proposed too many tool calls.")
                    for key in keys:
                        call_keys[key] = len(tool_calls)
                    tool_calls.append(call)
                if payload["stream"] and first_stream is None and (content or thinking or incoming):
                    first_stream = received - started
                if thinking:
                    if payload["stream"] and first_thinking is None:
                        first_thinking = received - started
                    thinking_chunks += 1
                    thinking_characters += len(thinking)
                if payload["stream"] and first_content is None and content.strip():
                    first_content = received - started
                characters += len(content)
                if characters > MAX_RESPONSE_CHARACTERS:
                    raise OllamaProtocolError("Ollama response exceeded its size limit; the stream was closed.")
                if done and item.get("done_reason") == "length":
                    if content:
                        yield ChatEvent(content=content)
                    raise OllamaGenerationLimitError(ChatEvent(
                        total_duration=_server_statistic(item, "total_duration"),
                        eval_count=_server_statistic(item, "eval_count"),
                        eval_duration=_server_statistic(item, "eval_duration"),
                        load_duration=_server_statistic(item, "load_duration"),
                        prompt_eval_count=_server_statistic(item, "prompt_eval_count"),
                        prompt_eval_duration=_server_statistic(item, "prompt_eval_duration"),
                        first_stream_seconds=first_stream,
                        first_thinking_seconds=first_thinking,
                        first_content_seconds=first_content,
                        thinking_chunks=thinking_chunks,
                        thinking_characters=thinking_characters,
                    ))
                if content or done:
                    yield ChatEvent(content=content, done=done,
                                    total_duration=_server_statistic(item, "total_duration"),
                                    eval_count=_server_statistic(item, "eval_count"),
                                    eval_duration=_server_statistic(item, "eval_duration"),
                                    load_duration=_server_statistic(item, "load_duration"),
                                    prompt_eval_count=_server_statistic(item, "prompt_eval_count"),
                                    prompt_eval_duration=_server_statistic(item, "prompt_eval_duration"),
                                    first_stream_seconds=first_stream if done else None,
                                    first_thinking_seconds=first_thinking if done else None,
                                    first_content_seconds=first_content if done else None,
                                    thinking_chunks=thinking_chunks if done else 0,
                                    thinking_characters=thinking_characters if done else 0,
                                    tool_calls=tuple(deepcopy(tool_calls)) if done else ())
                if done:
                    return
        except (TimeoutError, socket.timeout) as exc:
            raise OllamaTimeoutError("The local Ollama response timed out. Retry or increase --timeout.") from exc
        except (OSError, HTTPException) as exc:
            raise OllamaConnectionError("The connection to local Ollama failed or was interrupted. Check that Ollama is still running.") from exc
        finally:
            _close(connection, response)
