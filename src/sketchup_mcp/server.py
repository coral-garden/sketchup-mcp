from mcp.server.fastmcp import FastMCP, Context
import logging
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Dict, Any, Iterator, List

from .bridge import BridgeClient
from .catalog_fastmcp import CatalogFastMCP
from .mcp_server import CommandForwarder

# Configure logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SketchupMCPServer")

# Define version directly to avoid pkg_resources dependency
__version__ = "0.1.17"
logger.info(f"SketchupMCP Server version {__version__} starting up")

# Lazily configured stateless bridge client.
_bridge_client: BridgeClient | None = None


def get_bridge_client() -> BridgeClient:
    """Get the stateless client for one-request-per-connection exchanges."""
    global _bridge_client
    if _bridge_client is None:
        _bridge_client = BridgeClient.from_environment()
    return _bridge_client


@contextmanager
def use_bridge_client(client: BridgeClient) -> Iterator[None]:
    """Compose MCP tool handlers with a supplied bridge client for this scope."""

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
    """Forward one statically registered public command through the catalog path."""

    return CommandForwarder(get_bridge_client()).call(
        command,
        arguments,
        request_id=request_id,
    )


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    try:
        logger.info("SketchupMCP server starting up")
        get_bridge_client()
        yield {}
    finally:
        global _bridge_client
        _bridge_client = None
        logger.info("SketchupMCP server shut down")

# Create MCP server with lifespan support
mcp = CatalogFastMCP(
    "SketchupMCP",
    instructions="Sketchup integration through the Model Context Protocol",
    lifespan=server_lifespan,
)

# Tool endpoints
@mcp.tool()
def create_component(
    ctx: Context,
    type: str = "cube",
    position: List[float] = None,
    dimensions: List[float] = None
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
def delete_component(
    ctx: Context,
    id: int | str
) -> str:
    return forward_command(ctx.request_id, "delete_component", {"id": id})

@mcp.tool()
def transform_component(
    ctx: Context,
    id: int | str,
    position: List[float] = None,
    rotation: List[float] = None,
    scale: List[float] = None
) -> str:
    arguments = {"id": id}
    if position is not None:
        arguments["position"] = position
    if rotation is not None:
        arguments["rotation"] = rotation
    if scale is not None:
        arguments["scale"] = scale
    return forward_command(
        ctx.request_id,
        "transform_component",
        arguments,
    )

@mcp.tool()
def get_selection(ctx: Context) -> str:
    return forward_command(ctx.request_id, "get_selection", {})

@mcp.tool()
def set_material(
    ctx: Context,
    id: int | str,
    material: str
) -> str:
    return forward_command(
        ctx.request_id,
        "set_material",
        {"id": id, "material": material},
    )

@mcp.tool()
def export_scene(
    ctx: Context,
    format: str = "skp"
) -> str:
    return forward_command(
        ctx.request_id,
        "export_scene",
        {"format": format},
    )


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
    offset_z: float = 0.0
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
    offset_z: float = 0.0
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
    offset_z: float = 0.0
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
def eval_ruby(
    ctx: Context,
    code: str
) -> str:
    return forward_command(
        ctx.request_id,
        "eval_ruby",
        {"code": code},
    )

def main():
    mcp.run()

if __name__ == "__main__":
    main()
