# Public command catalog

`src/sketchup_mcp/command_catalog.json` is the authoritative inventory for the
commands exposed by the Python MCP server and executed by the Ruby SketchUp
extension. It defines every command's required and optional arguments, result
fields, and failure semantics. Runtime code and documentation should consume or
be checked against this catalog instead of maintaining independent inventories.

The accepted public command names are:

- `create_component`
- `delete_component`
- `transform_component`
- `get_selection`
- `set_material`
- `export_scene`
- `boolean_operation`
- `chamfer_edges`
- `fillet_edges`
- `create_mortise_tenon`
- `create_dovetail`
- `create_finger_joint`
- `eval_ruby`

Two names are recognized only when reporting migration work:

- `export` must become `export_scene`.
- `get_selected_components` must become `get_selection`.

`get_scene_info` is not an accepted command and has no canonical replacement.

Python consumers can load the typed, immutable view without importing FastMCP or
opening a SketchUp connection:

```python
from sketchup_mcp.command_catalog import load_command_catalog

catalog = load_command_catalog()
```

## Parity check

From the repository root, run:

```sh
PYTHONPATH=src python -m sketchup_mcp.command_parity .
```

Add `--json` for machine-readable output. The command exits zero only when the
Python MCP server, Ruby extension, `sketchup.json`, and README all use the
canonical names. It currently exits one by design: later migration tickets use
the report as their checklist and make each consumer converge independently.
