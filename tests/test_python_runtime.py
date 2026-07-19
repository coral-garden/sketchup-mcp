import asyncio
import json
from pathlib import Path
import unittest

from sketchup_mcp.bridge import (
    BridgeClient,
    BridgeProtocolError,
    InMemoryBridgeAdapter,
)
from sketchup_mcp.mcp_server import CreateComponentTool


CONTRACT = json.loads(
    (Path(__file__).parents[1] / "test/fixtures/create_component_contract.json").read_text(
        encoding="utf-8"
    )
)


class PythonRuntimeTest(unittest.TestCase):
    def test_create_component_succeeds_through_the_bridge_seam(self):
        adapter = InMemoryBridgeAdapter.returning(CONTRACT["success_result"])
        tool = CreateComponentTool(BridgeClient(adapter=adapter))

        result = tool.create_component(
            request_id="mcp-create-17",
            component_type="cube",
            position=[1, 2, 3],
            dimensions=[4, 5, 6],
        )

        self.assertEqual(json.dumps(CONTRACT["success_result"]), result)
        self.assertEqual(
            [
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "create_component",
                        "arguments": {
                            "type": "cube",
                            "position": [1, 2, 3],
                            "dimensions": [4, 5, 6],
                        },
                    },
                    "id": "mcp-create-17",
                }
            ],
            adapter.requests,
        )

    def test_create_component_expands_defaults_and_preserves_every_id_shape(self):
        for request_id in CONTRACT["success_ids"]:
            with self.subTest(request_id=request_id):
                adapter = InMemoryBridgeAdapter.returning(CONTRACT["success_result"])
                tool = CreateComponentTool(BridgeClient(adapter=adapter))

                tool.create_component(request_id=request_id)

                self.assertEqual(
                    {
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {
                            "name": "create_component",
                            "arguments": CONTRACT["defaults"],
                        },
                        "id": request_id,
                    },
                    adapter.requests[0],
                )

    def test_create_component_reports_catalogued_remote_failures_without_retrying(self):
        for fixture_name in ("invalid_type", "execution_error"):
            fixture = CONTRACT[fixture_name]
            with self.subTest(fixture_name=fixture_name):
                adapter = InMemoryBridgeAdapter(
                    lambda request, error=fixture["error"]: {
                        "jsonrpc": "2.0",
                        "error": error,
                        "id": request["id"],
                    }
                )
                tool = CreateComponentTool(BridgeClient(adapter=adapter))

                result = tool.create_component(
                    request_id=fixture["request_id"],
                    component_type=fixture.get("arguments", CONTRACT["defaults"])[
                        "type"
                    ],
                )

                self.assertEqual(
                    "Error creating component: "
                    f"SketchUp bridge error {fixture['error']['code']}: "
                    f"{fixture['error']['message']}",
                    result,
                )
                self.assertEqual(1, len(adapter.requests))

    def test_mismatched_response_id_is_a_final_protocol_error(self):
        fixture = CONTRACT["mismatched_id"]
        adapter = InMemoryBridgeAdapter(
            lambda _request: {
                "jsonrpc": "2.0",
                "result": CONTRACT["success_result"],
                "id": fixture["response_id"],
            }
        )
        client = BridgeClient(adapter=adapter)

        with self.assertRaisesRegex(
            BridgeProtocolError,
            "response id 'different-component' does not match request id "
            "'expected-component'",
        ):
            client.send_command(
                "create_component",
                CONTRACT["defaults"],
                request_id=fixture["request_id"],
            )

        self.assertEqual(1, len(adapter.requests))

    def test_null_request_id_still_requires_an_explicit_response_id(self):
        adapter = InMemoryBridgeAdapter(
            lambda _request: {
                "jsonrpc": "2.0",
                "result": CONTRACT["success_result"],
            }
        )
        client = BridgeClient(adapter=adapter)

        with self.assertRaisesRegex(
            BridgeProtocolError,
            "JSON-RPC response must contain an id",
        ):
            client.send_command(
                "create_component",
                CONTRACT["defaults"],
                request_id=None,
            )

    def test_create_component_reports_bounded_transport_failure(self):
        fixture = CONTRACT["unavailable"]

        def unavailable(_request):
            raise OSError("listener offline")

        adapter = InMemoryBridgeAdapter(unavailable)
        tool = CreateComponentTool(
            BridgeClient(adapter=adapter, max_attempts=fixture["attempts"])
        )

        result = tool.create_component(request_id=fixture["request_id"])

        self.assertEqual(
            "Error creating component: SketchUp bridge unavailable at "
            "127.0.0.1:9876 after 3 attempts: listener offline",
            result,
        )
        self.assertEqual(
            [fixture["request_id"]] * fixture["attempts"],
            [request["id"] for request in adapter.requests],
        )

    def test_bridge_rejects_commands_outside_the_authoritative_catalog(self):
        adapter = InMemoryBridgeAdapter.returning({})
        client = BridgeClient(adapter=adapter)

        with self.assertRaisesRegex(
            ValueError,
            "Unknown SketchUp command in command catalog: get_scene_info",
        ):
            client.send_command("get_scene_info", request_id="unknown-command")

        self.assertEqual([], adapter.requests)

    def test_direct_and_legacy_prewrapped_calls_share_one_request_shape(self):
        direct_adapter = InMemoryBridgeAdapter.returning(CONTRACT["success_result"])
        wrapped_adapter = InMemoryBridgeAdapter.returning(CONTRACT["success_result"])

        BridgeClient(adapter=direct_adapter).send_command(
            "create_component",
            CONTRACT["defaults"],
            request_id="compatibility",
        )
        BridgeClient(adapter=wrapped_adapter).send_command(
            "tools/call",
            {
                "name": "create_component",
                "arguments": CONTRACT["defaults"],
            },
            request_id="compatibility",
        )

        self.assertEqual(direct_adapter.requests, wrapped_adapter.requests)

    def test_create_component_logs_metadata_without_raw_arguments(self):
        adapter = InMemoryBridgeAdapter.returning(CONTRACT["success_result"])
        tool = CreateComponentTool(BridgeClient(adapter=adapter))

        with self.assertLogs("SketchupMCPServer", level="INFO") as captured:
            tool.create_component(
                request_id="safe-log-id",
                component_type="private-component-input",
                position=[101, 202, 303],
                dimensions=[404, 505, 606],
            )

        logs = "\n".join(captured.output)
        self.assertIn("create_component", logs)
        self.assertIn("safe-log-id", logs)
        self.assertNotIn("private-component-input", logs)
        self.assertNotIn("101", logs)
        self.assertNotIn("404", logs)

    def test_empty_vectors_are_not_replaced_by_defaults(self):
        adapter = InMemoryBridgeAdapter.returning(CONTRACT["success_result"])
        tool = CreateComponentTool(BridgeClient(adapter=adapter))

        tool.create_component(
            request_id="empty-vectors",
            position=[],
            dimensions=[],
        )

        self.assertEqual(
            {"type": "cube", "position": [], "dimensions": []},
            adapter.requests[0]["params"]["arguments"],
        )

    def test_exported_fastmcp_create_component_succeeds_through_in_memory_adapter(self):
        from sketchup_mcp import server as exported_server

        adapter = InMemoryBridgeAdapter.returning(CONTRACT["success_result"])
        context = type("RequestContext", (), {"request_id": "exported-success"})()
        with exported_server.use_bridge_client(BridgeClient(adapter=adapter)):
            result = exported_server.create_component(context)

        self.assertEqual(json.dumps(CONTRACT["success_result"]), result)

    def test_exported_fastmcp_create_component_reports_remote_failure(self):
        from sketchup_mcp import server as exported_server

        fixture = CONTRACT["execution_error"]
        adapter = InMemoryBridgeAdapter(
            lambda request: {
                "jsonrpc": "2.0",
                "error": fixture["error"],
                "id": request["id"],
            }
        )
        context = type("RequestContext", (), {"request_id": fixture["request_id"]})()
        with exported_server.use_bridge_client(BridgeClient(adapter=adapter)):
            result = exported_server.create_component(context)

        self.assertEqual(
            "Error creating component: SketchUp bridge error -32603: "
            "SketchUp could not create component",
            result,
        )

    def test_fastmcp_and_console_entrypoints_remain_stable(self):
        from sketchup_mcp import server as exported_server

        project = (Path(__file__).parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )

        tool_names = [
            tool.name for tool in asyncio.run(exported_server.mcp.list_tools())
        ]
        self.assertIn("create_component", tool_names)
        self.assertIn('sketchup-mcp = "sketchup_mcp.server:main"', project)
        self.assertIn('sketchup = "sketchup_mcp.server:mcp"', project)


if __name__ == "__main__":
    unittest.main()
