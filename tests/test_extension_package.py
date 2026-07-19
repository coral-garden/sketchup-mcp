import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build.py"
PROJECT_VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
STABLE_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)
ARTIFACT_NAME = f"sketchup-mcp-{PROJECT_VERSION}.rbz"
EXPECTED_FILES = (
    "su_mcp.rb",
    "su_mcp/VERSION",
    "su_mcp/bridge_listener.rb",
    "su_mcp/bridge_runtime.rb",
    "su_mcp/command_catalog.json",
    "su_mcp/command_catalog.rb",
    "su_mcp/command_dispatcher.rb",
    "su_mcp/command_execution_error.rb",
    "su_mcp/command_executor.rb",
    "su_mcp/command_response_builder.rb",
    "su_mcp/eval_result.rb",
    "su_mcp/extension_menu.rb",
    "su_mcp/extension_runtime.rb",
    "su_mcp/main.rb",
    "su_mcp/sketchup_adapter.rb",
    "su_mcp/sketchup_commands.rb",
    "su_mcp/version.rb",
)


class ExtensionPackageTest(unittest.TestCase):
    def copy_package_fixture(self, destination: Path) -> Path:
        shutil.copy(REPO_ROOT / "VERSION", destination / "VERSION")
        shutil.copy(REPO_ROOT / "su_mcp.rb", destination / "su_mcp.rb")
        shutil.copytree(REPO_ROOT / "scripts", destination / "scripts")
        shutil.copytree(REPO_ROOT / "su_mcp", destination / "su_mcp")
        catalog_directory = destination / "src" / "sketchup_mcp"
        catalog_directory.mkdir(parents=True)
        shutil.copy(
            REPO_ROOT / "src" / "sketchup_mcp" / "command_catalog.json",
            catalog_directory / "command_catalog.json",
        )
        return destination

    def run_fixture_build(self, fixture: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(fixture / "scripts" / "build.py")],
            cwd=fixture,
            capture_output=True,
            text=True,
        )

    def build(self, output_dir: Path) -> Path:
        completed = subprocess.run(
            [
                sys.executable,
                str(BUILD_SCRIPT),
                "--output-dir",
                str(output_dir),
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        artifact = output_dir / ARTIFACT_NAME
        self.assertIn(ARTIFACT_NAME, completed.stdout)
        self.assertTrue(artifact.is_file())
        return artifact

    def test_project_version_drives_python_metadata_and_runtime(self):
        configuration = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertRegex(PROJECT_VERSION, STABLE_VERSION_PATTERN)
        self.assertIn('dynamic = ["version"]', configuration)
        self.assertIn(
            '[tool.setuptools.dynamic]\nversion = {file = ["VERSION"]}',
            configuration,
        )
        self.assertIn('"setuptools>=66.1"', configuration)

        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(REPO_ROOT / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sketchup_mcp, sketchup_mcp.mcp_server as server; "
                    "print(sketchup_mcp.__version__); print(server.__version__)"
                ),
            ],
            cwd=REPO_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            [PROJECT_VERSION, PROJECT_VERSION], completed.stdout.splitlines()
        )

    def test_build_is_reproducible_and_contains_only_one_loader_layout(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_artifact = self.build(Path(first))
            second_artifact = self.build(Path(second))
            first_bytes = first_artifact.read_bytes()
            second_bytes = second_artifact.read_bytes()

            self.assertEqual(first_bytes, second_bytes)
            self.assertEqual(
                hashlib.sha256(first_bytes).hexdigest(),
                hashlib.sha256(second_bytes).hexdigest(),
            )

            with zipfile.ZipFile(first_artifact) as archive:
                self.assertEqual(EXPECTED_FILES, tuple(archive.namelist()))
                for member in archive.infolist():
                    with self.subTest(member=member.filename):
                        self.assertEqual((1980, 1, 1, 0, 0, 0), member.date_time)
                        self.assertEqual(
                            stat.S_IFREG | 0o644,
                            member.external_attr >> 16,
                        )
                self.assertEqual(
                    (REPO_ROOT / "VERSION").read_bytes(),
                    archive.read("su_mcp/VERSION"),
                )
                self.assertEqual(
                    (REPO_ROOT / "src/sketchup_mcp/command_catalog.json").read_bytes(),
                    archive.read("su_mcp/command_catalog.json"),
                )

    def test_packaged_loader_registers_the_extension_with_project_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            artifact = self.build(temporary_path / "dist")
            extracted = temporary_path / "extension"
            with zipfile.ZipFile(artifact) as archive:
                archive.extractall(extracted)

            stubs = temporary_path / "stubs"
            stubs.mkdir()
            (stubs / "sketchup.rb").write_text(
                textwrap.dedent(
                    """
                    def file_loaded?(_path)
                      false
                    end

                    def file_loaded(_path); end

                    module Sketchup
                      def self.register_extension(extension, enabled)
                        $registered_extension = [extension, enabled]
                      end
                    end
                    """
                )
            )
            (stubs / "extensions.rb").write_text(
                textwrap.dedent(
                    """
                    class SketchupExtension
                      attr_reader :name, :loader
                      attr_accessor :description, :version, :copyright, :creator

                      def initialize(name, loader)
                        @name = name
                        @loader = loader
                      end
                    end
                    """
                )
            )
            runner = temporary_path / "inspect_loader.rb"
            runner.write_text(
                textwrap.dedent(
                    """
                    require 'json'
                    load ARGV.fetch(0)
                    extension, enabled = $registered_extension
                    puts JSON.generate(
                      name: extension.name,
                      loader: extension.loader,
                      description: extension.description,
                      version: extension.version,
                      enabled: enabled
                    )
                    """
                )
            )

            completed = subprocess.run(
                [
                    "ruby",
                    "-I",
                    str(stubs),
                    str(runner),
                    str(extracted / "su_mcp.rb"),
                ],
                cwd=extracted,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                {
                    "name": "SketchUp MCP",
                    "loader": "su_mcp/main",
                    "description": "SketchUp extension that runs the local command bridge",
                    "version": PROJECT_VERSION,
                    "enabled": True,
                },
                json.loads(completed.stdout),
            )

    def test_package_check_rejects_unsafe_paths_and_symbolic_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            cases = (
                ("unsafe path", "../outside.rb", b"unsafe"),
                ("unsafe path", "C:/outside.rb", b"unsafe"),
                ("symbolic link", "su_mcp/link.rb", b"target.rb"),
            )
            for case_number, (expected_error, member_name, contents) in enumerate(cases):
                with self.subTest(member_name=member_name):
                    artifact = temporary_path / f"unsafe-{case_number}.rbz"
                    info = zipfile.ZipInfo(member_name)
                    info.create_system = 3
                    if expected_error == "symbolic link":
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    with zipfile.ZipFile(artifact, "w") as archive:
                        archive.writestr(info, contents)

                    completed = subprocess.run(
                        [sys.executable, str(BUILD_SCRIPT), "--check", str(artifact)],
                        cwd=REPO_ROOT,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(0, completed.returncode)
                    self.assertIn(expected_error, completed.stderr.lower())

    def test_build_rejects_symbolic_links_in_the_support_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.copy_package_fixture(Path(temporary))
            (fixture / "su_mcp" / "unsafe-link.rb").symlink_to("main.rb")

            completed = self.run_fixture_build(fixture)

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("symbolic link", completed.stderr.lower())

    def test_build_rejects_missing_literal_require_relative_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.copy_package_fixture(Path(temporary))
            (fixture / "su_mcp" / "extension_menu.rb").unlink()

            completed = self.run_fixture_build(fixture)

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("missing require_relative target", completed.stderr.lower())

    def test_build_rejects_missing_sketchup_extension_load_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.copy_package_fixture(Path(temporary))
            (fixture / "su_mcp" / "main.rb").unlink()

            completed = self.run_fixture_build(fixture)

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("missing sketchupextension load target", completed.stderr.lower())

    def test_build_rejects_legacy_nested_extension_loader(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.copy_package_fixture(Path(temporary))
            shutil.copy(
                fixture / "su_mcp.rb", fixture / "su_mcp" / "su_mcp.rb"
            )

            completed = self.run_fixture_build(fixture)

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("additional extension loader", completed.stderr.lower())

    def test_build_rejects_prerelease_project_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.copy_package_fixture(Path(temporary))
            (fixture / "VERSION").write_text("2.0.0-rc1\n", encoding="utf-8")

            completed = self.run_fixture_build(fixture)

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("invalid project version", completed.stderr.lower())


if __name__ == "__main__":
    unittest.main()
