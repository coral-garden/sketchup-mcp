import ast
import importlib.util
from pathlib import Path
import unittest

from sketchup_mcp.command_catalog import load_command_catalog


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"


class PublishedExampleContractTest(unittest.TestCase):
    def test_examples_use_the_supported_stdio_client_and_catalog_commands(self):
        scripts = tuple(sorted(EXAMPLES.glob("*.py")))

        self.assertEqual((EXAMPLES / "get_selection.py",), scripts)
        source = scripts[0].read_text(encoding="utf-8")
        self.assertNotIn("from mcp.client import Client", source)
        self.assertNotIn('Client("sketchup")', source)
        for supported_api in (
            "ClientSession",
            "StdioServerParameters",
            "stdio_client",
            "session.initialize()",
            "session.call_tool",
        ):
            with self.subTest(api=supported_api):
                self.assertIn(supported_api, source)

        syntax = ast.parse(source, filename=str(scripts[0]))
        called_tools = {
            node.args[0].value
            for node in ast.walk(syntax)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "call_tool"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        self.assertEqual({"get_selection"}, called_tools)
        self.assertLessEqual(called_tools, set(load_command_catalog().names))

        spec = importlib.util.spec_from_file_location(
            "published_get_selection_example", scripts[0]
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.main))

        readme = (EXAMPLES / "README.md").read_text(encoding="utf-8")
        self.assertIn("python examples/get_selection.py", readme)
        self.assertIn("MCP client", readme)
        self.assertIn("read-only", readme)
        self.assertNotIn("0.1.17", readme)
        self.assertNotIn("1.6.0", readme)


if __name__ == "__main__":
    unittest.main()
