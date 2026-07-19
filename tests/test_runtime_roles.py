import importlib
import json
import runpy
import sys
import unittest
from unittest import mock

from sketchup_mcp.bridge import BridgeClient, InMemoryBridgeAdapter
from sketchup_mcp.command_catalog import load_command_catalog


class RuntimeRolesTest(unittest.TestCase):
    def test_legacy_python_server_is_an_identity_preserving_compatibility_module(self):
        canonical = importlib.import_module("sketchup_mcp.mcp_server")
        legacy = importlib.import_module("sketchup_mcp.server")
        runtime_exports = {
            "__version__",
            "mcp",
            "main",
            "server_lifespan",
            "get_bridge_client",
            "use_bridge_client",
            "forward_command",
        }
        expected_exports = runtime_exports | set(load_command_catalog().names)

        self.assertEqual("SketchUp MCP", canonical.mcp.name)
        self.assertEqual(expected_exports, set(legacy.__all__))
        for name in expected_exports:
            with self.subTest(name=name):
                self.assertIs(getattr(canonical, name), getattr(legacy, name))
        for name in expected_exports - {"mcp"}:
            with self.subTest(owner=name):
                exported = getattr(canonical, name)
                if callable(exported):
                    self.assertEqual(canonical.__name__, exported.__module__)

    def test_legacy_python_server_module_execution_runs_the_canonical_main(self):
        canonical = importlib.import_module("sketchup_mcp.mcp_server")
        legacy = sys.modules.pop("sketchup_mcp.server", None)
        try:
            with mock.patch.object(canonical, "main") as canonical_main:
                runpy.run_module("sketchup_mcp.server", run_name="__main__")
        finally:
            if legacy is not None:
                sys.modules["sketchup_mcp.server"] = legacy

        canonical_main.assert_called_once_with()

    def test_python_role_logs_redact_bridge_payloads_and_eval_source(self):
        canonical = importlib.import_module("sketchup_mcp.mcp_server")
        adapter = InMemoryBridgeAdapter.returning(
            {
                "content": [{"type": "text", "text": json.dumps({"result": 2})}],
                "isError": False,
                "success": True,
            }
        )
        context = type("RequestContext", (), {"request_id": "role-log-17"})()
        secret_source = "raise 'RAW_EVAL_SOURCE_MUST_NOT_BE_LOGGED'"

        with self.assertLogs("SketchUpMCP", level="INFO") as captured:
            with canonical.use_bridge_client(BridgeClient(adapter=adapter)):
                canonical.eval_ruby(context, secret_source)
            remote_error = InMemoryBridgeAdapter(
                lambda request: {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32603,
                        "message": "RAW_REMOTE_EVAL_FAILURE",
                    },
                    "id": request["id"],
                }
            )
            with canonical.use_bridge_client(BridgeClient(adapter=remote_error)):
                canonical.eval_ruby(context, secret_source)

        logs = "\n".join(captured.output)
        self.assertIn("SketchUpMCP.MCPServer", logs)
        self.assertIn("SketchUpMCP.BridgeClient", logs)
        self.assertIn("MCP server tool", logs)
        self.assertIn("role-log-17", logs)
        self.assertNotIn(secret_source, logs)
        self.assertNotIn("RAW_EVAL_SOURCE", logs)
        self.assertNotIn("RAW_REMOTE_EVAL_FAILURE", logs)


if __name__ == "__main__":
    unittest.main()
