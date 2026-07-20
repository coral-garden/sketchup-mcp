#!/usr/bin/env python3
"""Read the active SketchUp selection through an official stdio MCP session."""

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sketchup_mcp"],
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool("get_selection", {})

    print(result.model_dump_json(indent=2, by_alias=True))


if __name__ == "__main__":
    asyncio.run(main())
