import json
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
BRIDGE_ADR = (
    REPO_ROOT / "docs/adr/0001-local-one-request-bridge-lifecycle.md"
).read_text(encoding="utf-8")
PROJECT_VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def markdown_section(document, heading):
    remainder = document.split(f"## {heading}\n", 1)[1]
    return remainder.split("\n## ", 1)[0]


def normalized_markdown(document):
    return " ".join(document.split())


def json_examples():
    return [
        json.loads(source)
        for source in re.findall(r"```json\n(.*?)\n```", README, re.DOTALL)
    ]


class EndUserSetupDocumentationTest(unittest.TestCase):
    def test_runtime_topology_uses_the_domain_roles_in_execution_order(self):
        topology = markdown_section(README, "Runtime topology")
        roles = (
            "MCP host",
            "MCP client",
            "Python MCP server",
            "bridge client",
            "Ruby bridge listener",
            "command executor",
            "SketchUp adapter",
            "SketchUp runtime",
        )

        positions = [topology.index(role) for role in roles]

        self.assertEqual(sorted(positions), positions)
        self.assertIn("same machine", topology)
        self.assertNotIn("same operating-system user", topology)
        self.assertIn("stdio", topology)
        self.assertIn("127.0.0.1", topology)

    def test_one_ordered_path_covers_build_install_start_configure_and_smoke(self):
        steps = (
            f"### 1. Build version {PROJECT_VERSION} from a clean checkout",
            "### 2. Install the extension in SketchUp",
            "### 3. Start the bridge listener",
            "### 4. Install the Python MCP server",
            "### 5. Configure and restart Claude Desktop",
            "### 6. Prove the full path with `get_selection`",
        )

        positions = [README.index(step) for step in steps]

        self.assertEqual(sorted(positions), positions)
        self.assertIn("Extensions > Extension Manager", README)
        self.assertIn("Install Extension", README)
        self.assertIn("SketchUp MCP > Start Bridge", README)
        self.assertIn("completely quit", README)

    def test_artifact_commands_and_names_are_derived_from_project_version(self):
        artifact = f"sketchup-mcp-{PROJECT_VERSION}.rbz"

        self.assertIn("python scripts/build.py", README)
        self.assertIn(f"dist/{artifact}", README)
        self.assertIn(
            f"python scripts/build.py --check dist/{artifact}", README
        )
        self.assertIn("SHA-256", README)
        self.assertIn(f"release for `v{PROJECT_VERSION}`", README)
        self.assertIn("published SHA-256", README)
        self.assertIn("Compare its printed SHA-256", README)

    def test_mcp_host_configuration_examples_are_complete_valid_json(self):
        configurations = [
            example for example in json_examples() if "mcpServers" in example
        ]

        self.assertEqual(2, len(configurations))
        macos, windows = configurations
        self.assertEqual(
            "/Users/you/Code/sketchup-mcp/.venv/bin/sketchup-mcp",
            macos["mcpServers"]["sketchup"]["command"],
        )
        self.assertEqual(
            "C:\\Users\\you\\Code\\sketchup-mcp\\.venv\\Scripts\\sketchup-mcp.exe",
            windows["mcpServers"]["sketchup"]["command"],
        )
        for configuration in configurations:
            server = configuration["mcpServers"]["sketchup"]
            self.assertEqual([], server["args"])
            self.assertNotIn("env", server)

    def test_smoke_check_documents_the_empty_selection_success_envelope(self):
        examples = json_examples()
        expected = {
            "content": [{"type": "text", "text": '{"entities":[]}'}],
            "isError": False,
            "success": True,
        }

        self.assertIn(expected, examples)
        self.assertIn("clear the sketchup selection", README.lower())
        self.assertIn("stdio", README)
        self.assertIn("bridge", README)
        self.assertIn("live SketchUp model", README)

    def test_troubleshooting_maps_symptoms_to_runtime_roles_and_logs(self):
        troubleshooting = markdown_section(README, "Troubleshooting")
        for role in (
            "MCP host / MCP client",
            "Python MCP server",
            "bridge client",
            "bridge listener",
            "extension runtime",
            "SketchUp runtime",
        ):
            with self.subTest(role=role):
                self.assertIn(role, troubleshooting)
        self.assertIn("mcp-server-sketchup.log", troubleshooting)
        self.assertIn("SketchUp bridge unavailable", troubleshooting)
        self.assertIn("Bridge listener:", troubleshooting)
        self.assertIn("Extension runtime:", troubleshooting)
        self.assertIn("Ruby Console", troubleshooting)

    def test_security_and_port_configuration_match_the_bridge_decision(self):
        security = normalized_markdown(markdown_section(README, "Security"))

        self.assertIn("127.0.0.1", security)
        self.assertIn("local machine", security)
        self.assertIn(
            "any local process or user able to connect", security.lower()
        )
        self.assertIn(
            "does not verify the connecting operating-system account", security
        )
        self.assertIn("no application authentication", security)
        self.assertIn("`eval_ruby`", security)
        self.assertIn("trusted local Ruby", security)
        self.assertIn("SKETCHUP_MCP_BRIDGE_PORT", security)
        self.assertIn("before starting SketchUp", security)
        self.assertIn("Claude Desktop configuration", security)

    def test_readme_and_bridge_decision_do_not_claim_same_user_is_enforced(self):
        for document in (README, BRIDGE_ADR):
            with self.subTest(document=document[:40]):
                normalized = normalized_markdown(document)
                self.assertNotIn("same operating-system user", normalized)
                self.assertNotIn(
                    "trust boundary is the local operating-system user",
                    normalized.lower(),
                )
                self.assertIn("local machine", normalized)
                self.assertIn(
                    "any local process or user able to connect", normalized.lower()
                )

    def test_setup_uses_primary_documentation_and_no_obsolete_install_language(self):
        for official_url in (
            "https://docs.astral.sh/uv/getting-started/installation/",
            "https://help.sketchup.com/en/extension-warehouse/installing-trial-extension",
            "https://modelcontextprotocol.io/docs/develop/connect-local-servers",
        ):
            with self.subTest(url=official_url):
                self.assertIn(official_url, README)

        obsolete = (
            "pypi.org",
            '"command": "uvx"',
            "Start Server",
            "download or build the latest",
            "SketchupMCP",
        )
        lowered = README.lower()
        for phrase in obsolete:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase.lower(), lowered)


if __name__ == "__main__":
    unittest.main()
