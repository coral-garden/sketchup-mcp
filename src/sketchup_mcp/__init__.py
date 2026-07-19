"""SketchUp integration through the Model Context Protocol.

Runtime entry points live in :mod:`sketchup_mcp.mcp_server`; the historic
``sketchup_mcp.server`` path remains a compatibility module. Keeping package
import side-effect free lets tooling consume static contracts without starting
FastMCP or attempting a SketchUp connection.
"""

from ._version import __version__

__all__ = ["__version__", "mcp"]


def __getattr__(name: str):
    """Preserve the historic ``sketchup_mcp.mcp`` export lazily."""

    if name == "mcp":
        from .mcp_server import mcp

        return mcp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
