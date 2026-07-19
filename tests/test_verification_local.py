"""Local dual-runtime verification tests."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify.py"


def load_verifier():
    spec = importlib.util.spec_from_file_location("verification_cli", VERIFY_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("verification CLI could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalVerificationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.local_arguments = [
            "local",
            "--report",
            str(Path(self.temporary.name) / "verification.json"),
        ]

    @staticmethod
    def succeed_with_exact_reports(command, **_options):
        if command[1:4] == ["-m", "coverage", "json"]:
            report = Path(command[command.index("-o") + 1])
            report.write_text(
                json.dumps(
                    {
                        "totals": {
                            "covered_lines": 590,
                            "num_statements": 590,
                            "covered_branches": 154,
                            "num_branches": 154,
                        }
                    }
                ),
                encoding="utf-8",
            )
        if command[:2] == ["ruby", "scripts/ruby_coverage.rb"] and "--json" in command:
            report = Path(command[command.index("--json") + 1])
            report.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "scope": "headless_ruby",
                        "thresholds": {"lines": 100, "branches": 100},
                        "lines": {"covered": 537, "total": 537},
                        "branches": {"covered": 175, "total": 175},
                    }
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0)

    def test_local_runs_every_local_suite_and_reports_each_runtime_scope(self):
        verifier = load_verifier()
        commands = []

        def succeed(command, **_options):
            commands.append(command)
            return self.succeed_with_exact_reports(command)

        output = io.StringIO()
        with mock.patch.object(verifier.subprocess, "run", side_effect=succeed):
            with contextlib.redirect_stdout(output):
                status = verifier.main(self.local_arguments)

        self.assertEqual(0, status, output.getvalue())
        self.assertEqual(5, len(commands))
        self.assertEqual(
            [str(REPO_ROOT / "scripts/test_python_coverage.sh")], commands[0]
        )
        self.assertEqual(
            [sys.executable, "-m", "coverage", "json", "--pretty-print", "-o"],
            commands[1][:-1],
        )
        self.assertEqual(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            commands[2],
        )
        self.assertEqual(
            ["ruby", "scripts/ruby_coverage.rb", "--json"],
            commands[3][:-1],
        )
        self.assertEqual(["ruby", "-Itest", "test/headless.rb"], commands[4])
        self.assertIn("Python: PASS", output.getvalue())
        self.assertIn("Headless Ruby: PASS", output.getvalue())
        self.assertIn("SketchUp runtime: EXTERNAL", output.getvalue())
        self.assertIn("Local verification: PASS", output.getvalue())

    def test_local_shell_gates_use_the_same_python_environment_as_the_cli(self):
        verifier = load_verifier()
        environments = []

        def succeed(command, **options):
            environments.append(options["env"])
            return self.succeed_with_exact_reports(command)

        with mock.patch.object(verifier.subprocess, "run", side_effect=succeed):
            status = verifier.main(self.local_arguments)

        self.assertEqual(0, status)
        expected = str(Path(sys.executable).absolute().parent)
        for environment in environments:
            with self.subTest(command_environment=environment):
                self.assertEqual(expected, environment["PATH"].split(os.pathsep)[0])

    def test_local_reports_the_failed_suite_and_still_runs_the_other_scopes(self):
        verifier = load_verifier()
        commands = []

        def fail_python_integration(command, **_options):
            commands.append(command)
            if "unittest" in command:
                return subprocess.CompletedProcess(command, 7)
            return self.succeed_with_exact_reports(command)

        output = io.StringIO()
        with mock.patch.object(
            verifier.subprocess,
            "run",
            side_effect=fail_python_integration,
        ):
            with contextlib.redirect_stdout(output):
                status = verifier.main(self.local_arguments)

        self.assertEqual(1, status)
        self.assertEqual(5, len(commands))
        self.assertIn("Python integration: FAIL (exit status 7)", output.getvalue())
        self.assertIn("Python: FAIL", output.getvalue())
        self.assertIn("Headless Ruby: PASS", output.getvalue())
        self.assertIn("Local verification: FAIL", output.getvalue())

    def test_local_reports_a_missing_required_executable_without_crashing(self):
        verifier = load_verifier()

        def missing_ruby(command, **_options):
            if command[0] == "ruby":
                raise FileNotFoundError(2, "No such file or directory", "ruby")
            return self.succeed_with_exact_reports(command)

        output = io.StringIO()
        with mock.patch.object(
            verifier.subprocess,
            "run",
            side_effect=missing_ruby,
        ):
            with contextlib.redirect_stdout(output):
                status = verifier.main(self.local_arguments)

        self.assertEqual(1, status)
        self.assertIn(
            "Headless Ruby coverage: FAIL (required executable missing: ruby)",
            output.getvalue(),
        )
        self.assertIn("Python: PASS", output.getvalue())
        self.assertIn("Headless Ruby: FAIL", output.getvalue())
        self.assertIn("Local verification: FAIL", output.getvalue())

    def test_local_reports_independent_exact_line_and_branch_metrics(self):
        verifier = load_verifier()
        output = io.StringIO()

        with mock.patch.object(
            verifier.subprocess,
            "run",
            side_effect=self.succeed_with_exact_reports,
        ):
            with contextlib.redirect_stdout(output):
                status = verifier.main(self.local_arguments)

        self.assertEqual(0, status, output.getvalue())
        self.assertIn(
            "Python coverage: lines 590/590, branches 154/154 (thresholds: 100%/100%)",
            output.getvalue(),
        )
        self.assertIn(
            "Headless Ruby coverage: lines 537/537, branches 175/175 "
            "(thresholds: 100%/100%)",
            output.getvalue(),
        )

    def test_local_writes_one_machine_readable_report_for_all_runtime_scopes(self):
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "local-verification.json"
            with mock.patch.object(
                verifier.subprocess,
                "run",
                side_effect=self.succeed_with_exact_reports,
            ):
                status = verifier.main(["local", "--report", str(report)])

            document = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(0, status)
        self.assertEqual(1, document["schema_version"])
        self.assertEqual("local", document["mode"])
        self.assertEqual("pass", document["status"])
        self.assertEqual(
            {
                "status": "pass",
                "coverage": {
                    "thresholds": {"lines": 100, "branches": 100},
                    "lines": {"covered": 590, "total": 590},
                    "branches": {"covered": 154, "total": 154},
                },
            },
            document["scopes"]["python"],
        )
        self.assertEqual(
            {
                "status": "pass",
                "coverage": {
                    "thresholds": {"lines": 100, "branches": 100},
                    "lines": {"covered": 537, "total": 537},
                    "branches": {"covered": 175, "total": 175},
                },
            },
            document["scopes"]["headless_ruby"],
        )
        self.assertEqual(
            {"status": "external", "required": False},
            document["scopes"]["sketchup_runtime"],
        )

    def test_local_rejects_a_successful_gate_that_omits_its_metrics_report(self):
        verifier = load_verifier()

        def omit_python_report(command, **options):
            if command[1:4] == ["-m", "coverage", "json"]:
                return subprocess.CompletedProcess(command, 0)
            return self.succeed_with_exact_reports(command, **options)

        output = io.StringIO()
        with mock.patch.object(
            verifier.subprocess,
            "run",
            side_effect=omit_python_report,
        ):
            with contextlib.redirect_stdout(output):
                status = verifier.main(self.local_arguments)

        document = json.loads(
            Path(self.local_arguments[-1]).read_text(encoding="utf-8")
        )
        self.assertEqual(1, status)
        self.assertIn(
            "Python coverage: FAIL (machine-readable report missing)",
            output.getvalue(),
        )
        self.assertEqual("fail", document["scopes"]["python"]["status"])
        self.assertEqual(
            "machine-readable coverage report missing or invalid",
            document["scopes"]["python"]["reason"],
        )

    def test_local_rejects_partial_branch_coverage_independently(self):
        verifier = load_verifier()

        def partial_python_branch(command, **options):
            completed = self.succeed_with_exact_reports(command, **options)
            if command[1:4] == ["-m", "coverage", "json"]:
                report = Path(command[command.index("-o") + 1])
                document = json.loads(report.read_text(encoding="utf-8"))
                document["totals"]["covered_branches"] = 153
                report.write_text(json.dumps(document), encoding="utf-8")
            return completed

        output = io.StringIO()
        with mock.patch.object(
            verifier.subprocess,
            "run",
            side_effect=partial_python_branch,
        ):
            with contextlib.redirect_stdout(output):
                status = verifier.main(self.local_arguments)

        self.assertEqual(1, status)
        self.assertIn(
            "Python branch coverage: FAIL (coverage is 153/154; required 100%)",
            output.getvalue(),
        )
        self.assertIn("Headless Ruby: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
