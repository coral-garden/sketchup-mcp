# SketchUp MCP - SketchUp Model Context Protocol Integration

SketchupMCP connects Sketchup to Claude AI through the Model Context Protocol (MCP), allowing Claude to directly interact with and control Sketchup. This integration enables prompt-assisted 3D modeling, scene creation, and manipulation in Sketchup.

Big Shoutout to [Blender MCP](https://github.com/ahujasid/blender-mcp) for the inspiration and structure.

## Features

* **Two-way communication**: Connect Claude AI to Sketchup through a TCP socket connection
* **Component manipulation**: Create, modify, delete, and transform components in Sketchup
* **Material control**: Apply and modify materials and colors
* **Scene inspection**: Get detailed information about the current Sketchup scene
* **Selection handling**: Get and manipulate selected components
* **Ruby code evaluation**: Execute arbitrary Ruby code directly in SketchUp for advanced operations

## Runtime Topology

The roles are deliberately distinct:

```text
MCP host
  -> MCP client
  -> Python MCP server over stdio
  -> Python bridge client over loopback TCP
  -> Ruby bridge listener
  -> command executor
  -> SketchUp adapter
  -> SketchUp runtime
```

The canonical Python MCP server module is `sketchup_mcp.mcp_server`. The legacy
`sketchup_mcp.server:mcp` import remains an identity-preserving compatibility
path. Both the `sketchup-mcp` command and `python -m sketchup_mcp` remain
supported launch paths. The historical `python -m sketchup_mcp.server` launch
also remains available for compatibility; new integrations should use the
`sketchup-mcp` command or `python -m sketchup_mcp`. The installed SketchUp
extension runs an `ExtensionRuntime`; it is not an MCP server. The legacy
`su_mcp` loader/package names and `SU_MCP_SERVER` product identifier remain
compatibility identifiers; issue #17 owns any packaging migration.

## Installation

### Python Packaging

We're using uv so you'll need to ```brew install uv```

### Sketchup Extension

1. Download or build the latest `.rbz` file
2. In Sketchup, go to Window > Extension Manager
3. Click "Install Extension" and select the downloaded `.rbz` file
4. Restart Sketchup

## Usage

### Starting the Connection

1. In SketchUp, go to Extensions > SketchUp MCP > Start Bridge
2. The Ruby bridge listener will start on the default port (9876)
3. Make sure the Python MCP server is running in your terminal

### Using with Claude

Configure Claude to use the MCP server by adding the following to your Claude configuration:

```json
    "mcpServers": {
        "sketchup": {
            "command": "uvx",
            "args": [
                "sketchup-mcp"
            ]
        }
    }
```

This will pull the [latest from PyPI](https://pypi.org/project/sketchup-mcp/)

Once connected, Claude can interact with Sketchup using the following capabilities:

#### Tools

<!-- command-catalog:start -->
* `create_component` - Create a grouped primitive in the active SketchUp model.
* `delete_component` - Delete one entity from the active SketchUp model.
* `transform_component` - Move, rotate, or scale an entity in the active SketchUp model.
* `get_selection` - List the entities currently selected in SketchUp.
* `set_material` - Apply a named material or hexadecimal color to an entity.
* `export_scene` - Export the active model to a temporary file.
* `boolean_operation` - Create the union, difference, or intersection of two grouped entities.
* `create_mortise_tenon` - Create a mortise and matching tenon between two boards.
* `create_dovetail` - Create matching dovetail tails and pins between two boards.
* `create_finger_joint` - Create matching fingers and slots between two boards.
* `eval_ruby` - Evaluate trusted local Ruby source in SketchUp's top-level binding. Snippets must not manage SketchUp operations.
<!-- command-catalog:end -->

### Example Commands

Here are some examples of what you can ask Claude to do:

* "Create a simple house model with a roof and windows"
* "Select all components and get their information"
* "Make the selected component red"
* "Move the selected component 10 units up"
* "Export the current scene as a 3D model"
* "Create a complex arts and crafts cabinet using Ruby code"

## Troubleshooting

* **Connection issues**: Make sure the SketchUp bridge and Python MCP server are running
* **Command failures**: Check the Ruby Console in Sketchup for error messages
* **Timeout errors**: Try simplifying your requests or breaking them into smaller steps

## Technical Details

### Communication Protocol

The Python bridge client sends one newline-terminated JSON-RPC `tools/call`
request per loopback connection to the Ruby bridge listener:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_selection","arguments":{}},"id":17}
```

A successful response preserves the request ID and carries a success envelope.
Its text is the exact plain command result serialized as JSON; `resourceId` is
present only for commands whose catalog contract declares one.

```json
{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"{\"entities\":[]}"}],"isError":false,"success":true},"id":17}
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT
