import asyncio
import importlib
import json
import runpy
import sys
import unittest
from unittest import mock

from mcp.shared.exceptions import McpError

from sketchup_mcp.bridge import BridgeClient, InMemoryBridgeAdapter


class FastMCPAndEntryPointCoverageTest(unittest.TestCase):
    def test_project_version_falls_back_to_installed_metadata(self):
        from importlib.metadata import PackageNotFoundError
        from sketchup_mcp import _version

        source_file = mock.Mock()
        source_root = mock.MagicMock()
        source_file.resolve.return_value.parents = (mock.Mock(), mock.Mock(), source_root)
        source_root.__truediv__.return_value.is_file.return_value = False

        with mock.patch.object(_version, "Path", return_value=source_file), mock.patch.object(
            _version, "version", return_value="9.8.7"
        ):
            self.assertEqual("9.8.7", _version._project_version())

        with mock.patch.object(_version, "Path", return_value=source_file), mock.patch.object(
            _version, "version", side_effect=PackageNotFoundError("sketchup-mcp")
        ):
            with self.assertRaisesRegex(RuntimeError, "project version is unavailable"):
                _version._project_version()

    def test_non_catalog_tools_keep_fastmcp_discovery_and_dispatch_behavior(self):
        from sketchup_mcp.catalog_fastmcp import CatalogFastMCP

        server = CatalogFastMCP("Mixed tools")

        @server.tool()
        def health() -> str:
            return "healthy"

        tools = asyncio.run(server.list_tools())
        result = asyncio.run(server.call_tool("health", {}))

        self.assertEqual(["health"], [tool.name for tool in tools])
        self.assertEqual("healthy", result[0][0].text)

    def test_catalog_tool_invalid_arguments_map_to_mcp_invalid_params(self):
        from sketchup_mcp import mcp_server

        with self.assertRaises(McpError) as raised:
            asyncio.run(mcp_server.mcp.call_tool("delete_component", {"id": 0}))
        self.assertIn("Invalid arguments for delete_component", str(raised.exception))

    def test_package_lazy_export_and_unknown_attributes_are_observable(self):
        import sketchup_mcp
        from sketchup_mcp import mcp_server

        self.assertIs(mcp_server.mcp, sketchup_mcp.__getattr__("mcp"))
        with self.assertRaisesRegex(AttributeError, "has no attribute 'missing'"):
            sketchup_mcp.__getattr__("missing")

    def test_module_entry_points_delegate_to_the_canonical_stdio_runner(self):
        from sketchup_mcp import mcp_server

        imported = sys.modules.pop("sketchup_mcp.__main__", None)
        try:
            with mock.patch.object(mcp_server, "main") as import_main:
                importlib.import_module("sketchup_mcp.__main__")
            import_main.assert_not_called()
        finally:
            sys.modules.pop("sketchup_mcp.__main__", None)
            if imported is not None:
                sys.modules["sketchup_mcp.__main__"] = imported

        with mock.patch.object(mcp_server, "main") as main:
            runpy.run_module("sketchup_mcp.__main__", run_name="__main__")
        main.assert_called_once_with()

        with mock.patch.object(mcp_server.mcp, "run") as run:
            mcp_server.main()
        run.assert_called_once_with()

    def test_default_bridge_client_is_created_once_and_reset_by_lifespan(self):
        from sketchup_mcp import mcp_server

        mcp_server._bridge_client = None
        expected = BridgeClient(adapter=InMemoryBridgeAdapter.returning({}))
        with mock.patch.object(
            mcp_server.BridgeClient, "from_environment", return_value=expected
        ) as from_environment:
            self.assertIs(expected, mcp_server.get_bridge_client())
            self.assertIs(expected, mcp_server.get_bridge_client())
        from_environment.assert_called_once_with()

        async def consume_lifespan():
            async with mcp_server.server_lifespan(mcp_server.mcp) as state:
                self.assertEqual({}, state)
                self.assertIs(expected, mcp_server.get_bridge_client())

        asyncio.run(consume_lifespan())
        self.assertIsNone(mcp_server._bridge_client)

    def test_transform_component_forwards_only_supplied_optional_values(self):
        from sketchup_mcp import mcp_server

        context = type("RequestContext", (), {"request_id": "transform-coverage"})()
        adapter = InMemoryBridgeAdapter.returning({"success": True})
        with mcp_server.use_bridge_client(BridgeClient(adapter=adapter)):
            result = mcp_server.transform_component(context, id=7)
        self.assertEqual(json.dumps({"success": True}), result)
        self.assertEqual(
            {"id": 7}, adapter.requests[0]["params"]["arguments"]
        )
