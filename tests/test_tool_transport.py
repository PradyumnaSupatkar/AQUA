"""Native Ollama tool framing and history, without network or execution."""

from copy import deepcopy
from http.client import HTTPConnection as WireHTTPConnection
import io
import json
import unittest
from unittest.mock import Mock, patch

from aqua.brain.ollama import (
    GenerationSettings, MAX_TOOL_ARGUMENT_BYTES, MAX_TOOL_CALLS,
    OllamaClient, OllamaError, OllamaProtocolError,
)


TOOLS = [{"type": "function", "function": {
    "name": "open_application", "description": "Request one app.",
    "parameters": {"type": "object", "properties": {"app_id": {"type": "string"}},
                   "required": ["app_id"], "additionalProperties": False},
}}]


def call(app="notepad", *, identifier="call-1", index=0, name="open_application"):
    return {"id": identifier, "function": {"index": index, "name": name, "arguments": {"app_id": app}}}


def wire(*items):
    return b"\n".join(json.dumps(item).encode("utf-8") for item in items) + b"\n"


class Response:
    def __init__(self, body):
        self.status = 200
        self.body = io.BytesIO(body)
        self.closed = False

    def readline(self, limit):
        return self.body.readline(limit)

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, response):
        self.response = response
        self.sock = Mock()
        self.request = Mock()
        self.closed = False

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class NativeToolTransportTests(unittest.TestCase):
    def setUp(self):
        self.client = OllamaClient()
        self.messages = [{"role": "system", "content": "Aqua"}, {"role": "user", "content": "Open Notepad."}]
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            manager = patch(target, side_effect=AssertionError("Tests must not contact a server or launch apps."))
            manager.start()
            self.addCleanup(manager.stop)

    def request(self, body, *, messages=None, tools=TOOLS):
        response = Response(body)
        connection = Connection(response)
        with patch("aqua.brain.ollama.HTTPConnection", return_value=connection):
            events = list(self.client.stream_chat(self.messages if messages is None else messages,
                                                 model="local-instruct", settings=GenerationSettings(), tools=tools))
        self.assertTrue(response.closed and connection.closed)
        return events, json.loads(connection.request.call_args.kwargs["body"])

    def test_native_definition_sent_without_structured_classifier_prompt(self):
        before = deepcopy((self.messages, TOOLS))
        events, payload = self.request(wire({"message": {"tool_calls": [call()]}, "done": True}))
        self.assertEqual(payload["tools"], TOOLS)
        self.assertEqual(payload["messages"], self.messages)
        self.assertNotIn("format", payload)
        self.assertEqual(payload["keep_alive"], "5m")
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertEqual((self.messages, TOOLS), before)
        self.assertEqual(events[-1].tool_calls, (call(),))

    def test_proposals_are_exposed_only_after_complete_stream(self):
        body = wire({"message": {"content": "A reply"}, "done": False},
                    {"message": {"tool_calls": [call()]}, "done": False},
                    {"message": {}, "done": True})
        events, _ = self.request(body)
        self.assertEqual(events[0].content, "A reply")
        self.assertEqual(events[0].tool_calls, ())
        self.assertEqual(events[-1].tool_calls, (call(),))
        self.assertTrue(events[-1].done)
        self.assertIsNotNone(events[-1].first_stream_seconds)

    def test_normal_chat_remains_streamed_in_one_request(self):
        events, _ = self.request(wire({"message": {"content": "Hello"}},
                                     {"message": {"content": " there"}}, {"done": True}))
        self.assertEqual([event.content for event in events], ["Hello", " there", ""])
        self.assertTrue(all(not event.tool_calls for event in events))

    def test_identical_repeated_id_and_index_are_deduplicated(self):
        events, _ = self.request(wire({"message": {"tool_calls": [call()]}},
                                     {"message": {"tool_calls": [call()]}, "done": True}))
        self.assertEqual(events[-1].tool_calls, (call(),))

    def test_different_complete_calls_are_returned_as_whole_batch(self):
        second = call("chrome", identifier="call-2", index=1)
        events, _ = self.request(wire({"message": {"tool_calls": [call()]}},
                                     {"message": {"tool_calls": [second]}, "done": True}))
        self.assertEqual(events[-1].tool_calls, (call(), second))

    def test_changed_duplicate_or_partial_framing_is_never_completed(self):
        invalid = (
            call("chrome"),
            {"id": "call-1", "function": {"name": "open_application"}},
            {"id": "call-1", "function": {"name": "", "arguments": {}}},
        )
        for proposal in invalid:
            with self.subTest(proposal=proposal):
                with self.assertRaises(OllamaProtocolError):
                    self.request(wire({"message": {"tool_calls": [call()]}},
                                      {"message": {"tool_calls": [proposal]}, "done": True}))

    def test_unknown_names_and_argument_schema_are_untrusted_executor_inputs(self):
        proposal = call(name="unknown_tool")
        proposal["function"]["arguments"] = {"path": "PRIVATE_EXECUTABLE", "extra": True}
        events, _ = self.request(wire({"message": {"tool_calls": [proposal]}, "done": True}))
        self.assertEqual(events[-1].tool_calls, (proposal,))
        # Parsing is not execution: semantic name/argument rejection belongs
        # to the registry. No subprocess or application module is invoked.

    def test_string_arguments_remain_strings_not_implicitly_decoded(self):
        proposal = call()
        proposal["function"]["arguments"] = '{"app_id":"notepad"}'
        events, _ = self.request(wire({"message": {"tool_calls": [proposal]}, "done": True}))
        self.assertIsInstance(events[-1].tool_calls[0]["function"]["arguments"], str)

    def test_call_and_result_fields_survive_history_copy(self):
        messages = [*self.messages, {"role": "assistant", "content": "", "tool_calls": [call()]},
                    {"role": "tool", "tool_name": "open_application", "tool_call_id": "call-1", "content": '{"status":"launch_attempt_succeeded"}'}]
        before = deepcopy(messages)
        events, payload = self.request(wire({"message": {"content": "Launch attempted."}, "done": True}),
                                       messages=messages, tools=[])
        self.assertEqual(payload["messages"], messages)
        self.assertEqual(payload["tools"], [])
        self.assertEqual(messages, before)
        self.assertEqual(events[-1].content, "Launch attempted.")

    def test_named_result_without_id_matches_single_call(self):
        messages = [*self.messages, {"role": "assistant", "tool_calls": [call()]},
                    {"role": "tool", "tool_name": "open_application", "content": "failed"}]
        _, payload = self.request(wire({"message": {"content": "It failed."}, "done": True}), messages=messages, tools=[])
        self.assertEqual(payload["messages"][-1], messages[-1])
        self.assertEqual(payload["messages"][-2]["content"], "")

    def test_valid_tool_pairs_can_precede_current_user_and_followup(self):
        messages = [*self.messages, {"role": "assistant", "content": "", "tool_calls": [call()]},
                    {"role": "tool", "tool_name": "open_application", "content": "success"},
                    {"role": "assistant", "content": "Launch attempted."},
                    {"role": "user", "content": "Which app?"}]
        _, payload = self.request(wire({"done": True}), messages=messages)
        self.assertEqual(payload["messages"], messages)

    def test_orphan_missing_duplicate_and_mismatched_results_fail_before_http(self):
        assistant = {"role": "assistant", "content": "", "tool_calls": [call()]}
        result = {"role": "tool", "tool_name": "open_application", "tool_call_id": "call-1", "content": "success"}
        cases = (
            [*self.messages, result],
            [*self.messages, assistant, {"role": "user", "content": "Next"}],
            [*self.messages, assistant, result, result],
            [*self.messages, assistant, {**result, "tool_call_id": "different"}],
            [*self.messages, assistant, {**result, "tool_name": "different"}],
            [*self.messages, {"role": "tool", "content": "success"}],
        )
        with patch("aqua.brain.ollama.HTTPConnection") as factory:
            for messages in cases:
                with self.subTest(messages=messages), self.assertRaises(ValueError):
                    list(self.client.stream_chat(messages, model="local", settings=GenerationSettings(), tools=TOOLS))
            factory.assert_not_called()

    def test_disabled_tools_reject_any_new_tool_proposal(self):
        for tools in (None, []):
            with self.subTest(tools=tools), self.assertRaisesRegex(OllamaProtocolError, "disabled"):
                self.request(wire({"message": {"tool_calls": [call()]}, "done": True}), tools=tools)

    def test_truncation_and_token_limit_never_emit_a_tool_completion(self):
        for body in (wire({"message": {"tool_calls": [call()]}}),
                     wire({"message": {"tool_calls": [call()]}, "done": True, "done_reason": "length"})):
            response = Response(body)
            connection = Connection(response)
            with self.subTest(body=body), patch("aqua.brain.ollama.HTTPConnection", return_value=connection):
                events = self.client.stream_chat(self.messages, model="local", settings=GenerationSettings(), tools=TOOLS)
                exposed = []
                with self.assertRaises(OllamaError):
                    for event in events:
                        exposed.extend(event.tool_calls)
                self.assertEqual(exposed, [])
                self.assertTrue(connection.closed and response.closed)

    def test_size_call_count_and_framing_are_bounded(self):
        bad_calls = (
            [call()] * (MAX_TOOL_CALLS + 1), None, {},
            [{"function": {"name": "open_application", "arguments": "x" * (MAX_TOOL_ARGUMENT_BYTES + 1)}}],
            [{**call(), "path": "PRIVATE"}],
            [{**call(), "id": "bad\nid"}],
            [{"function": {"index": True, "name": "open_application", "arguments": {}}}],
        )
        for proposals in bad_calls:
            with self.subTest(proposals=proposals), self.assertRaises(OllamaProtocolError):
                self.request(wire({"message": {"tool_calls": proposals}, "done": True}))

    def test_duplicate_and_nonfinite_argument_json_is_rejected(self):
        for arguments in (b'{"app_id":"chrome","app_id":"notepad"}', b'{"app_id":NaN}'):
            body = b'{"message":{"tool_calls":[{"function":{"name":"open_application","arguments":' + arguments + b'}}]},"done":true}\n'
            with self.subTest(arguments=arguments), self.assertRaises(OllamaProtocolError):
                self.request(body)

    def test_invalid_tool_definition_values_fail_before_http(self):
        with patch("aqua.brain.ollama.HTTPConnection") as factory:
            for tools in ("not a list", [None], [{}] * (MAX_TOOL_CALLS + 1), [{"bad": float("nan")}]):
                with self.subTest(tools=tools), self.assertRaises(ValueError):
                    list(self.client.stream_chat(self.messages, model="local", settings=GenerationSettings(), tools=tools))
            factory.assert_not_called()


class ClosingSocket:
    """A real HTTPResponse can close this in-memory transport without I/O."""
    def __init__(self, data):
        self.data, self.references, self.close_requested = data, 0, False
        self.closed = False

    def setsockopt(self, *_args):
        pass

    def sendall(self, _data):
        pass

    def settimeout(self, _timeout):
        if self.closed:
            raise OSError(10038, "Closed socket")

    def makefile(self, _mode):
        self.references += 1
        owner = self
        class SocketFile(io.BytesIO):
            def close(self):
                if not self.closed:
                    owner.references -= 1
                    if owner.close_requested and not owner.references:
                        owner.closed = True
                super().close()
        return SocketFile(self.data)

    def close(self):
        self.close_requested = True
        if not self.references:
            self.closed = True


class NativeToolFramingTests(unittest.TestCase):
    def test_real_http_parser_completes_tools_after_connection_close(self):
        body = wire({"message": {"tool_calls": [call()]}}, {"done": True})
        socket = ClosingSocket(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        connection = WireHTTPConnection("127.0.0.1", 11434)
        connection._create_connection = Mock(return_value=socket)
        with patch("socket.create_connection", side_effect=AssertionError("No real network")), patch("aqua.brain.ollama.HTTPConnection", return_value=connection):
            events = list(OllamaClient().stream_chat([{"role": "user", "content": "Open Notepad."}],
                                                   model="local", settings=GenerationSettings(), tools=TOOLS))
        self.assertEqual(events[-1].tool_calls, (call(),))
        self.assertTrue(socket.closed)


if __name__ == "__main__":
    unittest.main()
