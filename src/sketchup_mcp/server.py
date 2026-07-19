"""Compatibility exports for the historic Python MCP server module.

New code should import :mod:`sketchup_mcp.mcp_server`. The objects below are
aliases, not wrappers, so legacy imports preserve object identity and behavior.
"""

from . import mcp_server as _canonical


__all__ = _canonical.__all__
globals().update({name: getattr(_canonical, name) for name in __all__})


if __name__ == "__main__":
    main()
