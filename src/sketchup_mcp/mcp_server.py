"""Canonical Python MCP server, including tools, lifecycle, and stdio launch."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator

from mcp.server.fastmcp import Context, FastMCP

from ._version import __version__
from .bridge import BridgeClient
from .catalog_fastmcp import CatalogFastMCP
from .command_catalog import CommandCatalog, load_command_catalog


logger = logging.getLogger("SketchUpMCP.MCPServer")

@dataclass(frozen=True)
class CommandForwarder:
    """Carry one public command across the bridge seam."""

    bridge_client: BridgeClient
    catalog: CommandCatalog = field(default_factory=load_command_catalog)

    def call(
        self,
        command: str,
        arguments: dict[str, Any],
        request_id: Any,
    ) -> str:
        """Return the bridge success envelope serialized as JSON."""

        contract = self.catalog.command(command)
        logger.info(
            "MCP server tool called: command=%s request_id=%r",
            command,
            request_id,
        )
        try:
            result = self.bridge_client.send_command(
                command,
                arguments,
                request_id=request_id,
            )
        except Exception as error:
            logger.error(
                "MCP server tool failed: command=%s request_id=%r error_type=%s",
                command,
                request_id,
                type(error).__name__,
            )
            return f"Error {contract.failure_action}: {error}"
        logger.info(
            "MCP server tool completed: command=%s request_id=%r",
            command,
            request_id,
        )
        return json.dumps(result)


_bridge_client: BridgeClient | None = None


def get_bridge_client() -> BridgeClient:
    """Return the bridge client used by MCP tool handlers."""

    global _bridge_client
    if _bridge_client is None:
        _bridge_client = BridgeClient.from_environment()
    return _bridge_client


@contextmanager
def use_bridge_client(client: BridgeClient) -> Iterator[None]:
    """Use a supplied bridge client for one scoped MCP server interaction."""

    global _bridge_client
    previous = _bridge_client
    _bridge_client = client
    try:
        yield
    finally:
        _bridge_client = previous


def forward_command(
    request_id: Any,
    command: str,
    arguments: dict[str, Any],
) -> str:
    """Forward one statically registered public command through the bridge."""

    return CommandForwarder(get_bridge_client()).call(
        command,
        arguments,
        request_id=request_id,
    )


@asynccontextmanager
async def server_lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage the Python MCP server lifecycle."""

    logger.info("MCP server starting: version=%s", __version__)
    try:
        get_bridge_client()
        yield {}
    finally:
        global _bridge_client
        _bridge_client = None
        logger.info("MCP server stopped")


mcp = CatalogFastMCP(
    "SketchUp MCP",
    instructions="Sketchup integration through the Model Context Protocol",
    lifespan=server_lifespan,
)


@mcp.tool()
def create_component(
    ctx: Context,
    type: str = "cube",
    position: list[float] | None = None,
    dimensions: list[float] | None = None,
) -> str:
    return forward_command(
        ctx.request_id,
        "create_component",
        {
            "type": type,
            "position": position if position is not None else [0, 0, 0],
            "dimensions": dimensions if dimensions is not None else [1, 1, 1],
        },
    )


@mcp.tool()
def delete_component(ctx: Context, id: int | str) -> str:
    return forward_command(ctx.request_id, "delete_component", {"id": id})


@mcp.tool()
def transform_component(
    ctx: Context,
    id: int | str,
    position: list[float] | None = None,
    rotation: list[float] | None = None,
    scale: list[float] | None = None,
) -> str:
    arguments = {"id": id}
    if position is not None:
        arguments["position"] = position
    if rotation is not None:
        arguments["rotation"] = rotation
    if scale is not None:
        arguments["scale"] = scale
    return forward_command(ctx.request_id, "transform_component", arguments)


@mcp.tool()
def get_selection(ctx: Context) -> str:
    return forward_command(ctx.request_id, "get_selection", {})


@mcp.tool()
def set_material(ctx: Context, id: int | str, material: str) -> str:
    return forward_command(
        ctx.request_id,
        "set_material",
        {"id": id, "material": material},
    )


@mcp.tool()
def export_scene(ctx: Context, format: str = "skp") -> str:
    return forward_command(ctx.request_id, "export_scene", {"format": format})


@mcp.tool()
def boolean_operation(
    ctx: Context,
    operation: str,
    target_id: int | str,
    tool_id: int | str,
    delete_originals: bool = False,
) -> str:
    return forward_command(
        ctx.request_id,
        "boolean_operation",
        {
            "operation": operation,
            "target_id": target_id,
            "tool_id": tool_id,
            "delete_originals": delete_originals,
        },
    )


@mcp.tool()
def create_mortise_tenon(
    ctx: Context,
    mortise_id: int | str,
    tenon_id: int | str,
    width: float = 1.0,
    height: float = 1.0,
    depth: float = 1.0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    offset_z: float = 0.0,
) -> str:
    return forward_command(
        ctx.request_id,
        "create_mortise_tenon",
        {
            "mortise_id": mortise_id,
            "tenon_id": tenon_id,
            "width": width,
            "height": height,
            "depth": depth,
            "offset_x": offset_x,
            "offset_y": offset_y,
            "offset_z": offset_z,
        },
    )


@mcp.tool()
def create_dovetail(
    ctx: Context,
    tail_id: int | str,
    pin_id: int | str,
    width: float = 1.0,
    height: float = 1.0,
    depth: float = 1.0,
    angle: float = 15.0,
    num_tails: int = 3,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    offset_z: float = 0.0,
) -> str:
    return forward_command(
        ctx.request_id,
        "create_dovetail",
        {
            "tail_id": tail_id,
            "pin_id": pin_id,
            "width": width,
            "height": height,
            "depth": depth,
            "angle": angle,
            "num_tails": num_tails,
            "offset_x": offset_x,
            "offset_y": offset_y,
            "offset_z": offset_z,
        },
    )


@mcp.tool()
def create_finger_joint(
    ctx: Context,
    board1_id: int | str,
    board2_id: int | str,
    width: float = 1.0,
    height: float = 1.0,
    depth: float = 1.0,
    num_fingers: int = 5,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    offset_z: float = 0.0,
) -> str:
    return forward_command(
        ctx.request_id,
        "create_finger_joint",
        {
            "board1_id": board1_id,
            "board2_id": board2_id,
            "width": width,
            "height": height,
            "depth": depth,
            "num_fingers": num_fingers,
            "offset_x": offset_x,
            "offset_y": offset_y,
            "offset_z": offset_z,
        },
    )


@mcp.tool()
def eval_ruby(ctx: Context, code: str) -> str:
    return forward_command(ctx.request_id, "eval_ruby", {"code": code})


def main() -> None:
    """Run the Python MCP server over stdio."""

    mcp.run()


__all__ = (
    "__version__",
    "mcp",
    "main",
    "server_lifespan",
    "get_bridge_client",
    "use_bridge_client",
    "forward_command",
    *load_command_catalog().names,
)
