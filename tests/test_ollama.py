"""Local Ollama protocol tests use in-memory HTTP doubles, never a server."""

from copy import deepcopy
import io
import json
import math
import unittest
from unittest.mock import Mock, patch

from aqua.brain.ollama import (
    ChatEvent,
    GenerationSettings,
    MAX_LINE_BYTES,
    MAX_RESPONSE_CHARACTERS,
    OllamaClient,
    OllamaConnectionError,
    OllamaError,
    OllamaModelError,
    OllamaProtocolError,
    OllamaTimeoutError,
)


def encoded(value):
    return json.dumps(value).encode("utf-8")


def stream(*items):
    return b"\n".join(encoded(item) for item in items) + b"\n"


class Response:
    def __init__(self, body=b"", status=200, failure=None):
        self.status = status
        self.buffer = io.BytesIO(body)
        self.failure = failure
        self.closed = False

    def read(self, count):
        if self.failure:
            raise self.failure
        return self.buffer.read(count)

    def readline(self, count):
        if self.failure:
            raise self.failure
        return self.buffer.readline(count)

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, response):
        self.response = response
        self.request = Mock()
        self.sock = Mock()
        self.closed = False

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class OllamaTests(unittest.TestCase):
    def setUp(self):
        self.client = OllamaClient()
        self.messages = [{"role": "system", "content": "Be concise."}, {"role": "user", "content": "Hello."}]
        self.settings = GenerationSettings()

    def connections(self, *responses):
        connections = [Connection(response) for response in responses]
        factory = patch("aqua.brain.ollama.HTTPConnection", side_effect=connections)
        factory.start()
        self.addCleanup(factory.stop)
        return connections

    def chat(self, *, model="qwen3:4b"):
        return self.client.stream_chat(self.messages, model=model, settings=self.settings)

    def test_model_check_reads_only_loopback_version_tags_and_template(self):
        responses = [Response(encoded({"version": "0.40.16"})), Response(encoded({"models": [
            {"name": "qwen3:4b", "details": {"family": "qwen3"}},
        ]})), Response(encoded({"template": "<|im_start|>assistant\n<think>\n"}))]
        connections = self.connections(*responses)
        info = self.client.check_model("qwen3:4b")
        self.assertEqual((info.name, info.version, info.family), ("qwen3:4b", "0.40.16", "qwen3"))
        self.assertEqual(info.thinking_control, "hidden")
        self.assertEqual(connections[0].request.call_args.args[:2], ("GET", "/api/version"))
        self.assertEqual(connections[1].request.call_args.args[:2], ("GET", "/api/tags"))
        self.assertEqual(connections[2].request.call_args.args[:2], ("POST", "/api/show"))
        self.assertEqual(json.loads(connections[2].request.call_args.kwargs["body"]), {"model": "qwen3:4b"})
        self.assertTrue(all(connection.closed for connection in connections))
        self.assertTrue(all(response.closed for response in responses))

    def test_http_connection_is_fixed_loopback_and_ignores_proxy_environment(self):
        response = Response(stream({"done": True}))
        connection = Connection(response)
        with patch.dict("os.environ", {"HTTP_PROXY": "http://remote.invalid:9999"}), patch(
            "aqua.brain.ollama.HTTPConnection", return_value=connection
        ) as factory:
            list(self.chat())
        factory.assert_called_once_with("127.0.0.1", 11434, timeout=120.0)
        self.assertEqual(connection.request.call_args.args[:2], ("POST", "/api/chat"))

    def test_uninstalled_model_errors_without_any_pull_or_inference_request(self):
        connections = self.connections(Response(encoded({"version": "v"})), Response(encoded({"models": []})))
        with self.assertRaisesRegex(OllamaModelError, "never downloads"):
            self.client.check_model("qwen3:4b")
        self.assertEqual(sum(connection.request.call_count for connection in connections), 2)

    def test_latest_alias_is_resolved_without_loading_a_model(self):
        self.connections(Response(encoded({"version": "v"})), Response(encoded({"models": [{"name": "example:latest"}]})))
        self.assertEqual(self.client.check_model("example").name, "example:latest")

    def test_malformed_model_metadata_is_graceful(self):
        for tags in ({"models": None}, {"models": [None]}, {"models": [{"name": []}]}):
            with self.subTest(tags=tags):
                self.connections(Response(encoded({"version": "v"})), Response(encoded(tags)))
                with self.assertRaises(OllamaProtocolError):
                    self.client.check_model("qwen3:4b")

    def test_stream_yields_content_and_final_stats_but_never_thinking_fields(self):
        response = Response(stream(
            {"message": {"thinking": "PRIVATE REASONING", "content": ""}, "done": False},
            {"message": {"content": "Hello"}, "done": False},
            {"message": {"content": " there."}, "done": False},
            {"message": {"content": ""}, "done": True, "eval_count": 4, "eval_duration": 20},
        ))
        connection, = self.connections(response)
        events = list(self.chat())
        self.assertEqual("".join(event.content for event in events), "Hello there.")
        self.assertTrue(events[-1].done)
        self.assertEqual(events[-1].eval_count, 4)
        self.assertEqual(events[-1].eval_duration, 20)
        self.assertNotIn("PRIVATE", repr(events))
        self.assertTrue(connection.closed and response.closed)

    def test_legacy_unknown_qwen_keeps_reasoning_parser_and_does_not_inject_soft_switches(self):
        connection, = self.connections(Response(stream({"done": True})))
        before = deepcopy(self.messages)
        list(self.chat())
        payload = json.loads(connection.request.call_args.kwargs["body"])
        self.assertEqual(self.messages, before)
        self.assertEqual(payload["messages"], self.messages)
        self.assertNotIn("think", payload)
        self.assertIs(payload["stream"], True)
        self.assertEqual(payload["options"], {"temperature": 0.2, "num_predict": 1024, "num_ctx": 4096})
        self.assertEqual(payload["keep_alive"], "5m")
        self.assertNotIn("tools", payload)

    def test_think_aware_template_requests_disabled_reasoning_without_soft_directives(self):
        connections = self.connections(
            Response(encoded({"version": "future"})),
            Response(encoded({"models": [{"name": "qwen3:4b", "details": {"family": "qwen3"}}]})),
            Response(encoded({"template": "<|im_start|>assistant\n{{- if and $.IsThinkSet (not $.Think) }}<think>\n\n</think>\n{{- end }}{{- end }}"})),
            Response(stream({"message": {"content": "OK"}, "done": True})),
        )
        self.assertEqual(self.client.check_model("qwen3:4b").thinking_control, "disable_requested")
        self.assertEqual(list(self.chat())[0].content, "OK")
        payload = json.loads(connections[-1].request.call_args.kwargs["body"])
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["messages"], self.messages)

    def test_template_mentions_do_not_enable_unsafe_thinking_flag(self):
        templates = [
            # A comment cannot establish support for disabling thinking.
            "{{/* .Think is not supported */}}<|im_start|>assistant\n<think>\n",
            "<|im_start|>assistant\n{{- /* .Think */ -}}<think>\n{{end}}",
            # Historical-message rendering is outside the generation suffix.
            "{{ if .Think }}history{{end}}<|im_start|>assistant\n<think>\n",
            # Even a Think-controlled block cannot make a later hardcoded
            # generation opener safe.
            "<|im_start|>assistant\n{{if .Think}}<think>{{else}}{{end}}<think>\n",
            # An unknown expression or mere property rendering is not proof.
            "<|im_start|>assistant\n{{ .Think }}<think>\n",
            "<|im_start|>assistant\n{{if unknown .Think}}<think>{{end}}",
            # An unmatched closer is not an empty, closed thinking prefix.
            "<|im_start|>assistant\n{{if .Think}}<think>{{else}}</think>{{end}}",
            # No generation-header context: don't infer it from a snippet.
            "{{if .Think}}<think>{{else}}<think></think>{{end}}",
        ]
        for template in templates:
            with self.subTest(template=template):
                client = OllamaClient()
                connections = self.connections(
                    Response(encoded({"version": "v"})),
                    Response(encoded({"models": [{"name": "qwen3:4b", "details": {"family": "qwen3"}}]})),
                    Response(encoded({"template": template})),
                    Response(stream({"message": {"content": "OK"}, "done": True})),
                )
                self.assertEqual(client.check_model("qwen3:4b").thinking_control, "hidden")
                list(client.stream_chat(self.messages, model="qwen3:4b", settings=self.settings))
                self.assertNotIn("think", json.loads(connections[-1].request.call_args.kwargs["body"]))

    def test_known_generation_prefix_controls_can_disable_thinking(self):
        templates = [
            "<|im_start|>assistant\n{{if .Think}}<think>{{end}}",
            "<|im_start|>assistant\n{{if .Think}}<think>{{else}}<think>\n\n</think>{{end}}",
            "<|im_start|>assistant\n{{if .IsThinkSet}}{{if not .Think}}<think></think>{{end}}{{end}}",
        ]
        for template in templates:
            with self.subTest(template=template):
                client = OllamaClient()
                connections = self.connections(
                    Response(encoded({"version": "v"})),
                    Response(encoded({"models": [{"name": "qwen3:4b", "details": {"family": "qwen3"}}]})),
                    Response(encoded({"template": template})),
                    Response(stream({"message": {"content": "OK"}, "done": True})),
                )
                self.assertEqual(client.check_model("qwen3:4b").thinking_control, "disable_requested")
                list(client.stream_chat(self.messages, model="qwen3:4b", settings=self.settings))
                self.assertIs(json.loads(connections[-1].request.call_args.kwargs["body"])["think"], False)

    def test_unknown_qwen_template_fails_safe_to_hidden_mode(self):
        connections = self.connections(
            Response(encoded({"version": "unknown"})),
            Response(encoded({"models": [{"name": "custom:latest", "details": {"family": "qwen3"}}]})),
            Response(encoded({"template": None})),
            Response(stream({"message": {"thinking": "private"}, "done": False}, {"message": {"content": "OK"}, "done": True})),
        )
        self.assertEqual(self.client.check_model("custom").thinking_control, "hidden")
        self.assertEqual("".join(event.content for event in self.chat(model="custom")), "OK")
        self.assertNotIn("think", json.loads(connections[-1].request.call_args.kwargs["body"]))

    def test_explicit_non_thinking_instruct_capabilities_override_qwen_family(self):
        name = "qwen3:4b-instruct-2507-q4_K_M"
        connections = self.connections(
            Response(encoded({"version": "0.40.16"})),
            Response(encoded({"models": [{"name": name, "details": {"family": "qwen3"},
                                            "capabilities": ["completion", "tools"]}]})),
            Response(stream({"message": {"content": "A direct answer."}, "done": True})),
        )
        self.assertEqual(self.client.check_model(name).thinking_control, "non_thinking")
        events = list(self.chat(model=name))
        self.assertEqual(events[0].content, "A direct answer.")
        self.assertEqual([connection.request.call_args.args[:2] for connection in connections], [
            ("GET", "/api/version"), ("GET", "/api/tags"), ("POST", "/api/chat"),
        ])
        payload = json.loads(connections[-1].request.call_args.kwargs["body"])
        self.assertNotIn("think", payload)
        self.assertEqual(payload["messages"], self.messages)

    def test_show_can_declare_non_thinking_when_tags_omit_capabilities(self):
        name = "qwen3:4b-instruct-2507-q4_K_M"
        connections = self.connections(
            Response(encoded({"version": "v"})),
            Response(encoded({"models": [{"name": name, "details": {"family": "qwen3"}}]})),
            Response(encoded({"capabilities": ["completion", "tools"],
                              "template": "<|im_start|>assistant\n"})),
            Response(stream({"message": {"content": "OK"}, "done": True})),
        )
        self.assertEqual(self.client.check_model(name).thinking_control, "non_thinking")
        list(self.chat(model=name))
        self.assertNotIn("think", json.loads(connections[-1].request.call_args.kwargs["body"]))

    def test_verified_manifest_identity_overrides_misreported_thinking_for_aliases(self):
        names = [("qwen3:4b-instruct-2507-q4_K_M", "qwen3:4b-instruct-2507-q4_K_M"),
                 ("my-instruct-copy", "my-instruct-copy:latest")]
        for requested, installed in names:
            with self.subTest(requested=requested):
                client = OllamaClient()
                connections = self.connections(
                    Response(encoded({"version": "0.40.16"})),
                    Response(encoded({"models": [{
                        "name": installed, "details": {"family": "qwen3"},
                        "capabilities": ["tools", "thinking", "completion"],
                        "digest": "0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0",
                    }]})),
                    Response(stream({"message": {"content": "Direct."}, "done": True})),
                )
                self.assertEqual(client.check_model(requested).thinking_control, "non_thinking")
                events = list(client.stream_chat(self.messages, model=requested, settings=self.settings))
                self.assertEqual(events[0].content, "Direct.")
                payload = json.loads(connections[-1].request.call_args.kwargs["body"])
                self.assertNotIn("think", payload)
                self.assertEqual(payload["messages"], self.messages)
                self.assertEqual([connection.request.call_args.args[:2] for connection in connections], [
                    ("GET", "/api/version"), ("GET", "/api/tags"), ("POST", "/api/chat"),
                ])

    def test_similarly_named_model_with_unknown_manifest_remains_conservative(self):
        name = "qwen3:4b-instruct-2507-q4_K_M"
        connections = self.connections(
            Response(encoded({"version": "0.40.16"})),
            Response(encoded({"models": [{
                "name": name, "details": {"family": "qwen3"},
                "capabilities": ["tools", "thinking", "completion"], "digest": "0" * 64,
            }]})),
            Response(encoded({"template": "<|im_start|>assistant\n"})),
            Response(stream({"message": {"thinking": "PRIVATE"}},
                            {"message": {"content": "OK"}, "done": True})),
        )
        self.assertEqual(self.client.check_model(name).thinking_control, "hidden")
        events = list(self.chat(model=name))
        self.assertNotIn("PRIVATE", repr(events))
        self.assertNotIn("think", json.loads(connections[-1].request.call_args.kwargs["body"]))

    def test_verified_manifest_does_not_bypass_invalid_capability_validation(self):
        self.connections(
            Response(encoded({"version": "0.40.16"})),
            Response(encoded({"models": [{
                "name": "qwen3:4b-instruct-2507-q4_K_M", "capabilities": ["thinking", None],
                "digest": "0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0",
            }]})),
        )
        with self.assertRaisesRegex(OllamaProtocolError, "invalid model capabilities"):
            self.client.check_model("qwen3:4b-instruct-2507-q4_K_M")

    def test_instruct_tag_alone_does_not_prove_non_thinking(self):
        name = "qwen3:4b-instruct-2507-q4_K_M"
        connections = self.connections(
            Response(encoded({"version": "v"})),
            Response(encoded({"models": [{"name": name, "details": {"family": "qwen3"},
                                            "capabilities": []}]})),
            Response(encoded({"template": "<|im_start|>assistant\n<think>\n"})),
            Response(stream({"message": {"thinking": "PRIVATE"}},
                            {"message": {"content": "OK"}, "done": True})),
        )
        self.assertEqual(self.client.check_model(name).thinking_control, "hidden")
        events = list(self.chat(model=name))
        self.assertNotIn("PRIVATE", repr(events))
        self.assertNotIn("think", json.loads(connections[-1].request.call_args.kwargs["body"]))

    def test_invalid_capability_members_cannot_downgrade_to_non_thinking(self):
        for capabilities in (["completion", None], ["completion", 5], ["completion", ""],
                             ["completion", "thinking "], "completion"):
            with self.subTest(capabilities=capabilities):
                client = OllamaClient()
                self.connections(
                    Response(encoded({"version": "v"})),
                    Response(encoded({"models": [{"name": "qwen3:4b", "capabilities": capabilities}]})),
                )
                with self.assertRaisesRegex(OllamaProtocolError, "invalid model capabilities"):
                    client.check_model("qwen3:4b")

    def test_invalid_show_capabilities_fail_instead_of_enabling_a_flag(self):
        self.connections(
            Response(encoded({"version": "v"})),
            Response(encoded({"models": [{"name": "qwen3:4b"}]})),
            Response(encoded({"capabilities": ["completion", False],
                              "template": "<|im_start|>assistant\n{{if .Think}}<think>{{end}}"})),
        )
        with self.assertRaisesRegex(OllamaProtocolError, "invalid model capabilities"):
            self.client.check_model("qwen3:4b")

    def test_non_qwen_models_do_not_receive_qwen_template_directive(self):
        connection, = self.connections(Response(stream({"done": True})))
        list(self.chat(model="other:4b"))
        payload = json.loads(connection.request.call_args.kwargs["body"])
        self.assertEqual(payload["messages"], self.messages)

    def test_specific_unsupported_think_error_retries_once_without_field(self):
        first, second = self.connections(
            Response(encoded({"error": "think is not supported"}), status=400),
            Response(stream({"message": {"content": "OK"}, "done": True})),
        )
        self.assertEqual(list(self.chat(model="other:4b"))[0].content, "OK")
        self.assertFalse(json.loads(first.request.call_args.kwargs["body"])["think"])
        retry = json.loads(second.request.call_args.kwargs["body"])
        self.assertNotIn("think", retry)
        self.assertEqual(retry["messages"], self.messages)
        self.assertTrue(first.closed and second.closed)

    def test_general_http_errors_do_not_retry_or_echo_private_body(self):
        for status in (302, 400, 500):
            with self.subTest(status=status):
                connection, = self.connections(Response(encoded({"error": "PRIVATE USER CONTENT"}), status=status))
                with self.assertRaises(OllamaError) as caught:
                    list(self.chat())
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertTrue(connection.closed)

    def test_model_disappearing_between_preflight_and_generation_has_clear_error(self):
        self.connections(Response(encoded({"error": "not found"}), status=404))
        with self.assertRaises(OllamaModelError):
            list(self.chat())

    def test_truncated_stream_raises_instead_of_claiming_completion(self):
        response = Response(stream({"message": {"content": "partial"}, "done": False}))
        connection, = self.connections(response)
        events = self.chat()
        self.assertEqual(next(events).content, "partial")
        with self.assertRaisesRegex(OllamaProtocolError, "disconnected"):
            next(events)
        self.assertTrue(connection.closed and response.closed)

    def test_malformed_streams_fail_without_echoing_raw_text(self):
        for body in (b"not-json\n", b"\xff\n", b"[]\n", stream({"message": []}), stream({"message": {"content": 5}}), stream({"done": "yes"}), b'{"x":' + b'[' * 1100 + b'0' + b']' * 1100 + b'}\n', b'{"x":' + b'1' * 5000 + b'}\n'):
            with self.subTest(body=body):
                connection, = self.connections(Response(body))
                with self.assertRaises(OllamaProtocolError):
                    list(self.chat())
                self.assertTrue(connection.closed)

    def test_midstream_error_is_an_error_not_a_successful_final_chunk(self):
        self.connections(Response(stream({"error": "PRIVATE REASONING FROM SERVER"})))
        with self.assertRaises(OllamaError) as caught:
            list(self.chat())
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_token_limit_is_incomplete_and_never_emits_successful_completion(self):
        connection, = self.connections(Response(stream(
            {"message": {"content": "partial"}, "done": False},
            {"message": {"content": " reply"}, "done": True, "done_reason": "length"},
        )))
        events = self.chat()
        first, last = next(events), next(events)
        self.assertFalse(first.done or last.done)
        self.assertEqual(first.content + last.content, "partial reply")
        with self.assertRaisesRegex(OllamaError, "Increase --max-tokens"):
            next(events)
        self.assertTrue(connection.closed)

    def test_oversized_chunks_and_cumulative_output_are_bounded(self):
        connection, = self.connections(Response(b"x" * (MAX_LINE_BYTES + 1)))
        with self.assertRaisesRegex(OllamaProtocolError, "chunk exceeded"):
            list(self.chat())
        self.assertTrue(connection.closed)
        self.connections(Response(stream({"message": {"content": "x" * MAX_RESPONSE_CHARACTERS}, "done": False}, {"message": {"content": "y"}, "done": True})))
        with self.assertRaisesRegex(OllamaProtocolError, "response exceeded"):
            list(self.chat())

    def test_connection_failures_are_helpful_and_connections_close(self):
        connection, = self.connections(Response())
        connection.request.side_effect = ConnectionRefusedError("test")
        with self.assertRaisesRegex(OllamaConnectionError, "local Ollama"):
            list(self.chat())
        self.assertTrue(connection.closed)

    def test_timeout_is_reported_and_closes_stream(self):
        response = Response(failure=TimeoutError())
        connection, = self.connections(response)
        with self.assertRaises(OllamaTimeoutError):
            list(self.chat())
        self.assertTrue(connection.closed and response.closed)

    def test_total_stream_deadline_also_limits_a_drip_feed(self):
        response = Response(stream({"message": {"content": "late"}, "done": True}))
        connection, = self.connections(response)
        with patch("aqua.brain.ollama.perf_counter", side_effect=[0.0, 0.1, 121.0]):
            with self.assertRaises(OllamaTimeoutError):
                list(self.chat())
        self.assertTrue(connection.closed)

    def test_remaining_timeout_is_set_even_when_response_detaches_socket(self):
        response = Response(stream({"message": {"content": "OK"}, "done": True}))
        connection, = self.connections(response)
        stream_socket = connection.sock

        def detach():
            connection.sock = None
            return response

        connection.getresponse = detach
        with patch("aqua.brain.ollama.perf_counter", side_effect=[10.0, 15.0, 16.0]):
            self.assertEqual(list(self.chat())[0].content, "OK")
        stream_socket.settimeout.assert_called_once_with(115.0)
        self.assertTrue(connection.closed and response.closed)

    def test_closing_partial_generator_releases_connection(self):
        response = Response(stream({"message": {"content": "partial"}, "done": False}, {"done": True}))
        connection, = self.connections(response)
        events = self.chat()
        self.assertEqual(next(events).content, "partial")
        events.close()
        self.assertTrue(connection.closed and response.closed)

    def test_keyboard_interrupt_propagates_and_releases_connection(self):
        response = Response(failure=KeyboardInterrupt())
        connection, = self.connections(response)
        with self.assertRaises(KeyboardInterrupt):
            list(self.chat())
        self.assertTrue(connection.closed and response.closed)

    def test_generation_settings_and_timeout_reject_invalid_values(self):
        for kwargs in ({"temperature": math.nan}, {"temperature": -1}, {"temperature": True}, {"num_predict": 0}, {"num_predict": 4097}, {"num_ctx": 0}, {"num_ctx": 40000}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                GenerationSettings(**kwargs)
        for timeout in (0, -1, math.inf, math.nan, True, 601):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                OllamaClient(timeout=timeout)

    def test_invalid_messages_or_model_do_not_open_any_connection(self):
        with patch("aqua.brain.ollama.HTTPConnection") as factory:
            for messages in ([], [{"role": "tool", "content": "hello"}], [{"role": "assistant", "content": "hello"}], [{"role": "user", "content": None}]):
                with self.subTest(messages=messages), self.assertRaises(ValueError):
                    list(self.client.stream_chat(messages, model="qwen3:4b", settings=self.settings))
            for model in ("", "bad model", "\n"):
                with self.subTest(model=model), self.assertRaises(ValueError):
                    list(self.chat(model=model))
        factory.assert_not_called()

    def test_metadata_response_size_is_bounded(self):
        connection, = self.connections(Response(b"x" * (MAX_LINE_BYTES + 1)))
        with self.assertRaises(OllamaProtocolError):
            self.client.check_model("qwen3:4b")
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
