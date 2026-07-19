# SketchUp MCP

SketchUp MCP exposes a catalog of modeling commands to an MCP host and carries
each invocation to the SketchUp extension that owns the active model.

## Language

**MCP host**:
The user-facing application that discovers and invokes SketchUp commands through MCP.
_Avoid_: Claude, client, AI server

**MCP client**:
The protocol role within an MCP host that connects to the MCP server and invokes tools.
_Avoid_: MCP host, bridge client

**MCP server**:
The process that publishes the command catalog to an MCP host and delegates command invocations across the bridge.
_Avoid_: Python client, SketchUp server

**SketchUp extension**:
The installed SketchUp package that provides the bridge and modeling integration.
_Avoid_: Ruby server, plugin server

**Extension runtime**:
The active role inside the SketchUp extension that owns bridge availability and coordinates command handling with SketchUp.
_Avoid_: MCP server, Ruby server, command executor

**SketchUp runtime**:
The live SketchUp application environment that owns the active model and user interface.
_Avoid_: extension runtime, SketchUp adapter

**Bridge**:
The private, loopback-only exchange between the MCP server and the SketchUp extension.
_Avoid_: MCP connection, public API

**Bridge client**:
The MCP-server role that initiates one bridge exchange for a command invocation.
_Avoid_: persistent connection, SketchUp connection

**Bridge listener**:
The SketchUp-extension role that accepts bridge exchanges without owning command behavior.
_Avoid_: MCP server, command server

**Tool**:
A capability as an MCP host sees it — the MCP-facing surface only. Every tool
forwards to exactly one public command, under the same name.
_Avoid_: function, endpoint, action

**Public command**:
A named SketchUp operation that an MCP host may discover and invoke.
_Avoid_: tool (past the MCP boundary), operation, Ruby method

**Command catalog**:
The authoritative inventory of public command names, arguments, results, errors,
and compatibility aliases. The single source every consumer is checked against.
_Avoid_: tool contract, tool list, manifest inventory

**Command result**:
The language-neutral data produced by a successful public command before transport wrapping.
_Avoid_: response text, success message

**Catalog consumer**:
A runtime or document whose public command names are checked against the command catalog.
_Avoid_: source of truth, command owner

**Command executor**:
The role that carries out a public command once the bridge listener has handed
it over, independent of which SketchUp adapter it runs against.
_Avoid_: server, handler, dispatcher

**SketchUp adapter**:
The role that translates command-executor calls into operations against the SketchUp runtime.
_Avoid_: bridge listener, SketchUp wrapper

---

Bridge lifecycle, framing, retry, port, and trust-boundary decisions live in
[ADR 0001](./docs/adr/0001-local-one-request-bridge-lifecycle.md), not here.
