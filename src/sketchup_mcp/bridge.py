"""Private newline-framed JSON-RPC bridge to the SketchUp extension."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from typing import Any


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876


class BridgeProtocolError(Exception):
    """The peer returned a response that violates the bridge contract."""


class BridgeRemoteError(Exception):
    """A JSON-RPC error returned by the SketchUp extension."""

    def __init__(self, code, message, data, request_id):
        super().__init__(f"SketchUp bridge error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data
        self.request_id = request_id


class BridgeUnavailable(ConnectionError):
    """The bridge could not complete an exchange within its retry limit."""


class BridgeTimeout(TimeoutError):
    """The bridge did not respond within its retry limit."""


@dataclass(frozen=True)
class BridgeClient:
    """Send one JSON-RPC request over each TCP connection."""

    port: int = DEFAULT_PORT
    timeout: float = 15.0
    max_attempts: int = 3

    @classmethod
    def from_environment(cls) -> "BridgeClient":
        """Build the loopback client using the shared port setting."""
        return cls(port=int(os.environ.get("SKETCHUP_MCP_BRIDGE_PORT", DEFAULT_PORT)))

    def send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        request_id: Any = None,
    ) -> Any:
        request = self._request(method, params, request_id)
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        for attempt in range(1, self.max_attempts + 1):
            try:
                with socket.create_connection(
                    (DEFAULT_HOST, self.port), self.timeout
                ) as connection:
                    connection.settimeout(self.timeout)
                    connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
                    response_frame = self._read_frame(connection)
                break
            except socket.timeout as error:
                if attempt == self.max_attempts:
                    raise BridgeTimeout(
                        f"SketchUp bridge timed out after {attempt} attempts"
                    ) from error
            except OSError as error:
                if attempt == self.max_attempts:
                    raise BridgeUnavailable(
                        f"SketchUp bridge unavailable at {DEFAULT_HOST}:{self.port} "
                        f"after {attempt} attempts: {error}"
                    ) from error
        try:
            response = json.loads(response_frame.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BridgeProtocolError(f"malformed JSON response: {error}") from error
        if not isinstance(response, dict):
            raise BridgeProtocolError("JSON-RPC response must be an object")
        if response.get("jsonrpc") != "2.0":
            raise BridgeProtocolError("JSON-RPC response version must be '2.0'")
        if response.get("id") != request_id:
            raise BridgeProtocolError(
                f"response id {response.get('id')!r} does not match "
                f"request id {request_id!r}"
            )
        has_result = "result" in response
        has_error = "error" in response
        if has_result == has_error:
            raise BridgeProtocolError(
                "JSON-RPC response must contain exactly one of result or error"
            )
        if "error" in response:
            error = response["error"]
            if not isinstance(error, dict):
                raise BridgeProtocolError("JSON-RPC response error must be an object")
            if not isinstance(error.get("code"), int) or not isinstance(
                error.get("message"), str
            ):
                raise BridgeProtocolError(
                    "JSON-RPC response error requires integer code and string message"
                )
            raise BridgeRemoteError(
                error.get("code"),
                error.get("message", "Unknown error from SketchUp"),
                error.get("data"),
                response["id"],
            )
        return response.get("result", {})

    @staticmethod
    def _request(method, params, request_id):
        if method == "tools/call" and params and {"name", "arguments"} <= params.keys():
            return {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": request_id,
            }
        return {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": method, "arguments": params or {}},
            "id": request_id,
        }

    @staticmethod
    def _read_frame(connection):
        frame = bytearray()
        while not frame.endswith(b"\n"):
            chunk = connection.recv(8192)
            if not chunk:
                raise ConnectionError("bridge closed before completing a response frame")
            frame.extend(chunk)
        return bytes(frame[:-1])
