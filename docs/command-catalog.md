# Public command catalog

`src/sketchup_mcp/command_catalog.json` is the authoritative inventory for the
commands exposed by the Python MCP server and executed by the Ruby SketchUp
extension. It defines every command's required and optional arguments, result
fields, and failure semantics. Runtime code and documentation should consume or
be checked against this catalog instead of maintaining independent inventories.

<!-- command-catalog:start -->
The accepted public command names are:

- `create_component`
- `delete_component`
- `transform_component`
- `get_selection`
- `set_material`
- `export_scene`
- `boolean_operation`
- `create_mortise_tenon`
- `create_dovetail`
- `create_finger_joint`
- `eval_ruby`

Executable compatibility aliases:

- `export` executes `export_scene`.

Migration-only rename guidance:

- `get_selected_components` must become `get_selection` and is not executable.
<!-- command-catalog:end -->

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

Add `--json` for machine-readable output. The command exits zero only when live
FastMCP registration, Ruby command execution reachability, both generated
documentation blocks, and the extension's staged catalog bytes match the
authoritative catalog. The Ruby runtime loads the catalog's explicit executable
`export` compatibility alias separately from the canonical public inventory, so
compatibility does not appear as a second public command.

`chamfer_edges` and `fillet_edges` are deliberately absent. Their former Ruby
implementations could report success without producing valid chamfered or filleted
topology; real topology-aware commands require separate contracts and features.
