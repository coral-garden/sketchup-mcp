"""Focused MCP-facing command behavior, independent of FastMCP registration."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .bridge import BridgeClient


logger = logging.getLogger("SketchupMCPServer")


@dataclass(frozen=True)
class CreateComponentTool:
    """Map create-component inputs and outputs across the SketchUp bridge seam."""

    bridge: BridgeClient

    def create_component(
        self,
        request_id: Any,
        component_type: str = "cube",
        position: list[float] | None = None,
        dimensions: list[float] | None = None,
    ) -> str:
        """Create a SketchUp primitive while preserving the MCP request ID."""

        logger.info(
            "create_component called: request_id=%r",
            request_id,
        )
        try:
            result = self.bridge.send_command(
                "create_component",
                {
                    "type": component_type,
                    "position": position if position is not None else [0, 0, 0],
                    "dimensions": (
                        dimensions if dimensions is not None else [1, 1, 1]
                    ),
                },
                request_id=request_id,
            )
            logger.info("create_component completed: request_id=%r", request_id)
            return json.dumps(result)
        except Exception as error:
            logger.error(
                "create_component failed: request_id=%r, error=%s",
                request_id,
                error,
            )
            return f"Error creating component: {error}"
