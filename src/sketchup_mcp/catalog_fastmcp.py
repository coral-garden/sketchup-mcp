"""FastMCP boundary governed by selected authoritative command contracts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ContentBlock, ErrorData, INVALID_PARAMS, Tool

from .command_catalog import (
    InvalidCommandArguments,
    load_command_catalog,
    manifest_tools,
    validate_command_arguments,
)


class CatalogFastMCP(FastMCP):
    """Publish and enforce catalog schemas without modifying FastMCP internals."""

    def __init__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._command_catalog = load_command_catalog()
        self._catalog_tools = {
            tool["name"]: tool
            for tool in manifest_tools(self._command_catalog)
        }
        super().__init__(*args, **kwargs)

    async def list_tools(self) -> list[Tool]:
        """Replace inferred schemas with authored schemas at discovery time."""

        tools = await super().list_tools()
        return [
            tool.model_copy(
                update={
                    "description": self._catalog_tools[tool.name]["description"],
                    "inputSchema": self._catalog_tools[tool.name]["parameters"],
                }
            )
            if tool.name in self._catalog_tools
            else tool
            for tool in tools
        ]

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        """Reject invalid raw arguments before FastMCP can coerce or dispatch."""

        if name in self._catalog_tools:
            try:
                validate_command_arguments(name, arguments, self._command_catalog)
            except InvalidCommandArguments as error:
                raise McpError(
                    ErrorData(
                        code=INVALID_PARAMS,
                        message=f"Invalid arguments for {name}: {error}",
                    )
                ) from error
        return await super().call_tool(name, arguments)
