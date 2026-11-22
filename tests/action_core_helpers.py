"""Shared fakes for the new action core: a scripted model and an engine on a fake desktop."""

from copy import deepcopy
from datetime import datetime, timezone

from aqua.agent.context import ToolContext
from aqua.agent.loop import AgentEngine
from aqua.agent.policy import Policy
from aqua.brain.ollama import ChatEvent, GenerationSettings, _copy_messages, _copy_tools
from aqua.tools import build_registry
from aqua.tools.app_index import AppIndex
from aqua.tools.fake import FakeDesktop

FIXED_NOW = datetime(2026, 11, 22, 15, 42, tzinfo=timezone.utc)


def call(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


class ScriptedModel:
    """One scripted decision per request: a string answers, a list proposes tool calls.

    Every request is checked with the real Ollama client's message and tool
    validators, so anything the engine sends would also be accepted live.
    """

    def __init__(self, *rounds):
        self.rounds = list(rounds)
        self.requests = []

    def stream_chat(self, messages, *, model, settings, tools=None):
        _copy_messages(messages)
        _copy_tools(tools)
        self.requests.append({"messages": deepcopy(messages),
                              "tools": [tool["function"]["name"] for tool in tools or []]})
        if not self.rounds:
            raise AssertionError("The model was asked for more decisions than scripted.")
        item = self.rounds.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str):
            # Stream in two chunks, like Ollama does.
            middle = len(item) // 2
            return iter([ChatEvent(item[:middle]), ChatEvent(item[middle:]), ChatEvent(done=True)])
        return iter([ChatEvent(done=True, tool_calls=tuple(item))])

    @property
    def last_messages(self):
        return self.requests[-1]["messages"]

    def tool_results(self, index=-1):
        return [m for m in self.requests[index]["messages"] if m["role"] == "tool"]


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value


def make_engine(*rounds, desktop=None, registry=None, policy=None, clock=None):
    desktop = desktop if desktop is not None else FakeDesktop()
    model = ScriptedModel(*rounds)
    context = ToolContext(desktop=desktop, apps=AppIndex(desktop.app_entries, clock=desktop.monotonic),
                          policy=policy if policy is not None else Policy())
    engine = AgentEngine(model, model="test-model", settings=GenerationSettings(),
                         registry=registry if registry is not None else build_registry(),
                         context=context, clock=clock if clock is not None else Clock(), now=lambda: FIXED_NOW)
    return engine, desktop, model
