import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


class CommandCatalogTests(unittest.TestCase):
    def test_catalog_is_complete_and_loads_without_runtime_dependencies(self):
        script = """
import json
import socket
import sys

def fail_if_socket_opens(*args, **kwargs):
    raise AssertionError("catalog import opened a socket")

socket.socket = fail_if_socket_opens
from sketchup_mcp.command_catalog import load_command_catalog

catalog = load_command_catalog()
print(json.dumps({
    "names": list(catalog.names),
    "complete": all(
        command.required_arguments is not None
        and command.optional_arguments is not None
        and command.success
        and command.failures
        for command in catalog.commands
    ),
    "mcp_imported": any(name == "mcp" or name.startswith("mcp.") for name in sys.modules),
    "server_imported": "sketchup_mcp.server" in sys.modules,
}))
"""
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SRC_ROOT)

        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            json.loads(completed.stdout),
            {
                "names": [
                    "create_component",
                    "delete_component",
                    "transform_component",
                    "get_selection",
                    "set_material",
                    "export_scene",
                    "boolean_operation",
                    "chamfer_edges",
                    "fillet_edges",
                    "create_mortise_tenon",
                    "create_dovetail",
                    "create_finger_joint",
                    "eval_ruby",
                ],
                "complete": True,
                "mcp_imported": False,
                "server_imported": False,
            },
        )

    def test_catalog_locks_required_and_optional_arguments(self):
        from sketchup_mcp.command_catalog import load_command_catalog

        catalog = load_command_catalog()
        arguments = {
            command.name: (
                tuple(argument.name for argument in command.required_arguments),
                tuple(argument.name for argument in command.optional_arguments),
            )
            for command in catalog.commands
        }

        self.assertEqual(
            arguments,
            {
                "create_component": ((), ("type", "position", "dimensions")),
                "delete_component": (("id",), ()),
                "transform_component": (
                    ("id",),
                    ("position", "rotation", "scale"),
                ),
                "get_selection": ((), ()),
                "set_material": (("id", "material"), ()),
                "export_scene": ((), ("format",)),
                "boolean_operation": (
                    ("operation", "target_id", "tool_id"),
                    ("delete_originals",),
                ),
                "chamfer_edges": (
                    ("entity_id",),
                    ("distance", "edge_indices", "delete_original"),
                ),
                "fillet_edges": (
                    ("entity_id",),
                    ("radius", "segments", "edge_indices", "delete_original"),
                ),
                "create_mortise_tenon": (
                    ("mortise_id", "tenon_id"),
                    (
                        "width",
                        "height",
                        "depth",
                        "offset_x",
                        "offset_y",
                        "offset_z",
                    ),
                ),
                "create_dovetail": (
                    ("tail_id", "pin_id"),
                    (
                        "width",
                        "height",
                        "depth",
                        "angle",
                        "num_tails",
                        "offset_x",
                        "offset_y",
                        "offset_z",
                    ),
                ),
                "create_finger_joint": (
                    ("board1_id", "board2_id"),
                    (
                        "width",
                        "height",
                        "depth",
                        "num_fingers",
                        "offset_x",
                        "offset_y",
                        "offset_z",
                    ),
                ),
                "eval_ruby": (("code",), ()),
            },
        )
        self.assertEqual(
            catalog.renamed_commands,
            {"export": "export_scene", "get_selected_components": "get_selection"},
        )
        self.assertEqual(
            {
                name: semantics["jsonrpc_code"]
                for name, semantics in catalog.failure_semantics.items()
            },
            {"invalid_arguments": -32602, "execution_error": -32603},
        )

    def test_parity_comparison_reports_every_kind_of_name_disagreement(self):
        from sketchup_mcp.command_parity import compare_commands

        report = compare_commands(
            "example",
            {"create_component", "export", "get_scene_info"},
        )

        self.assertEqual(report.consumer, "example")
        self.assertEqual(report.differently_named, {"export": "export_scene"})
        self.assertEqual(report.extra, ("get_scene_info",))
        self.assertEqual(
            report.missing,
            (
                "delete_component",
                "transform_component",
                "get_selection",
                "set_material",
                "boolean_operation",
                "chamfer_edges",
                "fillet_edges",
                "create_mortise_tenon",
                "create_dovetail",
                "create_finger_joint",
                "eval_ruby",
            ),
        )
        self.assertFalse(report.in_sync)

    def test_repository_parity_exposes_each_current_consumer_disagreement(self):
        from sketchup_mcp.command_parity import inspect_repository

        reports = {
            report.consumer: report.as_dict()
            for report in inspect_repository(REPO_ROOT)
        }

        self.assertEqual(
            reports,
            {
                "python_mcp_server": {
                    "consumer": "python_mcp_server",
                    "in_sync": False,
                    "missing": [
                        "boolean_operation",
                        "chamfer_edges",
                        "fillet_edges",
                    ],
                    "extra": [],
                    "differently_named": {},
                },
                "ruby_extension": {
                    "consumer": "ruby_extension",
                    "in_sync": False,
                    "missing": [],
                    "extra": [],
                    "differently_named": {"export": "export_scene"},
                },
                "manifest": {
                    "consumer": "manifest",
                    "in_sync": False,
                    "missing": [
                        "boolean_operation",
                        "chamfer_edges",
                        "fillet_edges",
                        "create_mortise_tenon",
                        "create_dovetail",
                        "create_finger_joint",
                        "eval_ruby",
                    ],
                    "extra": [],
                    "differently_named": {},
                },
                "readme": {
                    "consumer": "readme",
                    "in_sync": False,
                    "missing": [
                        "boolean_operation",
                        "chamfer_edges",
                        "fillet_edges",
                        "create_mortise_tenon",
                        "create_dovetail",
                        "create_finger_joint",
                    ],
                    "extra": ["get_scene_info"],
                    "differently_named": {
                        "get_selected_components": "get_selection"
                    },
                },
            },
        )

    def test_parity_verifier_cli_returns_machine_readable_failure(self):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SRC_ROOT)

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "sketchup_mcp.command_parity",
                "--json",
                str(REPO_ROOT),
            ],
            cwd=REPO_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        report = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 1)
        self.assertFalse(report["in_sync"])
        self.assertEqual(
            [consumer["consumer"] for consumer in report["consumers"]],
            ["python_mcp_server", "ruby_extension", "manifest", "readme"],
        )
        self.assertEqual(report["consumers"][1]["differently_named"], {"export": "export_scene"})
        self.assertEqual(report["consumers"][3]["extra"], ["get_scene_info"])


if __name__ == "__main__":
    unittest.main()
