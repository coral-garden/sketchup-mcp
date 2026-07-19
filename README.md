# SketchupMCP - Sketchup Model Context Protocol Integration

SketchupMCP connects Sketchup to Claude AI through the Model Context Protocol (MCP), allowing Claude to directly interact with and control Sketchup. This integration enables prompt-assisted 3D modeling, scene creation, and manipulation in Sketchup.

Big Shoutout to [Blender MCP](https://github.com/ahujasid/blender-mcp) for the inspiration and structure.

## Features

* **Two-way communication**: Connect Claude AI to Sketchup through a TCP socket connection
* **Component manipulation**: Create, modify, delete, and transform components in Sketchup
* **Material control**: Apply and modify materials and colors
* **Scene inspection**: Get detailed information about the current Sketchup scene
* **Selection handling**: Get and manipulate selected components
* **Ruby code evaluation**: Execute arbitrary Ruby code directly in SketchUp for advanced operations

## Components

The system consists of two main components:

1. **Sketchup Extension**: A Sketchup extension that creates a TCP server within Sketchup to receive and execute commands
2. **MCP Server (`sketchup_mcp/server.py`)**: A Python server that implements the Model Context Protocol and connects to the Sketchup extension

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

1. In Sketchup, go to Extensions > SketchupMCP > Start Server
2. The server will start on the default port (9876)
3. Make sure the MCP server is running in your terminal

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

* `create_component` - Create a new component with specified parameters
* `delete_component` - Remove a component from the scene
* `transform_component` - Move, rotate, or scale a component
* `get_selection` - Get the entities currently selected in SketchUp
* `set_material` - Apply materials to components
* `export_scene` - Export the current scene to various formats
* `boolean_operation` - Create a union, difference, or intersection of two solid groups
* `create_mortise_tenon` - Create a mortise-and-tenon joint between two boards
* `create_dovetail` - Create a dovetail joint between two boards
* `create_finger_joint` - Create a finger joint between two boards
* `eval_ruby` - Execute arbitrary Ruby code in SketchUp for advanced operations

### Example Commands

Here are some examples of what you can ask Claude to do:

* "Create a simple house model with a roof and windows"
* "Select all components and get their information"
* "Make the selected component red"
* "Move the selected component 10 units up"
* "Export the current scene as a 3D model"
* "Create a complex arts and crafts cabinet using Ruby code"

## Troubleshooting

* **Connection issues**: Make sure both the Sketchup extension server and the MCP server are running
* **Command failures**: Check the Ruby Console in Sketchup for error messages
* **Timeout errors**: Try simplifying your requests or breaking them into smaller steps

## Technical Details

### Communication Protocol

The MCP server sends one newline-terminated JSON-RPC `tools/call` request per
loopback bridge connection:

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
