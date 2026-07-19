"""SketchUp integration through the Model Context Protocol.

Runtime entry points live in :mod:`sketchup_mcp.server`.  Keeping package import
side-effect free lets tooling consume static contracts without starting FastMCP
or attempting a SketchUp connection.
"""

__version__ = "0.1.17"

__all__ = ["__version__", "mcp"]


def __getattr__(name: str):
    """Preserve the historic ``sketchup_mcp.mcp`` export lazily."""

    if name == "mcp":
        from .server import mcp

        return mcp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
