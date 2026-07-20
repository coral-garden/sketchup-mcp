import json
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SUITE_ROOT = REPO_ROOT / "testup/production_adapter"


class ManualSketchUpSuiteContractTest(unittest.TestCase):
    def test_manifest_matches_the_exact_manually_runnable_test_inventory(self):
        manifest = json.loads(
            (SUITE_ROOT / "suite_manifest.json").read_text(encoding="utf-8")
        )
        suite = (SUITE_ROOT / "TC_ProductionAdapter.rb").read_text(encoding="utf-8")
        test_methods = re.findall(r"^  def test_([a-z0-9_]+)$", suite, re.MULTILINE)

        self.assertEqual(1, manifest["schema_version"])
        self.assertEqual(sorted(test_methods), test_methods)
        self.assertEqual(test_methods, manifest["scenarios"])
        self.assertEqual(21, len(test_methods))
        self.assertNotIn("runtime_report", suite)
        self.assertNotIn("run_marker", suite)

    def test_manual_suite_has_no_ci_attestation_or_coverage_environment_contract(self):
        loader = (REPO_ROOT / "su_mcp.rb").read_text(encoding="utf-8")
        support = (SUITE_ROOT / "support.rb").read_text(encoding="utf-8")
        combined = loader + support

        for obsolete in (
            "SKETCHUP_MCP_TESTUP_COVERAGE",
            "SKETCHUP_MCP_TESTUP_COMMIT",
            "SKETCHUP_MCP_TESTUP_RUN_ID",
            "SKETCHUP_MCP_TESTUP_RUNTIME_REPORT",
            "SKETCHUP_MCP_TESTUP_SUITE_MARKER",
            "Coverage.start",
            "manual attestation",
        ):
            with self.subTest(obsolete=obsolete):
                self.assertNotIn(obsolete, combined)

    def test_suite_still_covers_every_public_command(self):
        manifest = json.loads(
            (SUITE_ROOT / "suite_manifest.json").read_text(encoding="utf-8")
        )
        catalog = json.loads(
            (REPO_ROOT / "src/sketchup_mcp/command_catalog.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            [command["name"] for command in catalog["commands"]],
            manifest["commands"],
        )


if __name__ == "__main__":
    unittest.main()
