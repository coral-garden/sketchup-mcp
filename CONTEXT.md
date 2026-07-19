# SketchUp MCP

SketchUp MCP exposes a catalog of modeling commands to an MCP host and carries
each invocation to the SketchUp extension that owns the active model.

## Language

**MCP host**:
The user-facing application that discovers and invokes SketchUp commands through MCP.
_Avoid_: Claude, client, AI server

**MCP server**:
The process that publishes the command catalog to an MCP host and delegates command invocations across the bridge.
_Avoid_: Python client, SketchUp server

**SketchUp extension**:
The code running inside SketchUp that receives command invocations and applies them to the active model.
_Avoid_: Ruby server, plugin server

**Bridge**:
The private, loopback-only exchange between the MCP server and the SketchUp extension.
_Avoid_: MCP connection, public API

**Bridge client**:
The MCP-server role that initiates one bridge exchange for a command invocation.
_Avoid_: persistent connection, SketchUp connection

**Bridge listener**:
The SketchUp-extension role that accepts bridge exchanges without owning command behavior.
_Avoid_: MCP server, command server

**Bridge frame**:
One newline-terminated JSON-RPC message carried across the bridge.
_Avoid_: packet, raw request

**Public command**:
A named SketchUp operation that an MCP host may discover and invoke.
_Avoid_: tool implementation, Ruby method

**Command catalog**:
The authoritative contract for public command names, arguments, results, errors, and compatibility aliases.
_Avoid_: tool list, manifest inventory

**Command result**:
The language-neutral data produced by a successful public command before transport wrapping.
_Avoid_: response text, success message

**Success envelope**:
The JSON-RPC result shape that carries a command result back across the bridge.
_Avoid_: command result, MCP response

**Catalog consumer**:
A runtime or document whose public command names are checked against the command catalog.
_Avoid_: source of truth, command owner

**SketchUp adapter**:
The role that applies validated command behavior to the active SketchUp model.
_Avoid_: command executor, bridge listener
