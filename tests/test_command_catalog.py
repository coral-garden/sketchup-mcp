import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


class CommandCatalogTests(unittest.TestCase):
    def test_catalog_fastmcp_needs_no_second_command_inventory(self):
        from sketchup_mcp.catalog_fastmcp import CatalogFastMCP

        server = CatalogFastMCP("Catalog contract")

        self.assertEqual([], asyncio.run(server.list_tools()))

    def test_argument_validation_supports_scalar_positive_constraints(self):
        from sketchup_mcp.command_catalog import (
            ArgumentContract,
            CommandCatalog,
            CommandContract,
            InvalidCommandArguments,
            manifest_tools,
            validate_command_arguments,
        )

        catalog = CommandCatalog(
            schema_version=1,
            commands=(
                CommandContract(
                    name="positive_number",
                    description="A future catalog constraint fixture.",
                    failure_action="processing a positive number",
                    required_arguments=(
                        ArgumentContract(
                            name="amount",
                            type="number",
                            description="A positive amount.",
                            constraints={"positive": True},
                        ),
                    ),
                    optional_arguments=(),
                    success={},
                    failures=(),
                ),
            ),
            renamed_commands={},
            executable_aliases={},
            success_envelope={},
            failure_semantics={},
        )

        validate_command_arguments("positive_number", {"amount": 0.5}, catalog)
        with self.assertRaisesRegex(InvalidCommandArguments, "must be positive"):
            validate_command_arguments("positive_number", {"amount": 0}, catalog)
        self.assertEqual(
            0,
            manifest_tools(catalog)[0]["parameters"]["properties"]["amount"][
                "exclusiveMinimum"
            ],
        )

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
                and command.failure_action
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
        self.assertEqual({"export": "export_scene"}, catalog.executable_aliases)
        self.assertEqual(
            {
                name: semantics["jsonrpc_code"]
                for name, semantics in catalog.failure_semantics.items()
            },
            {"invalid_arguments": -32602, "execution_error": -32603},
        )
        eval_description = catalog.command("eval_ruby").description
        self.assertIn("trusted local Ruby", eval_description)
        self.assertIn("must not manage SketchUp operations", eval_description)

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
                "create_mortise_tenon",
                "create_dovetail",
                "create_finger_joint",
                "eval_ruby",
            ),
        )
        self.assertFalse(report.in_sync)

    @unittest.skipIf(
        os.environ.get("SKETCHUP_MCP_DETERMINISTIC_TESTS") == "1",
        "live Ruby consumer parity is outside the coverage target",
    )
    def test_repository_parity_classifies_each_consumer_report(self):
        from sketchup_mcp.command_parity import inspect_repository

        reports = inspect_repository(REPO_ROOT)

        self.assertEqual(
            [report.consumer for report in reports],
            [
                "fastmcp_registration",
                "ruby_execution",
                "readme",
                "command_docs",
                "package_catalog",
            ],
        )
        for report in reports:
            self.assertEqual(
                set(report.as_dict()),
                {"consumer", "in_sync", "missing", "extra", "differently_named"},
            )
            self.assertEqual((), report.missing, report.as_dict())
            self.assertEqual((), report.extra, report.as_dict())
        for report in reports:
            self.assertTrue(report.in_sync, report.as_dict())

    def test_command_documents_are_generated_and_the_obsolete_manifest_is_gone(self):
        from sketchup_mcp.command_docs import check_documents, write_documents

        self.assertFalse((REPO_ROOT / "sketchup.json").exists())
        self.assertTrue(check_documents(REPO_ROOT))

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            (fixture / "docs").mkdir()
            shutil.copy(REPO_ROOT / "README.md", fixture / "README.md")
            shutil.copy(
                REPO_ROOT / "docs/command-catalog.md",
                fixture / "docs/command-catalog.md",
            )
            readme = fixture / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8").replace(
                    "`create_component`", "`stale_component`", 1
                ),
                encoding="utf-8",
            )

            self.assertFalse(check_documents(fixture))
            write_documents(fixture)
            self.assertTrue(check_documents(fixture))

    @unittest.skipIf(
        os.environ.get("SKETCHUP_MCP_DETERMINISTIC_TESTS") == "1",
        "live Ruby consumer parity is outside the coverage target",
    )
    def test_parity_verifier_cli_returns_machine_readable_failure(self):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SRC_ROOT)

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            shutil.copytree(REPO_ROOT / "src", fixture / "src")
            shutil.copytree(REPO_ROOT / "su_mcp", fixture / "su_mcp")
            shutil.copytree(REPO_ROOT / "scripts", fixture / "scripts")
            shutil.copytree(REPO_ROOT / "docs", fixture / "docs")
            shutil.copy(REPO_ROOT / "README.md", fixture / "README.md")
            shutil.copy(REPO_ROOT / "VERSION", fixture / "VERSION")
            shutil.copy(REPO_ROOT / "su_mcp.rb", fixture / "su_mcp.rb")

            mcp_server = fixture / "src/sketchup_mcp/mcp_server.py"
            mcp_server.write_text(
                mcp_server.read_text(encoding="utf-8").replace(
                    "@mcp.tool()\ndef eval_ruby(", "def eval_ruby(", 1
                ),
                encoding="utf-8",
            )
            adapter = fixture / "su_mcp/sketchup_adapter.rb"
            adapter.write_text(
                adapter.read_text(encoding="utf-8").replace(
                    "def eval_ruby(code:)", "def unavailable_eval_ruby(code:)", 1
                ),
                encoding="utf-8",
            )
            readme = fixture / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8").replace(
                    "`eval_ruby`", "`stale_eval_ruby`", 1
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sketchup_mcp.command_parity",
                    "--json",
                    str(fixture),
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
            [
                "fastmcp_registration",
                "ruby_execution",
                "readme",
                "command_docs",
                "package_catalog",
            ],
        )
        self.assertEqual(
            ["eval_ruby"], report["consumers"][0]["missing"]
        )
        self.assertEqual(
            ["eval_ruby"], report["consumers"][1]["missing"]
        )
        self.assertIn("eval_ruby", report["consumers"][2]["missing"])


if __name__ == "__main__":
    unittest.main()
