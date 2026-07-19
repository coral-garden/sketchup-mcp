from mcp.server.fastmcp import FastMCP, Context
import json
import logging
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Dict, Any, Iterator, List

from .bridge import BridgeClient
from .catalog_fastmcp import CatalogFastMCP
from .mcp_server import CreateComponentTool, SceneGeometryTools

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


def call_scene_geometry_tool(
    request_id: Any,
    command: str,
    arguments: dict[str, Any],
    failure_action: str,
) -> str:
    """Serialize a bridge result while retaining the public tool error wording."""

    try:
        return SceneGeometryTools(get_bridge_client()).call(
            command,
            arguments,
            request_id=request_id,
        )
    except Exception as error:
        return f"Error {failure_action}: {error}"


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

# The scene/geometry family is catalog-governed while the remaining handlers are
# migrated independently by the next command-family issue.
SCENE_GEOMETRY_COMMANDS = (
    "create_component",
    "delete_component",
    "transform_component",
    "get_selection",
    "set_material",
    "export_scene",
    "boolean_operation",
)

# Create MCP server with lifespan support
mcp = CatalogFastMCP(
    "SketchupMCP",
    instructions="Sketchup integration through the Model Context Protocol",
    lifespan=server_lifespan,
    catalog_commands=SCENE_GEOMETRY_COMMANDS,
)

# Tool endpoints
@mcp.tool()
def create_component(
    ctx: Context,
    type: str = "cube",
    position: List[float] = None,
    dimensions: List[float] = None
) -> str:
    """Create a new component in Sketchup"""
    return CreateComponentTool(get_bridge_client()).create_component(
        request_id=ctx.request_id,
        component_type=type,
        position=position,
        dimensions=dimensions,
    )

@mcp.tool()
def delete_component(
    ctx: Context,
    id: int | str
) -> str:
    """Delete a component by ID"""
    return call_scene_geometry_tool(
        ctx.request_id, "delete_component", {"id": id}, "deleting component"
    )

@mcp.tool()
def transform_component(
    ctx: Context,
    id: int | str,
    position: List[float] = None,
    rotation: List[float] = None,
    scale: List[float] = None
) -> str:
    """Transform a component's position, rotation, or scale"""
    arguments = {"id": id}
    if position is not None:
        arguments["position"] = position
    if rotation is not None:
        arguments["rotation"] = rotation
    if scale is not None:
        arguments["scale"] = scale
    return call_scene_geometry_tool(
        ctx.request_id,
        "transform_component",
        arguments,
        "transforming component",
    )

@mcp.tool()
def get_selection(ctx: Context) -> str:
    """Get currently selected components"""
    return call_scene_geometry_tool(
        ctx.request_id, "get_selection", {}, "getting selection"
    )

@mcp.tool()
def set_material(
    ctx: Context,
    id: int | str,
    material: str
) -> str:
    """Set material for a component"""
    return call_scene_geometry_tool(
        ctx.request_id,
        "set_material",
        {"id": id, "material": material},
        "setting material",
    )

@mcp.tool()
def export_scene(
    ctx: Context,
    format: str = "skp"
) -> str:
    """Export the current scene"""
    return call_scene_geometry_tool(
        ctx.request_id,
        "export_scene",
        {"format": format},
        "exporting scene",
    )


@mcp.tool()
def boolean_operation(
    ctx: Context,
    operation: str,
    target_id: int | str,
    tool_id: int | str,
    delete_originals: bool = False,
) -> str:
    """Create the union, difference, or intersection of two solid groups."""
    return call_scene_geometry_tool(
        ctx.request_id,
        "boolean_operation",
        {
            "operation": operation,
            "target_id": target_id,
            "tool_id": tool_id,
            "delete_originals": delete_originals,
        },
        "performing boolean operation",
    )

@mcp.tool()
def create_mortise_tenon(
    ctx: Context,
    mortise_id: str,
    tenon_id: str,
    width: float = 1.0,
    height: float = 1.0,
    depth: float = 1.0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    offset_z: float = 0.0
) -> str:
    """Create a mortise and tenon joint between two components"""
    try:
        logger.info(f"create_mortise_tenon called with mortise_id={mortise_id}, tenon_id={tenon_id}, width={width}, height={height}, depth={depth}, offsets=({offset_x}, {offset_y}, {offset_z})")
        
        sketchup = get_bridge_client()
        
        result = sketchup.send_command(
            method="tools/call",
            params={
                "name": "create_mortise_tenon",
                "arguments": {
                    "mortise_id": mortise_id,
                    "tenon_id": tenon_id,
                    "width": width,
                    "height": height,
                    "depth": depth,
                    "offset_x": offset_x,
                    "offset_y": offset_y,
                    "offset_z": offset_z
                }
            },
            request_id=ctx.request_id
        )
        
        logger.info(f"create_mortise_tenon result: {result}")
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error in create_mortise_tenon: {str(e)}")
        return f"Error creating mortise and tenon joint: {str(e)}"

@mcp.tool()
def create_dovetail(
    ctx: Context,
    tail_id: str,
    pin_id: str,
    width: float = 1.0,
    height: float = 1.0,
    depth: float = 1.0,
    angle: float = 15.0,
    num_tails: int = 3,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    offset_z: float = 0.0
) -> str:
    """Create a dovetail joint between two components"""
    try:
        logger.info(f"create_dovetail called with tail_id={tail_id}, pin_id={pin_id}, width={width}, height={height}, depth={depth}, angle={angle}, num_tails={num_tails}")
        
        sketchup = get_bridge_client()
        
        result = sketchup.send_command(
            method="tools/call",
            params={
                "name": "create_dovetail",
                "arguments": {
                    "tail_id": tail_id,
                    "pin_id": pin_id,
                    "width": width,
                    "height": height,
                    "depth": depth,
                    "angle": angle,
                    "num_tails": num_tails,
                    "offset_x": offset_x,
                    "offset_y": offset_y,
                    "offset_z": offset_z
                }
            },
            request_id=ctx.request_id
        )
        
        logger.info(f"create_dovetail result: {result}")
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error in create_dovetail: {str(e)}")
        return f"Error creating dovetail joint: {str(e)}"

@mcp.tool()
def create_finger_joint(
    ctx: Context,
    board1_id: str,
    board2_id: str,
    width: float = 1.0,
    height: float = 1.0,
    depth: float = 1.0,
    num_fingers: int = 5,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    offset_z: float = 0.0
) -> str:
    """Create a finger joint (box joint) between two components"""
    try:
        logger.info(f"create_finger_joint called with board1_id={board1_id}, board2_id={board2_id}, width={width}, height={height}, depth={depth}, num_fingers={num_fingers}")
        
        sketchup = get_bridge_client()
        
        result = sketchup.send_command(
            method="tools/call",
            params={
                "name": "create_finger_joint",
                "arguments": {
                    "board1_id": board1_id,
                    "board2_id": board2_id,
                    "width": width,
                    "height": height,
                    "depth": depth,
                    "num_fingers": num_fingers,
                    "offset_x": offset_x,
                    "offset_y": offset_y,
                    "offset_z": offset_z
                }
            },
            request_id=ctx.request_id
        )
        
        logger.info(f"create_finger_joint result: {result}")
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error in create_finger_joint: {str(e)}")
        return f"Error creating finger joint: {str(e)}"

@mcp.tool()
def eval_ruby(
    ctx: Context,
    code: str
) -> str:
    """Evaluate arbitrary Ruby code in Sketchup"""
    try:
        logger.info(f"eval_ruby called with code length: {len(code)}")
        
        sketchup = get_bridge_client()
        
        result = sketchup.send_command(
            method="tools/call",
            params={
                "name": "eval_ruby",
                "arguments": {
                    "code": code
                }
            },
            request_id=ctx.request_id
        )
        
        logger.info(f"eval_ruby result: {result}")
        
        # Format the response to include the result
        response = {
            "success": True,
            "result": result.get("content", [{"text": "Success"}])[0].get("text", "Success") if isinstance(result.get("content"), list) and len(result.get("content", [])) > 0 else "Success"
        }
        
        return json.dumps(response)
    except Exception as e:
        logger.error(f"Error in eval_ruby: {str(e)}")
        return json.dumps({
            "success": False,
            "error": str(e)
        })

def main():
    mcp.run()

if __name__ == "__main__":
    main()
