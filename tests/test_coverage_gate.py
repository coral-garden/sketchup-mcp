import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class CoverageGateTest(unittest.TestCase):
    def test_repository_gate_rejects_full_line_but_partial_branch_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            (fixture / "scripts").mkdir()
            (fixture / "src/sketchup_mcp").mkdir(parents=True)
            (fixture / "tests").mkdir()
            shutil.copy2(REPO_ROOT / "pyproject.toml", fixture / "pyproject.toml")
            shutil.copy2(
                REPO_ROOT / "scripts/test_python_coverage.sh",
                fixture / "scripts/test_python_coverage.sh",
            )
            shutil.copy2(
                REPO_ROOT / "scripts/run_python_coverage_tests.py",
                fixture / "scripts/run_python_coverage_tests.py",
            )
            (fixture / "src/sketchup_mcp/__init__.py").write_text(
                "def choose(value):\n"
                "    if value:\n"
                "        value = True\n"
                "    return value\n",
                encoding="utf-8",
            )
            (fixture / "tests/test_fixture.py").write_text(
                "import unittest\n"
                "from sketchup_mcp import choose\n\n"
                "class SampleTest(unittest.TestCase):\n"
                "    def test_true(self):\n        self.assertIs(True, choose(True))\n",
                encoding="utf-8",
            )
            environment = {
                **os.environ,
                "COVERAGE_FILE": str(fixture / ".coverage"),
                "PATH": os.pathsep.join(
                    (str(Path(sys.executable).parent), os.environ.get("PATH", ""))
                ),
            }
            report = subprocess.run(
                [str(fixture / "scripts/test_python_coverage.sh")],
                cwd=fixture,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

        output = report.stdout + report.stderr
        self.assertEqual(2, report.returncode, output)
        self.assertRegex(
            output,
            re.compile(
                r"src/sketchup_mcp/__init__\.py\s+4\s+0\s+2\s+1\s+\d+%"
            ),
        )
        self.assertIn("Coverage failure", output)
