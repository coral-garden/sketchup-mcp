"""Focused MCP-facing command behavior, independent of FastMCP registration."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .bridge import BridgeClient
from .command_catalog import CommandCatalog, load_command_catalog


logger = logging.getLogger("SketchupMCPServer")


@dataclass(frozen=True)
class CommandForwarder:
    """Carry one public command across the bridge seam."""

    bridge: BridgeClient
    catalog: CommandCatalog = field(default_factory=load_command_catalog)

    def call(
        self,
        command: str,
        arguments: dict[str, Any],
        request_id: Any,
    ) -> str:
        """Return the bridge success envelope serialized as JSON."""

        contract = self.catalog.command(command)
        logger.info("%s called: request_id=%r", command, request_id)
        try:
            result = self.bridge.send_command(
                command,
                arguments,
                request_id=request_id,
            )
        except Exception as error:
            logger.error(
                "%s failed: request_id=%r, error=%s",
                command,
                request_id,
                error,
            )
            return f"Error {contract.failure_action}: {error}"
        logger.info("%s completed: request_id=%r", command, request_id)
        return json.dumps(result)
