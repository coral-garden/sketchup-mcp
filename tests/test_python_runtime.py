import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from sketchup_mcp.bridge import (
    BridgeClient,
    BridgeProtocolError,
    InMemoryBridgeAdapter,
)
from sketchup_mcp.mcp_server import CommandForwarder
from sketchup_mcp.command_catalog import load_command_catalog, manifest_tools


CONTRACT = json.loads(
    (Path(__file__).parents[1] / "test/fixtures/create_component_contract.json").read_text(
        encoding="utf-8"
    )
)
SCENE_GEOMETRY_CONTRACT = json.loads(
    (Path(__file__).parents[1] / "test/fixtures/scene_geometry_contract.json").read_text(
        encoding="utf-8"
    )
)
JOINERY_EVAL_CONTRACT = json.loads(
    (Path(__file__).parents[1] / "test/fixtures/joinery_eval_contract.json").read_text(
        encoding="utf-8"
    )
)


def call_create_component(client, request_id, **arguments):
    from sketchup_mcp import server

    context = type("RequestContext", (), {"request_id": request_id})()
    with server.use_bridge_client(client):
        return server.create_component(context, **arguments)


class PythonRuntimeTest(unittest.TestCase):
    def test_stdio_mcp_entrypoint_executes_through_the_real_ruby_bridge(self):
        commands = (
            SCENE_GEOMETRY_CONTRACT["commands"]
            + JOINERY_EVAL_CONTRACT["commands"]
        )
        fixture = (
            Path(__file__).parents[1]
            / "test/fixtures/ruby_command_contract_fixture.rb"
        )
        process = subprocess.Popen(
            ["ruby", str(fixture)],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        communicated = False
        try:
            ready_frame = process.stdout.readline()
            if not ready_frame:
                _stdout, stderr = process.communicate(timeout=2)
                communicated = True
                self.fail(f"Ruby command fixture failed to start: {stderr}")
            port = json.loads(ready_frame)["port"]

            async def call_all_tools():
                repo_root = Path(__file__).parents[1]
                environment = {
                    **os.environ,
                    "PYTHONPATH": str(repo_root / "src"),
                    "SKETCHUP_MCP_BRIDGE_PORT": str(port),
                }
                parameters = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "sketchup_mcp"],
                    env=environment,
                    cwd=repo_root,
                )
                with tempfile.TemporaryFile(
                    mode="w+", encoding="utf-8"
                ) as mcp_stderr:
                    async with stdio_client(
                        parameters, errlog=mcp_stderr
                    ) as streams:
                        async with ClientSession(*streams) as session:
                            await session.initialize()
                            results = []
                            for command in commands:
                                arguments = command["arguments"]
                                if command["name"] == "eval_ruby":
                                    arguments = {
                                        "code": "'RAW_STDIO_EVAL_SOURCE'"
                                    }
                                results.append(
                                    await session.call_tool(
                                        command["name"], arguments
                                    )
                                )
                    mcp_stderr.seek(0)
                    return results, mcp_stderr.read()

            results, mcp_logs = asyncio.run(call_all_tools())
            _stdout, stderr = process.communicate(input="done\n", timeout=2)
            communicated = True
            self.assertEqual(0, process.returncode, stderr)
        finally:
            if not communicated:
                process.terminate()
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=2)

        for command, result in zip(commands, results, strict=True):
            self.assertFalse(result.isError, command["name"])
            self.assertEqual(
                json.dumps(command["wire_result"]), result.content[0].text
            )
        self.assertIn("MCP server starting", mcp_logs)
        self.assertIn("Bridge client exchange", mcp_logs)
        self.assertNotIn("RAW_STDIO_EVAL_SOURCE", mcp_logs)
        self.assertIn("Bridge listener:", stderr)
        self.assertIn("Command executor:", stderr)
        self.assertNotIn("RAW_STDIO_EVAL_SOURCE", stderr)

    def test_all_eleven_catalog_commands_register_and_reach_the_bridge(self):
        from mcp.shared.memory import create_connected_server_and_client_session
        from sketchup_mcp import server as exported_server

        commands = (
            SCENE_GEOMETRY_CONTRACT["commands"]
            + JOINERY_EVAL_CONTRACT["commands"]
        )
        by_name = {command["name"]: command for command in commands}
        catalog = load_command_catalog()
        self.assertEqual(set(catalog.names), set(by_name))
        registered = {
            tool.name: tool for tool in asyncio.run(exported_server.mcp.list_tools())
        }
        self.assertEqual(set(catalog.names), set(registered))
        self.assertEqual(
            {command.name: command.description for command in catalog.commands},
            {name: tool.description for name, tool in registered.items()},
        )
        adapter = InMemoryBridgeAdapter(
            lambda request: {
                "jsonrpc": "2.0",
                "result": by_name[request["params"]["name"]]["wire_result"],
                "id": request["id"],
            }
        )

        async def call_all_tools():
            with exported_server.use_bridge_client(BridgeClient(adapter=adapter)):
                async with create_connected_server_and_client_session(
                    exported_server.mcp
                ) as session:
                    return [
                        await session.call_tool(
                            command["name"], command["arguments"]
                        )
                        for command in commands
                    ]

        results = asyncio.run(call_all_tools())

        for command, result in zip(commands, results, strict=True):
            self.assertFalse(result.isError, command["name"])
            self.assertEqual(
                json.dumps(command["wire_result"]), result.content[0].text
            )
        self.assertEqual(
            [command["name"] for command in commands],
            [request["params"]["name"] for request in adapter.requests],
        )

    def test_every_command_uses_the_catalogued_forwarding_error_path(self):
        catalog = load_command_catalog()

        for command in catalog.commands:
            with self.subTest(command=command.name):
                adapter = InMemoryBridgeAdapter(
                    lambda request: {
                        "jsonrpc": "2.0",
                        "error": {
                            "code": -32603,
                            "message": "stable command failure",
                            "data": {"success": False},
                        },
                        "id": request["id"],
                    }
                )

                result = CommandForwarder(BridgeClient(adapter=adapter)).call(
                    command.name,
                    {},
                    request_id=f"failure-{command.name}",
                )

                self.assertEqual(
                    f"Error {command.failure_action}: "
                    "SketchUp bridge error -32603: stable command failure",
                    result,
                )

    def test_joinery_and_eval_commands_share_the_catalogued_forwarding_interface(self):
        for command in JOINERY_EVAL_CONTRACT["commands"]:
            with self.subTest(command=command["name"]):
                adapter = InMemoryBridgeAdapter.returning(command["wire_result"])
                forwarder = CommandForwarder(BridgeClient(adapter=adapter))

                result = forwarder.call(
                    command["name"],
                    command["arguments"],
                    request_id=command["request_id"],
                )

                self.assertEqual(json.dumps(command["wire_result"]), result)
                self.assertEqual(command["request_id"], adapter.requests[0]["id"])
                self.assertEqual(
                    {"name": command["name"], "arguments": command["arguments"]},
                    adapter.requests[0]["params"],
                )

    def test_fastmcp_joinery_eval_schemas_and_invalid_inputs_come_from_the_catalog(self):
        from mcp.shared.memory import create_connected_server_and_client_session
        from sketchup_mcp import server as exported_server

        names = {command["name"] for command in JOINERY_EVAL_CONTRACT["commands"]}
        expected = {
            tool["name"]: tool["parameters"]
            for tool in manifest_tools()
            if tool["name"] in names
        }
        actual = {
            tool.name: tool.inputSchema
            for tool in asyncio.run(exported_server.mcp.list_tools())
            if tool.name in names
        }
        self.assertEqual(expected, actual)
        self.assertEqual(
            0,
            actual["create_mortise_tenon"]["properties"]["width"]["exclusiveMinimum"],
        )
        self.assertEqual(
            90,
            actual["create_dovetail"]["properties"]["angle"]["exclusiveMaximum"],
        )

        invalid_arguments = JOINERY_EVAL_CONTRACT["invalid_arguments"] + [
            {
                "name": "create_dovetail",
                "arguments": {"tail_id": 1},
                "contains": "pin_id",
            },
            {
                "name": "create_finger_joint",
                "arguments": {
                    "board1_id": 1,
                    "board2_id": 2,
                    "offset_z": float("inf"),
                },
                "contains": "offset_z",
            },
        ]
        adapter = InMemoryBridgeAdapter.returning({})

        async def call_invalid_tools():
            with exported_server.use_bridge_client(BridgeClient(adapter=adapter)):
                async with create_connected_server_and_client_session(
                    exported_server.mcp
                ) as session:
                    return [
                        await session.call_tool(case["name"], case["arguments"])
                        for case in invalid_arguments
                    ]

        results = asyncio.run(call_invalid_tools())
        for case, result in zip(invalid_arguments, results, strict=True):
            self.assertTrue(result.isError, case)
            self.assertIn(case["contains"], result.content[0].text)
        self.assertEqual([], adapter.requests)

    def test_public_joinery_eval_tools_forward_valid_calls_and_preserve_errors(self):
        from sketchup_mcp import server as exported_server

        invocations = {
            "create_mortise_tenon": lambda context, arguments: exported_server.create_mortise_tenon(
                context, **arguments
            ),
            "create_dovetail": lambda context, arguments: exported_server.create_dovetail(
                context, **arguments
            ),
            "create_finger_joint": lambda context, arguments: exported_server.create_finger_joint(
                context, **arguments
            ),
            "eval_ruby": lambda context, arguments: exported_server.eval_ruby(
                context, **arguments
            ),
        }
        actions = {
            "create_mortise_tenon": "creating mortise and tenon joint",
            "create_dovetail": "creating dovetail joint",
            "create_finger_joint": "creating finger joint",
            "eval_ruby": "evaluating Ruby",
        }

        for command in JOINERY_EVAL_CONTRACT["commands"]:
            context = type(
                "RequestContext", (), {"request_id": command["request_id"]}
            )()
            adapter = InMemoryBridgeAdapter.returning(command["wire_result"])
            with exported_server.use_bridge_client(BridgeClient(adapter=adapter)):
                result = invocations[command["name"]](context, command["arguments"])
            self.assertEqual(json.dumps(command["wire_result"]), result)
            self.assertEqual(
                command["arguments"], adapter.requests[0]["params"]["arguments"]
            )

            remote = InMemoryBridgeAdapter(
                lambda request: {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32603,
                        "message": "stable command failure",
                        "data": {"success": False, "secret": "must-not-render"},
                    },
                    "id": request["id"],
                }
            )
            with exported_server.use_bridge_client(BridgeClient(adapter=remote)):
                failure = invocations[command["name"]](context, command["arguments"])
            self.assertEqual(
                f"Error {actions[command['name']]}: "
                "SketchUp bridge error -32603: stable command failure",
                failure,
            )
            self.assertNotIn("must-not-render", failure)

    def test_connected_fastmcp_joinery_eval_calls_reach_the_bridge(self):
        from mcp.shared.memory import create_connected_server_and_client_session
        from sketchup_mcp import server as exported_server

        by_name = {
            command["name"]: command for command in JOINERY_EVAL_CONTRACT["commands"]
        }
        adapter = InMemoryBridgeAdapter(
            lambda request: {
                "jsonrpc": "2.0",
                "result": by_name[request["params"]["name"]]["wire_result"],
                "id": request["id"],
            }
        )

        async def call_tools():
            with exported_server.use_bridge_client(BridgeClient(adapter=adapter)):
                async with create_connected_server_and_client_session(
                    exported_server.mcp
                ) as session:
                    return [
                        await session.call_tool(
                            command["name"], command["arguments"]
                        )
                        for command in JOINERY_EVAL_CONTRACT["commands"]
                    ]

        results = asyncio.run(call_tools())
        for command, result in zip(
            JOINERY_EVAL_CONTRACT["commands"], results, strict=True
        ):
            self.assertFalse(result.isError)
            self.assertEqual(
                json.dumps(command["wire_result"]), result.content[0].text
            )
        self.assertEqual(
            [command["arguments"] for command in JOINERY_EVAL_CONTRACT["commands"]],
            [request["params"]["arguments"] for request in adapter.requests],
        )

    def test_connected_fastmcp_joinery_calls_expand_catalog_defaults(self):
        from mcp.shared.memory import create_connected_server_and_client_session
        from sketchup_mcp import server as exported_server

        required = {
            "create_mortise_tenon": {"mortise_id": 1, "tenon_id": 2},
            "create_dovetail": {"tail_id": 3, "pin_id": 4},
            "create_finger_joint": {"board1_id": 5, "board2_id": 6},
        }
        adapter = InMemoryBridgeAdapter(
            lambda request: {
                "jsonrpc": "2.0",
                "result": {"content": [], "isError": False, "success": True},
                "id": request["id"],
            }
        )

        async def call_tools():
            with exported_server.use_bridge_client(BridgeClient(adapter=adapter)):
                async with create_connected_server_and_client_session(
                    exported_server.mcp
                ) as session:
                    for name, arguments in required.items():
                        await session.call_tool(name, arguments)

        asyncio.run(call_tools())
        expected = [
            required[name] | JOINERY_EVAL_CONTRACT["defaults"][name]
            for name in required
        ]
        self.assertEqual(
            expected,
            [request["params"]["arguments"] for request in adapter.requests],
        )

    def test_valid_fastmcp_create_component_call_reaches_the_bridge_unchanged(self):
        from mcp.shared.memory import create_connected_server_and_client_session
        from sketchup_mcp import server as exported_server

        command = SCENE_GEOMETRY_CONTRACT["commands"][0]
        adapter = InMemoryBridgeAdapter.returning(command["wire_result"])

        async def call_create_component():
            with exported_server.use_bridge_client(BridgeClient(adapter=adapter)):
                async with create_connected_server_and_client_session(
                    exported_server.mcp
                ) as session:
                    return await session.call_tool(
                        command["name"], command["arguments"]
                    )

        result = asyncio.run(call_create_component())

        self.assertFalse(result.isError)
        self.assertEqual(json.dumps(command["wire_result"]), result.content[0].text)
        self.assertEqual(1, len(adapter.requests))
        self.assertEqual("tools/call", adapter.requests[0]["method"])
        self.assertEqual(
            {
                "name": "create_component",
                "arguments": command["arguments"],
            },
            adapter.requests[0]["params"],
        )

    def test_fastmcp_scene_geometry_schemas_match_the_command_catalog(self):
        from sketchup_mcp import server as exported_server

        command_names = {
            command["name"] for command in SCENE_GEOMETRY_CONTRACT["commands"]
        }
        expected = {
            tool["name"]: tool["parameters"]
            for tool in manifest_tools()
            if tool["name"] in command_names
        }
        actual = {
            tool.name: tool.inputSchema
            for tool in asyncio.run(exported_server.mcp.list_tools())
            if tool.name in command_names
        }

        self.assertEqual(expected, actual)
        self.assertEqual(
            ["cube", "cylinder", "sphere", "cone"],
            actual["create_component"]["properties"]["type"]["enum"],
        )
        self.assertEqual(
            {
                "type": "array",
                "items": {"type": "number", "exclusiveMinimum": 0},
                "minItems": 3,
                "maxItems": 3,
                "description": "Width, depth, and height in model units.",
                "default": [1, 1, 1],
            },
            actual["create_component"]["properties"]["dimensions"],
        )
        self.assertEqual(
            {
                "anyOf": [
                    {"type": "integer", "minimum": 1},
                    {
                        "type": "string",
                        "pattern": "^[1-9][0-9]*$",
                    },
                ],
                "description": "SketchUp entity ID to delete.",
            },
            actual["delete_component"]["properties"]["id"],
        )
        self.assertTrue(
            all(schema["additionalProperties"] is False for schema in actual.values())
        )

    def test_fastmcp_rejects_invalid_scene_geometry_arguments_before_the_bridge(self):
        from mcp.shared.exceptions import McpError
        from mcp.shared.memory import create_connected_server_and_client_session
        from mcp.types import INVALID_PARAMS
        from sketchup_mcp import server as exported_server

        invalid_arguments = SCENE_GEOMETRY_CONTRACT["invalid_arguments"] + [
            {
                "name": "create_component",
                "arguments": {"typo": "cube"},
                "contains": "typo",
            },
            {
                "name": "create_component",
                "arguments": {"position": [1, 2]},
                "contains": "position",
            },
            {
                "name": "create_component",
                "arguments": {"position": [0, float("inf"), 0]},
                "contains": "position",
            },
        ]
        adapter = InMemoryBridgeAdapter.returning({})

        async def call_invalid_tools():
            with exported_server.use_bridge_client(BridgeClient(adapter=adapter)):
                async with create_connected_server_and_client_session(
                    exported_server.mcp
                ) as session:
                    return [
                        await session.call_tool(case["name"], case["arguments"])
                        for case in invalid_arguments
                    ]

        results = asyncio.run(call_invalid_tools())

        for case, result in zip(invalid_arguments, results, strict=True):
            with self.subTest(command=case["name"], arguments=case["arguments"]):
                self.assertTrue(result.isError)
                self.assertIn("Invalid arguments", result.content[0].text)
                self.assertIn(case["contains"], result.content[0].text)
        self.assertEqual([], adapter.requests)

        direct_adapter = InMemoryBridgeAdapter.returning({})
        with exported_server.use_bridge_client(BridgeClient(adapter=direct_adapter)):
            with self.assertRaises(McpError) as raised:
                asyncio.run(
                    exported_server.mcp.call_tool(
                        "get_selection", {"unknown": "argument"}
                    )
                )
        self.assertEqual(INVALID_PARAMS, raised.exception.error.code)
        self.assertEqual([], direct_adapter.requests)

    def test_scene_geometry_commands_share_one_bridge_tool_interface(self):
        for command in SCENE_GEOMETRY_CONTRACT["commands"]:
            with self.subTest(command=command["name"]):
                adapter = InMemoryBridgeAdapter.returning(command["wire_result"])
                tools = CommandForwarder(BridgeClient(adapter=adapter))

                result = tools.call(
                    command["name"],
                    command["arguments"],
                    request_id=command["request_id"],
                )

                self.assertEqual(json.dumps(command["wire_result"]), result)
                self.assertEqual(
                    {
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {
                            "name": command["name"],
                            "arguments": command["arguments"],
                        },
                        "id": command["request_id"],
                    },
                    adapter.requests[0],
                )

    def test_every_exported_scene_geometry_tool_preserves_success_and_remote_errors(self):
        from sketchup_mcp import server as exported_server

        invocations = {
            "create_component": lambda server, context, arguments: server.create_component(
                context,
                type=arguments["type"],
                position=arguments["position"],
                dimensions=arguments["dimensions"],
            ),
            "delete_component": lambda server, context, arguments: server.delete_component(
                context, id=arguments["id"]
            ),
            "transform_component": lambda server, context, arguments: server.transform_component(
                context,
                id=arguments["id"],
                position=arguments["position"],
                rotation=arguments["rotation"],
                scale=arguments["scale"],
            ),
            "get_selection": lambda server, context, _arguments: server.get_selection(
                context
            ),
            "set_material": lambda server, context, arguments: server.set_material(
                context, id=arguments["id"], material=arguments["material"]
            ),
            "export_scene": lambda server, context, arguments: server.export_scene(
                context, format=arguments["format"]
            ),
            "boolean_operation": lambda server, context, arguments: server.boolean_operation(
                context,
                operation=arguments["operation"],
                target_id=arguments["target_id"],
                tool_id=arguments["tool_id"],
                delete_originals=arguments["delete_originals"],
            ),
        }
        failure_actions = {
            "create_component": "creating component",
            "delete_component": "deleting component",
            "transform_component": "transforming component",
            "get_selection": "getting selection",
            "set_material": "setting material",
            "export_scene": "exporting scene",
            "boolean_operation": "performing boolean operation",
        }

        for command in SCENE_GEOMETRY_CONTRACT["commands"]:
            with self.subTest(command=command["name"], result="success"):
                adapter = InMemoryBridgeAdapter.returning(command["wire_result"])
                context = type(
                    "RequestContext", (), {"request_id": command["request_id"]}
                )()
                with exported_server.use_bridge_client(BridgeClient(adapter=adapter)):
                    result = invocations[command["name"]](
                        exported_server, context, command["arguments"]
                    )
                self.assertEqual(json.dumps(command["wire_result"]), result)

            with self.subTest(command=command["name"], result="remote-error"):
                adapter = InMemoryBridgeAdapter(
                    lambda request: {
                        "jsonrpc": "2.0",
                        "error": {
                            "code": -32602,
                            "message": "invalid scene geometry arguments",
                            "data": {"success": False},
                        },
                        "id": request["id"],
                    }
                )
                with exported_server.use_bridge_client(BridgeClient(adapter=adapter)):
                    result = invocations[command["name"]](
                        exported_server, context, command["arguments"]
                    )
                self.assertEqual(
                    f"Error {failure_actions[command['name']]}: "
                    "SketchUp bridge error -32602: invalid scene geometry arguments",
                    result,
                )

    def test_create_component_succeeds_through_the_bridge_seam(self):
        adapter = InMemoryBridgeAdapter.returning(CONTRACT["success_result"])

        result = call_create_component(
            BridgeClient(adapter=adapter),
            "mcp-create-17",
            type="cube",
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

                call_create_component(BridgeClient(adapter=adapter), request_id)

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
                result = call_create_component(
                    BridgeClient(adapter=adapter),
                    fixture["request_id"],
                    type=fixture.get("arguments", CONTRACT["defaults"])["type"],
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

        result = call_create_component(
            BridgeClient(adapter=adapter, max_attempts=fixture["attempts"]),
            fixture["request_id"],
        )

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

        with self.assertLogs("SketchUpMCP", level="INFO") as captured:
            call_create_component(
                BridgeClient(adapter=adapter),
                "safe-log-id",
                type="private-component-input",
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

        call_create_component(
            BridgeClient(adapter=adapter),
            "empty-vectors",
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
        self.assertIn('sketchup-mcp = "sketchup_mcp.mcp_server:main"', project)
        self.assertIn('sketchup = "sketchup_mcp.mcp_server:mcp"', project)


if __name__ == "__main__":
    unittest.main()
