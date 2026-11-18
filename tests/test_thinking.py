"""Thinking controls use inspected local metadata and never expose reasoning."""

import io
import json
import math
import unittest
from unittest.mock import Mock, patch

from aqua.brain.ollama import (
    GenerationSettings, OllamaClient, OllamaGenerationLimitError, OllamaProtocolError, OllamaThinkingError,
    VERIFIED_NON_THINKING_DIGESTS, VERIFIED_THINKING_CONTROL_DIGESTS,
)


def encoded(value):
    return json.dumps(value).encode("utf-8")


def wire(*items):
    return b"\n".join(encoded(item) for item in items) + b"\n"


class Response:
    def __init__(self, body=b"", status=200):
        self.status, self.closed = status, False
        self.buffer = io.BytesIO(body)

    def read(self, size):
        return self.buffer.read(size)

    def readline(self, size):
        return self.buffer.readline(size)

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, response):
        self.response = response
        self.request, self.sock = Mock(), Mock()
        self.closed = False

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


NATIVE = (
    ("qwen3.5:9b-q4_K_M", "qwen35", "56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90"),
    ("gemma4:latest", "gemma4", "c6eb396dbd5992bbe3f5cdb947e8bbc0ee413d7c17e2beaae69f5d569cf982eb"),
)
CALL = {"function": {"name": "open_application", "arguments": {"app_id": "notepad"}}}
TOOLS = [{"type": "function", "function": {"name": "open_application", "parameters": {"type": "object"}}}]


class ThinkingTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Thinking unit tests must stay offline."))
            manager.start()
            self.addCleanup(manager.stop)
        self.messages = [{"role": "user", "content": "A fixed test question."}]

    def responses(self, *responses):
        connections = [Connection(response) for response in responses]
        manager = patch("aqua.brain.ollama.HTTPConnection", side_effect=connections)
        self.factory = manager.start()
        self.addCleanup(manager.stop)
        return connections

    def native_client(self, *response, model=NATIVE[0], shown=None):
        name, family, digest = model
        connections = self.responses(
            Response(encoded({"version": "0.40.16"})),
            Response(encoded({"models": [{"name": name, "digest": digest, "details": {"family": family}}]})),
            Response(encoded(shown if shown is not None else {"capabilities": ["completion", "thinking", "tools"], "template": "{{ .Prompt }}"})),
            *response,
        )
        client = OllamaClient()
        info = client.check_model(name)
        return client, info, connections

    def test_defaults_preserve_auto_and_unseeded_requests(self):
        settings = GenerationSettings()
        self.assertEqual(settings.thinking, "auto")
        self.assertIsNone(settings.seed)
        connection, = self.responses(Response(wire({"done": True})))
        list(OllamaClient().stream_chat(self.messages, model="qwen3:4b", settings=settings))
        payload = json.loads(connection.request.call_args.kwargs["body"])
        self.assertNotIn("think", payload)
        self.assertEqual(payload["options"], {"temperature": .2, "num_predict": 1024, "num_ctx": 4096})

    def test_invalid_thinking_and_sampling_settings_fail_locally(self):
        for thinking in (None, True, "ON", "false", "sometimes"):
            with self.subTest(thinking=thinking), self.assertRaises(ValueError):
                GenerationSettings(thinking=thinking)
        for changes in ({"seed": True}, {"seed": -1}, {"seed": 2**31}, {"seed": 1.5},
                        {"top_p": math.nan}, {"top_p": 1.1}, {"top_k": True}, {"top_k": -1},
                        {"presence_penalty": math.inf}, {"presence_penalty": -3}, {"repeat_penalty": -1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                GenerationSettings(**changes)

    def test_sampling_and_seed_are_options_but_think_is_top_level(self):
        client, info, connections = self.native_client(Response(wire({"message": {"content": "Answer"}, "done": True})))
        settings = GenerationSettings(thinking="off", seed=42, temperature=.2, num_predict=512,
                                      top_p=.95, top_k=40, presence_penalty=0, repeat_penalty=1)
        list(client.stream_chat(self.messages, model=info.name, settings=settings))
        payload = json.loads(connections[-1].request.call_args.kwargs["body"])
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["options"], {"temperature": .2, "num_predict": 512, "num_ctx": 4096,
                                               "seed": 42, "top_p": .95, "top_k": 40, "presence_penalty": 0, "repeat_penalty": 1})
        self.assertNotIn("thinking", payload["options"])

    def test_native_profiles_allow_on_off_and_keep_auto_model_default(self):
        for model in NATIVE:
            with self.subTest(model=model[0]):
                client, info, connections = self.native_client(model=model)
                self.assertEqual(info.thinking_control, "native_toggle")
                self.assertEqual(info.thinking_options, ("auto", "on", "off"))
                self.assertEqual(info.digest, model[2])
                self.assertIs(client.validate_thinking(info.name, "on"), True)
                self.assertIs(client.validate_thinking(info.name, "off"), False)
                self.assertIsNone(client.validate_thinking(info.name, "auto"))
                self.assertEqual(sum(connection.request.call_count for connection in connections), 3)

    def test_native_model_name_without_verified_manifest_cannot_enable_off(self):
        name, family, _digest = NATIVE[0]
        client, info, _connections = self.native_client(model=(name, family, "0" * 64))
        self.assertEqual(info.thinking_control, "hidden")
        with self.assertRaises(OllamaThinkingError):
            client.validate_thinking(name, "off")

    def test_native_digest_without_thinking_capability_does_not_enable_on(self):
        client, info, _connections = self.native_client(shown={"capabilities": ["completion"], "template": "{{ .Prompt }}"})
        self.assertEqual(info.thinking_control, "non_thinking")
        with self.assertRaises(OllamaThinkingError):
            client.validate_thinking(info.name, "on")

    def test_non_thinking_instruct_rejects_on_and_off_does_not_add_fake_control(self):
        name = "qwen3:4b-instruct-2507-q4_K_M"
        connections = self.responses(
            Response(encoded({"version": "0.40.16"})),
            Response(encoded({"models": [{"name": name, "digest": next(iter(VERIFIED_NON_THINKING_DIGESTS)), "capabilities": ["thinking", "tools"]}]})),
            Response(wire({"message": {"content": "A direct answer."}, "done": True})),
        )
        client = OllamaClient()
        info = client.check_model(name)
        self.assertEqual(info.thinking_options, ("auto", "off"))
        with self.assertRaises(OllamaThinkingError):
            list(client.stream_chat(self.messages, model=name, settings=GenerationSettings(thinking="on")))
        list(client.stream_chat(self.messages, model=name, settings=GenerationSettings(thinking="off")))
        self.assertNotIn("think", json.loads(connections[-1].request.call_args.kwargs["body"]))
        self.assertEqual(client.chat_request_count, 1)

    def test_explicit_mode_without_preflight_inspects_metadata_before_generation(self):
        name, family, digest = NATIVE[0]
        connections = self.responses(
            Response(encoded({"version": "0.40.16"})),
            Response(encoded({"models": [{"name": name, "digest": digest, "details": {"family": family}}]})),
            Response(encoded({"capabilities": ["thinking"], "template": "{{ .Prompt }}"})),
            Response(wire({"message": {"content": "OK"}, "done": True})),
        )
        events = list(OllamaClient().stream_chat(self.messages, model=name, settings=GenerationSettings(thinking="off")))
        self.assertEqual(events[-1].content, "OK")
        self.assertEqual([item.request.call_args.args[1] for item in connections], ["/api/version", "/api/tags", "/api/show", "/api/chat"])

    def test_explicit_modes_never_retry_unsupported_control(self):
        for mode in ("on", "off"):
            with self.subTest(mode=mode):
                client, info, connections = self.native_client(Response(encoded({"error": "think is not supported PRIVATE"}), status=400))
                with self.assertRaises(OllamaThinkingError) as caught:
                    list(client.stream_chat(self.messages, model=info.name, settings=GenerationSettings(thinking=mode)))
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertEqual(client.chat_request_count, 1)
                self.assertTrue(connections[-1].closed)

    def test_off_rejects_ignored_control_before_content_or_tools_are_exposed(self):
        item = {"message": {"content": "Answer", "thinking": "PRIVATE REASONING", "tool_calls": [CALL]}, "done": True}
        client, info, connections = self.native_client(Response(wire(item)))
        exposed = []
        with self.assertRaises(OllamaThinkingError) as caught:
            for event in client.stream_chat(self.messages, model=info.name, settings=GenerationSettings(thinking="off"), tools=TOOLS):
                exposed.append(event)
        self.assertEqual(exposed, [])
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertTrue(connections[-1].closed)

    def test_on_discards_reasoning_but_preserves_tool_call_and_metrics(self):
        items = ({"message": {"thinking": "PRIVATE REASONING"}}, {"message": {"tool_calls": [CALL]}, "done": True, "eval_count": 30})
        client, info, _connections = self.native_client(Response(wire(*items)))
        events = list(client.stream_chat(self.messages, model=info.name, settings=GenerationSettings(thinking="on"), tools=TOOLS))
        self.assertEqual(events[-1].tool_calls, (CALL,))
        self.assertEqual(events[-1].eval_count, 30)
        self.assertEqual(events[-1].thinking_chunks, 1)
        self.assertNotIn("PRIVATE", repr(events))

    def test_off_preserves_paired_native_tool_history(self):
        messages = [{"role": "user", "content": "Open Notepad."}, {"role": "assistant", "content": "", "tool_calls": [CALL]},
                    {"role": "tool", "tool_name": "open_application", "content": '{"status":"launch_attempt_succeeded"}'}]
        client, info, connections = self.native_client(Response(wire({"message": {"content": "The attempt succeeded."}, "done": True})))
        list(client.stream_chat(messages, model=info.name, settings=GenerationSettings(thinking="off"), tools=[]))
        payload = json.loads(connections[-1].request.call_args.kwargs["body"])
        self.assertEqual(payload["messages"], messages)
        self.assertEqual(payload["tools"], [])

    def test_nonstreaming_on_discards_reasoning_without_fabricated_phase_timings(self):
        item = {"message": {"content": "Answer", "thinking": "PRIVATE"}, "done": True, "eval_count": 42, "eval_duration": 1000}
        client, info, connections = self.native_client(Response(encoded(item)))
        event = client.chat(self.messages, model=info.name, settings=GenerationSettings(thinking="on"))
        payload = json.loads(connections[-1].request.call_args.kwargs["body"])
        self.assertIs(payload["stream"], False)
        self.assertIs(payload["think"], True)
        self.assertEqual(event.content, "Answer")
        self.assertEqual(event.eval_count, 42)
        self.assertIsNone(event.first_content_seconds)
        self.assertIsNone(event.first_thinking_seconds)
        self.assertIsNone(event.first_stream_seconds)
        self.assertNotIn("PRIVATE", repr(event))

    def test_nonstreaming_off_preserves_complete_tool_proposal(self):
        client, info, _connections = self.native_client(Response(encoded({"message": {"tool_calls": [CALL]}, "done": True})))
        event = client.chat(self.messages, model=info.name, settings=GenerationSettings(thinking="off"), tools=TOOLS)
        self.assertEqual(event.tool_calls, (CALL,))
        self.assertTrue(event.done)

    def test_nonstreaming_off_fails_closed_when_control_is_ignored(self):
        client, info, _connections = self.native_client(Response(encoded({"message": {"content": "Answer", "thinking": "PRIVATE"}, "done": True})))
        with self.assertRaises(OllamaThinkingError):
            client.chat(self.messages, model=info.name, settings=GenerationSettings(thinking="off"))

    def test_nonstreaming_channel_guard_rejects_raw_marker_but_keeps_legitimate_tags(self):
        for content in ("<think>PRIVATE</think>Answer", "</think>Answer", "<thi"):
            with self.subTest(content=content):
                client, info, _connections = self.native_client(Response(encoded({"message": {"content": content}, "done": True})))
                with self.assertRaises(OllamaProtocolError):
                    client.chat(self.messages, model=info.name, settings=GenerationSettings(thinking="off"))
        content = "The literal tag is <think>, inside this explanation."
        client, info, _connections = self.native_client(Response(encoded({"message": {"content": content}, "done": True})))
        self.assertEqual(client.chat(self.messages, model=info.name, settings=GenerationSettings(thinking="off")).content, content)

    def test_nonstreaming_incomplete_or_malformed_body_fails_closed(self):
        for item in ({"message": {"content": "partial"}, "done": False}, {"message": {"thinking": []}, "done": True},
                     {"message": {"tool_calls": [CALL]}, "done": True}):
            with self.subTest(item=item):
                client, info, _connections = self.native_client(Response(encoded(item)))
                with self.assertRaises(OllamaProtocolError):
                    client.chat(self.messages, model=info.name, settings=GenerationSettings(thinking="on"), tools=[])

    def test_profile_digest_constants_are_exact_and_distinct(self):
        self.assertTrue(all(len(value) == 64 and all(char in "0123456789abcdef" for char in value)
                            for value in VERIFIED_THINKING_CONTROL_DIGESTS))
        self.assertFalse(VERIFIED_NON_THINKING_DIGESTS & VERIFIED_THINKING_CONTROL_DIGESTS)

    def test_stream_token_limit_keeps_metrics_without_completion_or_proposals(self):
        body = wire(
            {"message": {"thinking": "PRIVATE REASONING"}},
            {"message": {"content": "Unfinished answer", "tool_calls": [CALL]},
             "done": True, "done_reason": "length", "eval_count": 512,
             "eval_duration": 90000000000, "load_duration": 1000000000,
             "prompt_eval_count": 60, "prompt_eval_duration": 2000000000,
             "total_duration": 93000000000},
        )
        client, info, connections = self.native_client(Response(body))
        exposed = []
        with self.assertRaises(OllamaGenerationLimitError) as caught:
            for event in client.stream_chat(self.messages, model=info.name, settings=GenerationSettings(thinking="on"), tools=TOOLS):
                exposed.append(event)
        diagnostic = caught.exception.completion
        self.assertEqual(caught.exception.done_reason, "length")
        self.assertEqual(diagnostic.eval_count, 512)
        self.assertEqual(diagnostic.eval_duration, 90000000000)
        self.assertEqual(diagnostic.load_duration, 1000000000)
        self.assertEqual(diagnostic.prompt_eval_count, 60)
        self.assertEqual(diagnostic.thinking_chunks, 1)
        self.assertEqual(diagnostic.thinking_characters, len("PRIVATE REASONING"))
        self.assertEqual(diagnostic.content, "")
        self.assertEqual(diagnostic.tool_calls, ())
        self.assertFalse(diagnostic.done)
        self.assertTrue(all(not event.done and not event.tool_calls for event in exposed))
        self.assertNotIn("PRIVATE", repr(diagnostic))
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertTrue(connections[-1].closed)

    def test_nonstream_token_limit_retains_counts_without_inventing_phase_times(self):
        item = {"message": {"thinking": "PRIVATE", "tool_calls": [CALL]},
                "done": True, "done_reason": "length", "eval_count": 1024,
                "eval_duration": 123000000000}
        client, info, _connections = self.native_client(Response(encoded(item)))
        with self.assertRaises(OllamaGenerationLimitError) as caught:
            client.chat(self.messages, model=info.name, settings=GenerationSettings(thinking="on"), tools=TOOLS)
        diagnostic = caught.exception.completion
        self.assertEqual(diagnostic.eval_count, 1024)
        self.assertEqual(diagnostic.eval_duration, 123000000000)
        self.assertIsNone(diagnostic.first_stream_seconds)
        self.assertIsNone(diagnostic.first_thinking_seconds)
        self.assertIsNone(diagnostic.first_content_seconds)
        self.assertEqual(diagnostic.tool_calls, ())
        self.assertFalse(diagnostic.done)
        self.assertNotIn("PRIVATE", repr(diagnostic))


if __name__ == "__main__":
    unittest.main()
