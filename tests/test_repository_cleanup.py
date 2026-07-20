from email.parser import Parser
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]


class RepositoryCleanupContractTest(unittest.TestCase):
    def test_obsolete_setup_artifacts_are_not_published(self):
        for relative_path in (
            ".local-index/sketchup_mcp-0.1.0-py3-none-any.whl",
            "docs/architecture-review-20260720.html",
            "plan.md",
            "requirements.txt",
            "sketchup.json",
            "su_mcp/extension.json",
            "su_mcp/package.rb",
            "su_mcp/package_contract.rb",
            "su_mcp/su_mcp.rb",
            "test_eval_ruby.py",
            "update_and_restart.sh",
        ):
            with self.subTest(path=relative_path):
                self.assertFalse((REPO_ROOT / relative_path).exists())
        self.assertIn(
            ".local-index/",
            (REPO_ROOT / ".gitignore").read_text(encoding="utf-8"),
        )

    def test_project_source_metadata_names_the_actual_project(self):
        project = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

        for expected in (
            'description = "SketchUp integration through the Model Context Protocol"',
            '{name = "Coral Garden"}',
            'Homepage = "https://github.com/coral-garden/sketchup-mcp"',
            'Repository = "https://github.com/coral-garden/sketchup-mcp"',
            'Issues = "https://github.com/coral-garden/sketchup-mcp/issues"',
            'Documentation = "https://github.com/coral-garden/sketchup-mcp#readme"',
            '"Operating System :: MacOS"',
            '"Operating System :: Microsoft :: Windows"',
        ):
            with self.subTest(metadata=expected):
                self.assertIn(expected, project)
        self.assertNotIn("Operating System :: OS Independent", project)
        self.assertNotIn("your.email@example.com", project)
        self.assertIn(
            "MIT License",
            (REPO_ROOT / "LICENSE").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "extension.creator = 'Coral Garden'",
            (REPO_ROOT / "su_mcp.rb").read_text(encoding="utf-8"),
        )

    def test_distributions_publish_the_real_project_identity_and_license(self):
        with tempfile.TemporaryDirectory() as directory:
            for builder in ("build_sdist", "build_wheel"):
                subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "from setuptools import build_meta; import sys; "
                            "getattr(build_meta, sys.argv[1])(sys.argv[2])"
                        ),
                        builder,
                        directory,
                    ],
                    cwd=REPO_ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            artifacts = Path(directory)
            wheel = next(artifacts.glob("*.whl"))
            source = next(artifacts.glob("*.tar.gz"))
            with zipfile.ZipFile(wheel) as archive:
                metadata_name = next(
                    name for name in archive.namelist() if name.endswith("/METADATA")
                )
                metadata = Parser().parsestr(
                    archive.read(metadata_name).decode("utf-8")
                )
                license_name = next(
                    name for name in archive.namelist() if name.endswith("/LICENSE")
                )
                wheel_license = archive.read(license_name).decode("utf-8")
            with tarfile.open(source) as archive:
                license_member = next(
                    member for member in archive.getmembers()
                    if member.name.endswith("/LICENSE")
                )
                extracted = archive.extractfile(license_member)
                self.assertIsNotNone(extracted)
                source_license = extracted.read().decode("utf-8")

        self.assertEqual(
            "SketchUp integration through the Model Context Protocol",
            metadata["Summary"],
        )
        self.assertEqual("Coral Garden", metadata["Author"])
        self.assertIsNone(metadata["Author-email"])
        self.assertEqual(
            {
                "Homepage, https://github.com/coral-garden/sketchup-mcp",
                "Repository, https://github.com/coral-garden/sketchup-mcp",
                "Issues, https://github.com/coral-garden/sketchup-mcp/issues",
                "Documentation, https://github.com/coral-garden/sketchup-mcp#readme",
            },
            set(metadata.get_all("Project-URL")),
        )
        self.assertIn("Operating System :: MacOS", metadata.get_all("Classifier"))
        self.assertIn(
            "Operating System :: Microsoft :: Windows",
            metadata.get_all("Classifier"),
        )
        self.assertNotIn(
            "Operating System :: OS Independent",
            metadata.get_all("Classifier"),
        )
        self.assertIn("MIT License", wheel_license)
        self.assertEqual(wheel_license, source_license)

    def test_published_sources_do_not_restore_retired_setup_descriptions(self):
        published_files = (
            [
                REPO_ROOT / "README.md",
                REPO_ROOT / "pyproject.toml",
                REPO_ROOT / "su_mcp.rb",
            ]
            + list((REPO_ROOT / "docs").rglob("*.md"))
            + list((REPO_ROOT / "examples").rglob("*.md"))
            + list((REPO_ROOT / "examples").rglob("*.py"))
            + list((REPO_ROOT / "scripts").rglob("*.py"))
            + list((REPO_ROOT / "src").rglob("*.py"))
            + list((REPO_ROOT / "su_mcp").rglob("*.rb"))
        )
        obsolete_fragments = (
            "https://github.com/yourusername/sketchup-mcp",
            "your.email@example.com",
            "MCP Team",
            "mcp.client import Client",
            'Client("sketchup")',
            ".is_connected",
            "su_mcp/su_mcp",
            "issue #11 migrates",
            "Start Server",
            "Stop Server",
            "0.1.0",
            "0.1.15",
            "0.1.17",
            "1.5.0",
            "1.6.0",
        )

        for path in published_files:
            if not path.is_file():
                continue
            source = path.read_text(encoding="utf-8")
            for fragment in obsolete_fragments:
                with self.subTest(path=path.relative_to(REPO_ROOT), fragment=fragment):
                    self.assertNotIn(fragment, source)


if __name__ == "__main__":
    unittest.main()
