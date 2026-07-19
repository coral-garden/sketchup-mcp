import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from sketchup_mcp.bridge import (
    BridgeClient,
    BridgeProtocolError,
    BridgeRemoteError,
    BridgeTimeout,
    BridgeUnavailable,
)


class ScriptedBridge:
    def __init__(self, exchanges):
        self._exchanges = exchanges
        self.requests = []
        self._error = None
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen()
        self.port = self._socket.getsockname()[1]

    def __enter__(self):
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._thread.join(timeout=2)
        self._socket.close()
        if self._thread.is_alive():
            raise AssertionError("scripted bridge did not finish")
        if self._error:
            raise self._error

    def _serve(self):
        try:
            self._socket.settimeout(2)
            for exchange in self._exchanges:
                client, _ = self._socket.accept()
                with client:
                    client.settimeout(2)
                    frame = self._read_frame(client)
                    request = json.loads(frame)
                    self.requests.append(request)
                    exchange(client, request)
        except BaseException as error:
            self._error = error

    @staticmethod
    def _read_frame(client):
        frame = bytearray()
        while not frame.endswith(b"\n"):
            chunk = client.recv(4096)
            if not chunk:
                raise AssertionError("request ended before newline frame")
            frame.extend(chunk)
        return frame


def send_result(result):
    def exchange(client, request):
        response = {
            "jsonrpc": "2.0",
            "result": result,
            "id": request["id"],
        }
        client.sendall(json.dumps(response).encode("utf-8") + b"\n")

    return exchange


def send_response(response):
    def exchange(client, _request):
        client.sendall(json.dumps(response).encode("utf-8") + b"\n")

    return exchange


def send_bytes(*chunks):
    def exchange(client, _request):
        for chunk in chunks:
            client.sendall(chunk)

    return exchange


def close_without_response(_client, _request):
    return None


def delay_without_response(_client, _request):
    time.sleep(0.05)


class LegacyPersistentBridgeClient:
    """Test fixture that reintroduces the pre-ADR persistent socket lifecycle."""

    def __init__(self, port, timeout=1):
        self._connection = socket.create_connection(
            ("127.0.0.1", port), timeout=timeout
        )
        self._connection.settimeout(timeout)
        self.responses = []

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self._connection.close()

    def send_command(self, method, params=None, request_id=None):
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": method, "arguments": params or {}},
            "id": request_id,
        }
        try:
            self._connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
            response_frame = self._read_frame()
        except OSError as error:
            raise ConnectionError(
                "bridge closed before completing a response frame"
            ) from error
        response = json.loads(response_frame.decode("utf-8"))
        if response.get("id") != request_id:
            raise BridgeProtocolError(
                f"response id {response.get('id')!r} does not match "
                f"request id {request_id!r}"
            )
        self.responses.append(response)
        return response["result"]

    def _read_frame(self):
        frame = bytearray()
        while not frame.endswith(b"\n"):
            chunk = self._connection.recv(8192)
            if not chunk:
                raise ConnectionError(
                    "bridge closed before completing a response frame"
                )
            frame.extend(chunk)
        return bytes(frame[:-1])


class RubyCloseBridgeFixture:
    """Run the real one-request-per-connection Ruby listener fixture."""

    def __init__(self, fixture_path):
        self._fixture_path = fixture_path
        self._process = None
        self._communicated = False
        self.port = None

    def __enter__(self):
        self._process = subprocess.Popen(
            ["ruby", str(self._fixture_path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        ready_frame = self._process.stdout.readline()
        if not ready_frame:
            _stdout, stderr = self._process.communicate(timeout=2)
            self._communicated = True
            raise AssertionError(f"Ruby bridge fixture failed to start: {stderr}")
        self.port = json.loads(ready_frame)["port"]
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        if self._communicated:
            return
        if self._process.poll() is None:
            self._process.terminate()
        try:
            self._process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.communicate(timeout=2)
        self._communicated = True

    def wait_for_clean_exit(self):
        _stdout, stderr = self._process.communicate(timeout=2)
        self._communicated = True
        if self._process.returncode != 0:
            raise AssertionError(
                f"Ruby bridge fixture exited with {self._process.returncode}: {stderr}"
            )


class BridgeLifecycleTest(unittest.TestCase):
    def test_request_and_response_are_newline_framed_and_preserve_id(self):
        with ScriptedBridge([send_result({"ok": True})]) as bridge:
            client = BridgeClient.for_tcp(port=bridge.port)

            result = client.send_command(
                "tools/call",
                {"name": "get_selection", "arguments": {}},
                request_id="request-17",
            )

        self.assertEqual({"ok": True}, result)
        self.assertEqual(
            [
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "get_selection", "arguments": {}},
                    "id": "request-17",
                }
            ],
            bridge.requests,
        )

    def test_response_id_must_match_request_id(self):
        response = {"jsonrpc": "2.0", "result": {}, "id": "someone-else"}
        with ScriptedBridge([send_response(response)]) as bridge:
            client = BridgeClient.for_tcp(port=bridge.port)

            with self.assertRaisesRegex(
                BridgeProtocolError,
                "response id 'someone-else' does not match request id 'request-17'",
            ):
                client.send_command("get_selection", request_id="request-17")

    def test_remote_jsonrpc_errors_are_not_flattened_or_retried(self):
        response = {
            "jsonrpc": "2.0",
            "error": {
                "code": -32603,
                "message": "SketchUp operation failed",
                "data": {"tool": "create_component"},
            },
            "id": 42,
        }
        with ScriptedBridge([send_response(response)]) as bridge:
            client = BridgeClient.for_tcp(port=bridge.port)

            with self.assertRaises(BridgeRemoteError) as raised:
                client.send_command("create_component", request_id=42)

        self.assertEqual(-32603, raised.exception.code)
        self.assertEqual("SketchUp operation failed", raised.exception.message)
        self.assertEqual({"tool": "create_component"}, raised.exception.data)
        self.assertEqual(42, raised.exception.request_id)
        self.assertEqual(1, len(bridge.requests))

    def test_malformed_json_is_a_protocol_error_and_is_not_retried(self):
        with ScriptedBridge([send_bytes(b'{"jsonrpc":"2.0",nope}\n')]) as bridge:
            client = BridgeClient.for_tcp(port=bridge.port)

            with self.assertRaisesRegex(
                BridgeProtocolError, "malformed JSON response"
            ):
                client.send_command("get_selection", request_id=9)

        self.assertEqual(1, len(bridge.requests))

    def test_malformed_jsonrpc_object_is_a_protocol_error(self):
        response = {"jsonrpc": "2.0", "id": 10}
        with ScriptedBridge([send_response(response)]) as bridge:
            client = BridgeClient.for_tcp(port=bridge.port)

            with self.assertRaisesRegex(
                BridgeProtocolError, "exactly one of result or error"
            ):
                client.send_command("get_selection", request_id=10)

    def test_malformed_remote_error_is_a_protocol_error(self):
        response = {"jsonrpc": "2.0", "error": "nope", "id": 11}
        with ScriptedBridge([send_response(response)]) as bridge:
            client = BridgeClient.for_tcp(port=bridge.port)

            with self.assertRaisesRegex(
                BridgeProtocolError, "error must be an object"
            ):
                client.send_command("get_selection", request_id=11)

    def test_eof_reconnects_and_retries_the_same_request(self):
        with ScriptedBridge(
            [close_without_response, send_result({"reconnected": True})]
        ) as bridge:
            client = BridgeClient.for_tcp(port=bridge.port, max_attempts=2)

            result = client.send_command("get_selection", request_id=73)

        self.assertEqual({"reconnected": True}, result)
        self.assertEqual([73, 73], [request["id"] for request in bridge.requests])

    def test_timeout_stops_after_the_configured_attempt_limit(self):
        with ScriptedBridge(
            [delay_without_response, delay_without_response]
        ) as bridge:
            client = BridgeClient.for_tcp(
                port=bridge.port, timeout=0.01, max_attempts=2
            )

            with self.assertRaisesRegex(BridgeTimeout, "timed out after 2 attempts"):
                client.send_command("get_selection", request_id=81)

        self.assertEqual([81, 81], [request["id"] for request in bridge.requests])

    def test_response_may_arrive_in_multiple_tcp_chunks(self):
        response = json.dumps(
            {"jsonrpc": "2.0", "result": {"chunked": True}, "id": 91}
        ).encode("utf-8")
        with ScriptedBridge(
            [send_bytes(response[:5], response[5:19], response[19:] + b"\n")]
        ) as bridge:
            client = BridgeClient.for_tcp(port=bridge.port)

            result = client.send_command("get_selection", request_id=91)

        self.assertEqual({"chunked": True}, result)

    def test_consecutive_commands_use_independent_scripted_connections(self):
        with ScriptedBridge(
            [send_result({"request": 1}), send_result({"request": 2})]
        ) as bridge:
            client = BridgeClient.for_tcp(port=bridge.port)

            first = client.send_command("get_selection", request_id=101)
            second = client.send_command("get_selection", request_id=102)

        self.assertEqual({"request": 1}, first)
        self.assertEqual({"request": 2}, second)
        self.assertEqual([101, 102], [request["id"] for request in bridge.requests])

    def test_unavailable_port_maps_to_a_bounded_connection_error(self):
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reservation.bind(("127.0.0.1", 0))
        unused_port = reservation.getsockname()[1]
        reservation.close()

        client = BridgeClient.for_tcp(
            port=unused_port, timeout=0.01, max_attempts=2
        )

        with self.assertRaisesRegex(
            BridgeUnavailable,
            rf"127\.0\.0\.1:{unused_port} after 2 attempts",
        ):
            client.send_command("get_selection", request_id=111)

    def test_port_is_configured_from_one_environment_variable(self):
        with patch.dict(os.environ, {"SKETCHUP_MCP_BRIDGE_PORT": "12345"}):
            client = BridgeClient.from_environment()

        self.assertEqual("127.0.0.1:12345", client.adapter.endpoint)

    def test_tcp_adapter_connects_to_ipv4_loopback_only(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = json.dumps(
            {"jsonrpc": "2.0", "result": {"ok": True}, "id": "loopback"}
        ).encode("utf-8") + b"\n"

        with patch(
            "sketchup_mcp.bridge.socket.create_connection",
            return_value=connection,
        ) as create_connection:
            client = BridgeClient.for_tcp(port=12_345, timeout=0.25)
            client.send_command("get_selection", request_id="loopback")

        create_connection.assert_called_once_with(("127.0.0.1", 12_345), 0.25)

    def test_ruby_close_breaks_legacy_persistence_but_not_bridge_client(self):
        fixture = (
            Path(__file__).parent.parent / "test" / "fixtures" / "ruby_bridge_fixture.rb"
        )
        with RubyCloseBridgeFixture(fixture) as legacy_bridge:
            with LegacyPersistentBridgeClient(legacy_bridge.port) as legacy_client:
                with self.assertRaisesRegex(
                    ConnectionError,
                    "bridge closed before completing a response frame",
                ):
                    self.assert_two_commands_complete(legacy_client)
                self.assertEqual(
                    ["lifecycle-1"],
                    [response["id"] for response in legacy_client.responses],
                )

        with RubyCloseBridgeFixture(fixture) as current_bridge:
            client = BridgeClient.for_tcp(port=current_bridge.port)
            self.assert_two_commands_complete(client)
            current_bridge.wait_for_clean_exit()

    def assert_two_commands_complete(self, client):
        results = [
            client.send_command("get_selection", request_id="lifecycle-1"),
            client.send_command("get_selection", request_id="lifecycle-2"),
        ]

        self.assertEqual(
            [
                {"request": 1, "request_id": "lifecycle-1"},
                {"request": 2, "request_id": "lifecycle-2"},
            ],
            results,
        )

    def test_mcp_tool_reports_an_unavailable_bridge_port(self):
        from sketchup_mcp import server

        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reservation.bind(("127.0.0.1", 0))
        unused_port = reservation.getsockname()[1]
        reservation.close()
        unavailable_client = BridgeClient.for_tcp(
            port=unused_port, timeout=0.01, max_attempts=2
        )
        context = type("RequestContext", (), {"request_id": "mcp-121"})()
        with server.use_bridge_client(unavailable_client):
            result = server.create_component(context)

        self.assertIn("Error creating component:", result)
        self.assertIn(
            f"SketchUp bridge unavailable at 127.0.0.1:{unused_port} after 2 attempts",
            result,
        )


if __name__ == "__main__":
    unittest.main()
