from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"
WORKFLOW = REPO_ROOT / ".github/workflows/verification.yml"
PROJECT_VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
README = REPO_ROOT / "README.md"
RUBY_TEST_NOTES = REPO_ROOT / "test/README.md"
VERIFICATION_NOTES = REPO_ROOT / "docs/testing/verification.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCKFILE = REPO_ROOT / "uv.lock"
BUILD_REQUIREMENTS = ("setuptools==80.9.0", "wheel==0.45.1")


def documented_shell_commands(document):
    blocks = re.findall(r"```sh\n(.*?)\n```", document, re.DOTALL)
    return tuple(
        line.strip()
        for block in blocks
        for line in block.splitlines()
        if line.strip()
    )


class ContributorWorkflowTest(unittest.TestCase):
    def test_fresh_clone_uses_one_locked_python_310_environment_everywhere(self):
        guide = CONTRIBUTING.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertTrue(LOCKFILE.is_file())
        self.assertIn("Python 3.10 or newer", guide)
        self.assertIn("Ruby 3.2.2", guide)
        sync = "uv sync --locked --python 3.10 --group test --group build"
        local = (
            "uv run --locked --group test --group build "
            "python scripts/verify.py local"
        )
        self.assertIn(sync, guide)
        self.assertIn(local, guide)
        self.assertRegex(
            workflow,
            re.compile(r"uses: astral-sh/setup-uv@[0-9a-f]{40} # v8\.3\.2"),
        )
        self.assertIn('version: "0.10.4"', workflow)
        self.assertGreaterEqual(workflow.count(sync), 2)
        self.assertIn(
            "uses: ruby/setup-ruby@003a5c4d8d6321bd302e38f6f0ec593f77f06600",
            workflow,
        )
        self.assertIn('ruby-version: "3.2.2"', workflow)
        self.assertNotIn("pip install", workflow)
        self.assertNotIn("requirements.txt", workflow)

    def test_build_system_and_lock_use_the_same_exact_reviewed_tooling(self):
        configuration = PYPROJECT.read_text(encoding="utf-8")
        lock = LOCKFILE.read_text(encoding="utf-8")

        self.assertIn(
            'build = [\n    "setuptools==80.9.0",\n    "wheel==0.45.1",\n]',
            configuration,
        )
        self.assertIn(
            'requires = ["setuptools==80.9.0", "wheel==0.45.1"]',
            configuration,
        )
        for requirement in BUILD_REQUIREMENTS:
            name, version = requirement.split("==", 1)
            with self.subTest(requirement=requirement):
                self.assertRegex(
                    lock,
                    re.compile(
                        rf'\[\[package\]\]\nname = "{re.escape(name)}"\n'
                        rf'version = "{re.escape(version)}"\n'
                    ),
                )
                self.assertRegex(
                    lock,
                    re.compile(
                        rf'\[package\.metadata\.requires-dev\].*?'
                        rf'build = \[(?:(?!\n\[).)*name = "{re.escape(name)}".*?'
                        rf'specifier = "=={re.escape(version)}"',
                        re.DOTALL,
                    ),
                )

    def test_local_gate_documents_headless_results_and_manual_desktop_boundary(self):
        guide = CONTRIBUTING.read_text(encoding="utf-8")

        for expected in (
            "Python: PASS",
            "Headless Ruby: PASS",
            "SketchUp runtime: MANUAL",
            "Local verification: PASS",
            "artifacts/verification/local.json",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, guide)
        self.assertIn("exactly 100% line and branch coverage", guide)
        self.assertIn("docs/testing/sketchup-testup.md", guide)
        self.assertIn("manual", guide.lower())
        self.assertNotIn("self-hosted runner", guide.lower())

    def test_successful_main_push_builds_all_versioned_artifacts(self):
        guide = CONTRIBUTING.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        normalized_workflow = " ".join(workflow.split())

        self.assertIn("needs: local", workflow)
        self.assertIn(
            "if: github.event_name == 'push' && github.ref == 'refs/heads/main'",
            workflow,
        )
        for command in (
            "python scripts/build.py --output-dir dist",
            "uv build --offline --no-build-isolation --out-dir dist",
            "python scripts/check_python_distribution.py --dist-dir dist",
            "sha256sum",
        ):
            with self.subTest(command=command):
                self.assertIn(command, normalized_workflow)
        self.assertIn("name: main-build-${{ github.sha }}", workflow)
        self.assertIn("path: dist/", workflow)
        self.assertIn("retention-days: 14", workflow)

        for artifact in (
            "sketchup-mcp-$version.rbz",
            "sketchup_mcp-$version-py3-none-any.whl",
            "sketchup_mcp-$version.tar.gz",
            "SHA256SUMS",
        ):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, guide)

    def test_every_documented_shell_command_has_a_workflow_mapping(self):
        guide = CONTRIBUTING.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertEqual(
            (
                "git clone https://github.com/coral-garden/sketchup-mcp.git",
                "cd sketchup-mcp",
                "uv sync --locked --python 3.10 --group test --group build",
                (
                    "uv run --locked --group test --group build "
                    "python scripts/verify.py local"
                ),
                'version="$(python scripts/build.py --print-version)"',
                "python scripts/build.py --output-dir dist",
                'python scripts/build.py --check "dist/sketchup-mcp-$version.rbz"',
                "uv build --offline --no-build-isolation --out-dir dist",
                (
                    "uv run --locked --group test --group build "
                    "python scripts/check_python_distribution.py --dist-dir dist"
                ),
            ),
            documented_shell_commands(guide),
        )
        self.assertNotIn(PROJECT_VERSION, guide)
        for command in documented_shell_commands(guide)[2:]:
            with self.subTest(command=command):
                self.assertIn(command, " ".join(workflow.split()))

    def test_project_docs_defer_to_the_single_canonical_contributor_workflow(self):
        readme = README.read_text(encoding="utf-8")
        ruby_notes = RUBY_TEST_NOTES.read_text(encoding="utf-8")
        verification_notes = VERIFICATION_NOTES.read_text(encoding="utf-8")

        self.assertIn("[Contributor workflow](CONTRIBUTING.md)", readme)
        self.assertNotIn("issues/19", readme)
        self.assertIn("../../CONTRIBUTING.md", verification_notes)
        self.assertNotIn("pip install", verification_notes)
        self.assertNotIn("requirements.txt", verification_notes)
        self.assertIn("../CONTRIBUTING.md", ruby_notes)
        self.assertNotRegex(ruby_notes, r"```(?:sh|bash)\n")


if __name__ == "__main__":
    unittest.main()
