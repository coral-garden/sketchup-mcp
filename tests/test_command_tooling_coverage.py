from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]

class DocumentationAndParityCoverageTest(unittest.TestCase):
    @staticmethod
    def write_document_fixture(root, readme_body="old", catalog_body="old"):
        from sketchup_mcp.command_docs import END_MARKER, START_MARKER

        (root / "docs").mkdir()
        (root / "README.md").write_text(
            f"before\n{START_MARKER}\n{readme_body}\n{END_MARKER}\nafter\n",
            encoding="utf-8",
        )
        (root / "docs/command-catalog.md").write_text(
            f"before\n{START_MARKER}\n{catalog_body}\n{END_MARKER}\nafter\n",
            encoding="utf-8",
        )

    def test_document_generation_reports_missing_markers_and_repairs_stale_blocks(self):
        from sketchup_mcp.command_docs import (
            check_documents,
            stale_documents,
            write_documents,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_document_fixture(root)
            self.assertEqual(
                ("README.md", "docs/command-catalog.md"), stale_documents(root)
            )
            self.assertFalse(check_documents(root))
            write_documents(root)
            self.assertTrue(check_documents(root))

            (root / "README.md").write_text("no markers", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing command-catalog markers"):
                stale_documents(root)

    def test_document_cli_covers_write_stale_and_current_results(self):
        from sketchup_mcp import command_docs

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_document_fixture(root)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(1, command_docs.main([str(root)]))
            self.assertIn("Stale generated command documents", output.getvalue())
            self.assertEqual(0, command_docs.main([str(root), "--write"]))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, command_docs.main([str(root)]))
            self.assertIn("documents are current", output.getvalue())

    def test_document_module_execution_returns_the_cli_status(self):
        old_argv = sys.argv
        try:
            sys.argv = ["command_docs", str(REPO_ROOT)]
            with warnings.catch_warnings(), self.assertRaises(SystemExit) as raised:
                warnings.simplefilter("ignore", RuntimeWarning)
                runpy.run_module("sketchup_mcp.command_docs", run_name="__main__")
        finally:
            sys.argv = old_argv
        self.assertEqual(0, raised.exception.code)

    def subprocess_adapter(self, package_exact=True):
        from sketchup_mcp.command_catalog import load_command_catalog

        names = json.dumps(load_command_catalog().names)

        def run(arguments, **options):
            if "-c" in arguments:
                return subprocess.CompletedProcess(arguments, 0, stdout=names, stderr="")
            if Path(arguments[0]).name.startswith("ruby"):
                return subprocess.CompletedProcess(arguments, 0, stdout=names, stderr="")
            if package_exact is not None:
                output = Path(arguments[arguments.index("--output-dir") + 1])
                artifact = output / "sketchup-mcp-test.rbz"
                source = (
                    Path(options["cwd"])
                    / "src/sketchup_mcp/command_catalog.json"
                ).read_bytes()
                with zipfile.ZipFile(artifact, "w") as archive:
                    archive.writestr(
                        "su_mcp/command_catalog.json",
                        source if package_exact else b"{}",
                    )
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

        return run

    def test_repository_parity_observes_all_consumers_through_substituted_processes(self):
        from sketchup_mcp.command_parity import inspect_repository

        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "sketchup_mcp.command_parity.subprocess.run",
            side_effect=self.subprocess_adapter(),
        ):
            reports = inspect_repository(REPO_ROOT)
        self.assertEqual(5, len(reports))
        self.assertTrue(all(report.in_sync for report in reports))
        self.assertTrue(all(report.as_dict()["in_sync"] for report in reports))

        with mock.patch.dict(os.environ, {"PYTHONPATH": "existing"}, clear=True), mock.patch(
            "sketchup_mcp.command_parity.subprocess.run",
            side_effect=self.subprocess_adapter(package_exact=False),
        ):
            reports = inspect_repository(REPO_ROOT)
        self.assertFalse(reports[-1].in_sync)

        with mock.patch(
            "sketchup_mcp.command_parity.subprocess.run",
            side_effect=self.subprocess_adapter(package_exact=None),
        ):
            reports = inspect_repository(REPO_ROOT)
        self.assertFalse(reports[-1].in_sync)

    def test_repository_parity_treats_unreadable_documents_as_stale(self):
        from sketchup_mcp.command_parity import repository_command_names

        with mock.patch(
            "sketchup_mcp.command_parity.stale_documents",
            side_effect=OSError("unreadable"),
        ), mock.patch(
            "sketchup_mcp.command_parity.subprocess.run",
            side_effect=self.subprocess_adapter(),
        ):
            names = repository_command_names(REPO_ROOT)
        self.assertEqual(set(), names["readme"])
        self.assertEqual(set(), names["command_docs"])

    def test_parity_cli_renders_json_and_text_for_sync_and_drift(self):
        from sketchup_mcp import command_parity
        from sketchup_mcp.command_parity import ConsumerParity

        sync = ConsumerParity("sync", (), (), {})
        drift = ConsumerParity(
            "drift", ("missing",), ("extra",), {"old": "new"}
        )
        empty_drift = ConsumerParity("empty", (), (), {})

        output = io.StringIO()
        with mock.patch.object(
            command_parity, "inspect_repository", return_value=(sync,)
        ), redirect_stdout(output):
            self.assertEqual(0, command_parity.main([".", "--json"]))
        self.assertTrue(json.loads(output.getvalue())["in_sync"])

        output = io.StringIO()
        with mock.patch.object(
            command_parity,
            "inspect_repository",
            return_value=(drift, empty_drift),
        ), redirect_stdout(output):
            self.assertEqual(1, command_parity.main(["."]))
        rendered = output.getvalue()
        self.assertIn("drift: out of sync", rendered)
        self.assertIn("missing: missing", rendered)
        self.assertIn("extra: extra", rendered)
        self.assertIn("old -> new", rendered)
        self.assertIn("missing: none", rendered)
        self.assertIn("differently named: none", rendered)

    def test_parity_module_execution_returns_the_cli_status(self):
        old_argv = sys.argv
        try:
            sys.argv = ["command_parity", "--json", str(REPO_ROOT)]
            with mock.patch(
                "subprocess.run", side_effect=self.subprocess_adapter()
            ), warnings.catch_warnings(), self.assertRaises(SystemExit) as raised:
                warnings.simplefilter("ignore", RuntimeWarning)
                runpy.run_module("sketchup_mcp.command_parity", run_name="__main__")
        finally:
            sys.argv = old_argv
        self.assertEqual(0, raised.exception.code)
