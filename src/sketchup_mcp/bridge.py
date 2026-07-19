"""Private newline-framed JSON-RPC bridge to the SketchUp extension."""

from __future__ import annotations

import json
import logging
import os
import socket
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .command_catalog import CommandCatalog, load_command_catalog


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
logger = logging.getLogger("SketchUpMCP.BridgeClient")


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


class BridgeAdapter(Protocol):
    """Perform one exchange with the SketchUp extension."""

    @property
    def endpoint(self) -> str:
        """Describe the destination for user-facing transport errors."""

    def exchange(self, request_payload: bytes, timeout: float) -> bytes:
        """Return the payload from one complete response."""


@dataclass(frozen=True)
class TCPBridgeAdapter:
    """Open one loopback TCP connection for each bridge exchange."""

    port: int = DEFAULT_PORT

    @property
    def endpoint(self) -> str:
        return f"{DEFAULT_HOST}:{self.port}"

    def exchange(self, request_payload: bytes, timeout: float) -> bytes:
        with socket.create_connection((DEFAULT_HOST, self.port), timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(request_payload + b"\n")
            return self._read_frame(connection)

    @staticmethod
    def _read_frame(connection) -> bytes:
        frame = bytearray()
        while not frame.endswith(b"\n"):
            chunk = connection.recv(8192)
            if not chunk:
                raise ConnectionError(
                    "bridge closed before completing a response frame"
                )
            frame.extend(chunk)
        return bytes(frame[:-1])


class InMemoryBridgeAdapter:
    """Exercise the bridge exchange seam without opening a TCP connection."""

    def __init__(
        self,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
        endpoint: str = f"{DEFAULT_HOST}:{DEFAULT_PORT}",
    ):
        self._handler = handler
        self._endpoint = endpoint
        self.requests: list[dict[str, Any]] = []

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @classmethod
    def returning(cls, result: Any) -> "InMemoryBridgeAdapter":
        """Create an adapter that returns the same successful result for each request."""

        return cls(
            lambda request: {
                "jsonrpc": "2.0",
                "result": result,
                "id": request["id"],
            }
        )

    def exchange(self, request_payload: bytes, timeout: float) -> bytes:
        del timeout
        request = json.loads(request_payload.decode("utf-8"))
        self.requests.append(request)
        response = self._handler(request)
        return json.dumps(response).encode("utf-8")


@dataclass(frozen=True)
class BridgeClient:
    """Validate and retry one JSON-RPC command exchange at a time."""

    adapter: BridgeAdapter = field(repr=False)
    timeout: float = 15.0
    max_attempts: int = 3
    catalog: CommandCatalog = field(
        default_factory=load_command_catalog,
        init=False,
        repr=False,
        compare=False,
    )

    @classmethod
    def for_tcp(
        cls,
        port: int = DEFAULT_PORT,
        timeout: float = 15.0,
        max_attempts: int = 3,
    ) -> "BridgeClient":
        """Build a client backed by the production loopback TCP adapter."""

        return cls(
            adapter=TCPBridgeAdapter(port),
            timeout=timeout,
            max_attempts=max_attempts,
        )

    @classmethod
    def from_environment(cls) -> "BridgeClient":
        """Build the loopback client using the shared port setting."""
        return cls.for_tcp(
            port=int(os.environ.get("SKETCHUP_MCP_BRIDGE_PORT", DEFAULT_PORT))
        )

    def send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        request_id: Any = None,
    ) -> Any:
        request = self._request(method, params, request_id)
        command_name = request["params"]["name"]
        try:
            self.catalog.command(command_name)
        except KeyError as error:
            raise ValueError(
                f"Unknown SketchUp command in command catalog: {command_name}"
            ) from error
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        request_payload = json.dumps(request).encode("utf-8")
        adapter = self.adapter
        # A successful exchange breaks and a failed final attempt raises, so a
        # constructed, non-empty integer attempt range cannot exhaust normally.
        for attempt in range(1, self.max_attempts + 1):  # pragma: no branch
            logger.info(
                "Bridge client exchange: command=%s request_id=%r attempt=%d endpoint=%s",
                command_name,
                request_id,
                attempt,
                adapter.endpoint,
            )
            try:
                response_payload = adapter.exchange(request_payload, self.timeout)
                logger.info(
                    "Bridge client response: command=%s request_id=%r attempt=%d",
                    command_name,
                    request_id,
                    attempt,
                )
                break
            except socket.timeout as error:
                logger.warning(
                    "Bridge client timeout: command=%s request_id=%r attempt=%d",
                    command_name,
                    request_id,
                    attempt,
                )
                if attempt == self.max_attempts:
                    raise BridgeTimeout(
                        f"SketchUp bridge timed out after {attempt} attempts"
                    ) from error
            except OSError as error:
                logger.warning(
                    "Bridge client unavailable: command=%s request_id=%r attempt=%d",
                    command_name,
                    request_id,
                    attempt,
                )
                if attempt == self.max_attempts:
                    raise BridgeUnavailable(
                        f"SketchUp bridge unavailable at {adapter.endpoint} "
                        f"after {attempt} attempts: {error}"
                    ) from error
        try:
            response = json.loads(response_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BridgeProtocolError(f"malformed JSON response: {error}") from error
        if not isinstance(response, dict):
            raise BridgeProtocolError("JSON-RPC response must be an object")
        if response.get("jsonrpc") != "2.0":
            raise BridgeProtocolError("JSON-RPC response version must be '2.0'")
        if "id" not in response:
            raise BridgeProtocolError("JSON-RPC response must contain an id")
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
        """Normalize direct commands and legacy pre-wrapped tool calls.

        Both forms remain supported until issue #11 migrates the remaining MCP
        handlers to the focused command interface.
        """

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
