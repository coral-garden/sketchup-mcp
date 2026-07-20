# MCP client example

`get_selection.py` is a minimal read-only MCP client. It launches the installed
Python MCP server over stdio, initializes an MCP session, and invokes the
authoritative `get_selection` tool.

Complete the extension and Python installation in the root [README](../README.md),
open a model in SketchUp, and choose **Extensions > SketchUp MCP > Start Bridge**.
Then run the example with the same Python environment in which `sketchup-mcp` is
installed:

```sh
python examples/get_selection.py
```

The script prints the raw MCP `CallToolResult` as JSON. Its text content contains
the bridge success envelope; the envelope's text content contains the command
result. With nothing selected, that command result is `{"entities":[]}`.

The example is an MCP host for demonstration purposes. Normal end users configure
their existing MCP host as described in the root README instead of running this
script.
