import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
CHECKER = REPO_ROOT / "scripts" / "check_python_distribution.py"


def load_checker_module():
    scripts = str(CHECKER.parent)
    sys.path.insert(0, scripts)
    try:
        spec = importlib.util.spec_from_file_location(
            "check_python_distribution_contract", CHECKER
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("distribution checker could not be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(scripts)


class PythonDistributionVersionTest(unittest.TestCase):
    def _probe_version_error(self, repo_root):
        probe = (
            "from pathlib import Path\n"
            "import sys\n"
            "import check_python_distribution as checker\n"
            "checker.REPO_ROOT = Path(sys.argv[1])\n"
            "try:\n"
            "    checker._version()\n"
            "except Exception as error:\n"
            "    print(type(error).__name__)\n"
            "    print(error)\n"
            "    print(type(error.__cause__).__name__)\n"
            "else:\n"
            "    raise AssertionError('invalid fixture was accepted')\n"
        )
        return subprocess.run(
            [sys.executable, "-c", probe, str(repo_root)],
            cwd=REPO_ROOT / "scripts",
            check=False,
            capture_output=True,
            text=True,
        )

    def test_missing_version_translates_shared_package_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            checked = self._probe_version_error(Path(temporary))

        self.assertEqual(0, checked.returncode, checked.stderr)
        error_type, message, cause_type = checked.stdout.splitlines()
        self.assertEqual("DistributionError", error_type)
        self.assertIn("project version is unavailable", message)
        self.assertEqual("PackageError", cause_type)

    def test_locked_runtime_install_allows_hashed_downloads(self):
        checker = load_checker_module()
        recorded = []

        def record(command, *, cwd):
            recorded.append((command, cwd))

        checker._run = record
        workspace = Path("isolated-workspace")
        checker._install_locked_runtime_requirements(
            requirements=workspace / "runtime-requirements.txt",
            python=workspace / "venv/bin/python",
            workspace=workspace,
        )

        self.assertEqual(1, len(recorded))
        command, cwd = recorded[0]
        self.assertEqual(workspace, cwd)
        self.assertEqual(["uv", "pip", "install"], command[:3])
        self.assertIn("--require-hashes", command)
        self.assertNotIn("--offline", command)

    def test_invalid_version_translates_shared_package_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "VERSION").write_text("1.2.3-rc1\n", encoding="utf-8")
            checked = self._probe_version_error(root)

        self.assertEqual(0, checked.returncode, checked.stderr)
        error_type, message, cause_type = checked.stdout.splitlines()
        self.assertEqual("DistributionError", error_type)
        self.assertEqual("invalid project version: '1.2.3-rc1'", message)
        self.assertEqual("PackageError", cause_type)


@unittest.skipIf(
    os.environ.get("SKETCHUP_MCP_DETERMINISTIC_TESTS") == "1",
    "isolated Python artifact builds are outside the deterministic coverage gate",
)
class PythonDistributionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.dist = Path(cls.temporary.name) / "dist"
        cls.build = subprocess.run(
            [
                "uv",
                "build",
                "--offline",
                "--no-build-isolation",
                "--out-dir",
                str(cls.dist),
                str(REPO_ROOT),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _check(self, dist):
        return subprocess.run(
            [
                sys.executable,
                str(CHECKER),
                "--dist-dir",
                str(dist),
                "--python",
                sys.executable,
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def _copied_dist(self, parent):
        dist = parent / "dist"
        shutil.copytree(self.dist, dist)
        return dist

    def test_wheel_sdist_and_wheel_from_sdist_have_installable_project_metadata(self):
        self.assertEqual(0, self.build.returncode, self.build.stdout + self.build.stderr)

        checked = self._check(self.dist)

        self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
        self.assertIn("Python distribution: PASS", checked.stdout)
        self.assertIn(f"Version: {PROJECT_VERSION}", checked.stdout)
        self.assertIn(
            f"Wheel: sketchup_mcp-{PROJECT_VERSION}-py3-none-any.whl",
            checked.stdout,
        )
        self.assertIn(
            f"Source distribution: sketchup_mcp-{PROJECT_VERSION}.tar.gz",
            checked.stdout,
        )
        self.assertIn("Wheel-from-sdist install: PASS", checked.stdout)
        self.assertIn("Entry points: PASS", checked.stdout)

    def test_missing_advertised_application_module_is_rejected_in_wheel(self):
        self.assertEqual(0, self.build.returncode, self.build.stdout + self.build.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            dist = self._copied_dist(Path(temporary))
            wheel = dist / f"sketchup_mcp-{PROJECT_VERSION}-py3-none-any.whl"
            rewritten = dist / "rewritten.whl"
            with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(
                rewritten, "w"
            ) as target:
                for member in source.infolist():
                    if member.filename != "sketchup_mcp/mcp_server.py":
                        target.writestr(member, source.read(member.filename))
            rewritten.replace(wheel)

            checked = self._check(dist)

        self.assertNotEqual(0, checked.returncode)
        self.assertIn("mcp_server.py", checked.stderr)

    def test_missing_advertised_application_module_is_rejected_in_sdist(self):
        self.assertEqual(0, self.build.returncode, self.build.stdout + self.build.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            dist = self._copied_dist(Path(temporary))
            sdist = dist / f"sketchup_mcp-{PROJECT_VERSION}.tar.gz"
            rewritten = dist / "rewritten.tar.gz"
            omitted = (
                f"sketchup_mcp-{PROJECT_VERSION}/src/sketchup_mcp/mcp_server.py"
            )
            with tarfile.open(sdist, "r:gz") as source, tarfile.open(
                rewritten, "w:gz"
            ) as target:
                for member in source.getmembers():
                    if member.name == omitted:
                        continue
                    contents = source.extractfile(member) if member.isfile() else None
                    target.addfile(member, contents)
            rewritten.replace(sdist)

            checked = self._check(dist)

        self.assertNotEqual(0, checked.returncode)
        self.assertIn("mcp_server.py", checked.stderr)


if __name__ == "__main__":
    unittest.main()
