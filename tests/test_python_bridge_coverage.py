import json
import os
import socket
import unittest
from unittest import mock

from sketchup_mcp.bridge import (
    BridgeClient,
    BridgeProtocolError,
    BridgeRemoteError,
    BridgeTimeout,
    BridgeUnavailable,
    InMemoryBridgeAdapter,
    TCPBridgeAdapter,
)


class SequenceAdapter:
    """Deterministic adapter for observable retry outcomes."""

    endpoint = "127.0.0.1:4321"

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def exchange(self, request_payload, timeout):
        self.requests.append((json.loads(request_payload), timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def response_bytes(*, result=None, error=None, request_id="request-1"):
    response = {"jsonrpc": "2.0", "id": request_id}
    if result is not None:
        response["result"] = result
    if error is not None:
        response["error"] = error
    return json.dumps(response).encode("utf-8")


class DeterministicBridgeTest(unittest.TestCase):
    def test_tcp_adapter_frames_one_loopback_exchange_without_a_real_socket(self):
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.side_effect = [b'{"ok":', b'true}\n']
        with mock.patch(
            "sketchup_mcp.bridge.socket.create_connection",
            return_value=connection,
        ) as create_connection:
            adapter = TCPBridgeAdapter(port=1234)
            result = adapter.exchange(b"request", timeout=0.25)

        self.assertEqual(b'{"ok":true}', result)
        self.assertEqual("127.0.0.1:1234", adapter.endpoint)
        create_connection.assert_called_once_with(("127.0.0.1", 1234), 0.25)
        connection.settimeout.assert_called_once_with(0.25)
        connection.sendall.assert_called_once_with(b"request\n")

    def test_consecutive_tcp_commands_open_and_close_distinct_connections(self):
        first = mock.MagicMock(name="first_connection")
        first.__enter__.return_value = first
        first.__exit__.side_effect = lambda *_arguments: first.close()
        first.recv.return_value = response_bytes(
            result={"exchange": 1}, request_id="first"
        ) + b"\n"
        second = mock.MagicMock(name="second_connection")
        second.__enter__.return_value = second
        second.__exit__.side_effect = lambda *_arguments: second.close()
        second.recv.return_value = response_bytes(
            result={"exchange": 2}, request_id="second"
        ) + b"\n"

        with mock.patch(
            "sketchup_mcp.bridge.socket.create_connection",
            side_effect=(first, second),
        ) as create_connection:
            client = BridgeClient.for_tcp(port=1234, timeout=0.25)
            first_result = client.send_command(
                "get_selection", request_id="first"
            )
            second_result = client.send_command(
                "get_selection", request_id="second"
            )

        self.assertEqual({"exchange": 1}, first_result)
        self.assertEqual({"exchange": 2}, second_result)
        self.assertEqual(
            [
                mock.call(("127.0.0.1", 1234), 0.25),
                mock.call(("127.0.0.1", 1234), 0.25),
            ],
            create_connection.call_args_list,
        )
        first.__enter__.assert_called_once_with()
        second.__enter__.assert_called_once_with()
        first.__exit__.assert_called_once_with(None, None, None)
        second.__exit__.assert_called_once_with(None, None, None)
        first.close.assert_called_once_with()
        second.close.assert_called_once_with()
        self.assertIsNot(first, second)
        first_request = json.loads(first.sendall.call_args.args[0][:-1])
        second_request = json.loads(second.sendall.call_args.args[0][:-1])
        self.assertEqual("first", first_request["id"])
        self.assertEqual("second", second_request["id"])

    def test_tcp_adapter_rejects_eof_before_a_complete_frame(self):
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = b""
        with mock.patch(
            "sketchup_mcp.bridge.socket.create_connection",
            return_value=connection,
        ):
            with self.assertRaisesRegex(ConnectionError, "closed before completing"):
                TCPBridgeAdapter().exchange(b"request", timeout=1)

    def test_bridge_constructors_preserve_defaults_and_environment_port(self):
        tcp_client = BridgeClient.for_tcp(port=1234, timeout=2.5, max_attempts=4)
        self.assertEqual("127.0.0.1:1234", tcp_client.adapter.endpoint)
        self.assertEqual(2.5, tcp_client.timeout)
        self.assertEqual(4, tcp_client.max_attempts)

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                "127.0.0.1:9876", BridgeClient.from_environment().adapter.endpoint
            )
        with mock.patch.dict(
            os.environ, {"SKETCHUP_MCP_BRIDGE_PORT": "2468"}, clear=True
        ):
            self.assertEqual(
                "127.0.0.1:2468", BridgeClient.from_environment().adapter.endpoint
            )

    def test_in_memory_adapter_exposes_requests_results_and_custom_endpoint(self):
        adapter = InMemoryBridgeAdapter.returning({"selected": []})
        result = BridgeClient(adapter=adapter).send_command(
            "get_selection", request_id="request-1"
        )
        custom = InMemoryBridgeAdapter(lambda _request: {}, endpoint="memory:test")

        self.assertEqual({"selected": []}, result)
        self.assertEqual("memory:test", custom.endpoint)
        self.assertEqual("get_selection", adapter.requests[0]["params"]["name"])

    def test_timeout_retries_then_succeeds_with_the_same_request(self):
        adapter = SequenceAdapter(
            socket.timeout("slow"),
            response_bytes(result={"ok": True}),
        )
        result = BridgeClient(adapter=adapter, timeout=0.5, max_attempts=2).send_command(
            "get_selection", request_id="request-1"
        )

        self.assertEqual({"ok": True}, result)
        self.assertEqual(["request-1", "request-1"], [item[0]["id"] for item in adapter.requests])
        self.assertEqual([0.5, 0.5], [item[1] for item in adapter.requests])

    def test_timeout_and_unavailability_stop_at_the_attempt_limit(self):
        timeout_adapter = SequenceAdapter(socket.timeout(), socket.timeout())
        with self.assertRaisesRegex(BridgeTimeout, "timed out after 2 attempts"):
            BridgeClient(adapter=timeout_adapter, max_attempts=2).send_command(
                "get_selection", request_id="request-1"
            )

        unavailable_adapter = SequenceAdapter(OSError("offline"), OSError("offline"))
        with self.assertRaisesRegex(
            BridgeUnavailable,
            "unavailable at 127.0.0.1:4321 after 2 attempts: offline",
        ):
            BridgeClient(adapter=unavailable_adapter, max_attempts=2).send_command(
                "get_selection", request_id="request-1"
            )

    def test_unavailability_retries_then_succeeds(self):
        adapter = SequenceAdapter(
            OSError("reconnect"),
            response_bytes(result={"reconnected": True}),
        )
        result = BridgeClient(adapter=adapter, max_attempts=2).send_command(
            "get_selection", request_id="request-1"
        )
        self.assertEqual({"reconnected": True}, result)

    def test_attempt_limit_and_catalog_are_validated_before_exchange(self):
        adapter = InMemoryBridgeAdapter.returning({})
        with self.assertRaisesRegex(ValueError, "max_attempts must be at least 1"):
            BridgeClient(adapter=adapter, max_attempts=0).send_command(
                "get_selection", request_id="request-1"
            )
        with self.assertRaises(TypeError):
            BridgeClient(adapter=adapter, max_attempts=1.5).send_command(
                "get_selection", request_id="request-1"
            )
        with self.assertRaisesRegex(ValueError, "Unknown SketchUp command"):
            BridgeClient(adapter=adapter).send_command(
                "unknown_command", request_id="request-1"
            )
        with self.assertRaisesRegex(ValueError, "tools/call"):
            BridgeClient(adapter=adapter).send_command("tools/call", request_id="request-1")
        with self.assertRaisesRegex(ValueError, "tools/call"):
            BridgeClient(adapter=adapter).send_command(
                "tools/call", {"name": "get_selection"}, request_id="request-1"
            )
        self.assertEqual([], adapter.requests)

    def test_direct_and_preformed_tool_calls_have_the_same_wire_shape(self):
        direct = InMemoryBridgeAdapter.returning({})
        preformed = InMemoryBridgeAdapter.returning({})
        BridgeClient(adapter=direct).send_command(
            "get_selection", {}, request_id="request-1"
        )
        BridgeClient(adapter=preformed).send_command(
            "tools/call",
            {"name": "get_selection", "arguments": {}},
            request_id="request-1",
        )
        self.assertEqual(direct.requests, preformed.requests)

    def test_malformed_response_frames_are_final_protocol_errors(self):
        cases = (
            (b"\xff", "malformed JSON response"),
            (b"{", "malformed JSON response"),
            (b"[]", "response must be an object"),
            (json.dumps({"jsonrpc": "1.0", "id": "request-1", "result": {}}).encode(), "version"),
            (json.dumps({"jsonrpc": "2.0", "result": {}}).encode(), "contain an id"),
            (response_bytes(result={}, request_id="different"), "does not match"),
            (json.dumps({"jsonrpc": "2.0", "id": "request-1"}).encode(), "exactly one"),
            (response_bytes(result={}, error={}), "exactly one"),
            (response_bytes(error="bad"), "error must be an object"),
            (response_bytes(error={"code": "bad", "message": "failure"}), "integer code"),
            (response_bytes(error={"code": -1, "message": 5}), "string message"),
        )
        for payload, message in cases:
            with self.subTest(message=message):
                adapter = SequenceAdapter(payload)
                with self.assertRaisesRegex(BridgeProtocolError, message):
                    BridgeClient(adapter=adapter).send_command(
                        "get_selection", request_id="request-1"
                    )
                self.assertEqual(1, len(adapter.requests))

    def test_remote_errors_preserve_structured_fields_and_are_final(self):
        adapter = SequenceAdapter(
            response_bytes(
                error={"code": -32603, "message": "failed", "data": {"safe": True}}
            )
        )
        with self.assertRaises(BridgeRemoteError) as raised:
            BridgeClient(adapter=adapter, max_attempts=3).send_command(
                "get_selection", request_id="request-1"
            )

        self.assertEqual(-32603, raised.exception.code)
        self.assertEqual("failed", raised.exception.message)
        self.assertEqual({"safe": True}, raised.exception.data)
        self.assertEqual("request-1", raised.exception.request_id)
        self.assertEqual(1, len(adapter.requests))
