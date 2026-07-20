from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"
LOCAL_WORKFLOW = REPO_ROOT / ".github/workflows/verification.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github/workflows/release-verification.yml"
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

        self.assertTrue((REPO_ROOT / "uv.lock").is_file())
        self.assertIn("Python 3.10 or newer", guide)
        self.assertIn("Ruby 3.2.2", guide)
        sync = "uv sync --locked --python 3.10 --group test --group build"
        local = (
            "uv run --locked --group test --group build "
            "python scripts/verify.py local"
        )
        self.assertIn(sync, guide)
        self.assertIn(
            local,
            guide,
        )
        for workflow_path in (LOCAL_WORKFLOW, RELEASE_WORKFLOW):
            with self.subTest(workflow=workflow_path.name):
                workflow = workflow_path.read_text(encoding="utf-8")
                self.assertRegex(
                    workflow,
                    re.compile(
                        r"uses: astral-sh/setup-uv@[0-9a-f]{40} # v8\.3\.2"
                    ),
                )
                self.assertIn('version: "0.10.4"', workflow)
                self.assertIn(
                    sync,
                    workflow,
                )
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

    def test_local_gate_documents_distinct_runtime_results_and_exact_thresholds(self):
        guide = CONTRIBUTING.read_text(encoding="utf-8")

        for expected in (
            "Python: PASS",
            "Headless Ruby: PASS",
            "SketchUp runtime: EXTERNAL",
            "Local verification: PASS",
            "artifacts/verification/local.json",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, guide)
        self.assertIn("exactly 100% line and branch coverage", guide)
        self.assertIn("docs/testing/sketchup-testup.md", guide)
        self.assertIn("licensed", guide.lower())
        self.assertIn("Windows or macOS", guide)
        self.assertNotIn("Linux can produce trusted SketchUp", guide)

    def test_release_preparation_builds_versioned_artifacts_and_publishes_nothing(self):
        guide = CONTRIBUTING.read_text(encoding="utf-8")
        workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        rbz = "sketchup-mcp-$version.rbz"
        wheel = "sketchup_mcp-$version-py3-none-any.whl"
        sdist = "sketchup_mcp-$version.tar.gz"

        commands = (
            'version="$(python scripts/build.py --print-version)"',
            "python scripts/build.py",
            'python scripts/build.py --check "dist/sketchup-mcp-$version.rbz"',
            "uv build --offline --no-build-isolation --out-dir dist",
            (
                "uv run --locked --group test --group build "
                "python scripts/verify.py release "
                "--runtime-root artifacts/trusted-runtime "
                "--runtime-run-id RUN_ID"
            ),
        )
        normalized_guide = " ".join(guide.split())
        normalized_workflow = " ".join(workflow.split())
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, normalized_guide)
        for artifact in (rbz, wheel, sdist):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, guide)

        self.assertIn("python scripts/build.py", normalized_workflow)
        self.assertIn(
            "uv build --offline --no-build-isolation --out-dir dist",
            normalized_workflow,
        )
        self.assertIn("python scripts/verify.py release", normalized_workflow)
        self.assertIn("SketchUp runtime: PASS", guide)
        self.assertIn("Release verification: PASS", guide)
        self.assertIn("artifacts/verification/release.json", guide)
        self.assertIn("does not", guide.lower())
        for prohibited in ("git tag", "git push", "gh release create", "twine upload"):
            with self.subTest(prohibited=prohibited):
                self.assertNotIn(prohibited, guide)

    def test_every_documented_shell_command_has_an_executable_workflow_mapping(self):
        guide = CONTRIBUTING.read_text(encoding="utf-8")
        local = LOCAL_WORKFLOW.read_text(encoding="utf-8")
        release = RELEASE_WORKFLOW.read_text(encoding="utf-8")

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
                "python scripts/build.py",
                'python scripts/build.py --check "dist/sketchup-mcp-$version.rbz"',
                "uv build --offline --no-build-isolation --out-dir dist",
                (
                    "uv run --locked --group test --group build "
                    "python scripts/verify.py release "
                    "--runtime-root artifacts/trusted-runtime "
                    "--runtime-run-id RUN_ID"
                ),
            ),
            documented_shell_commands(guide),
        )
        self.assertNotIn(PROJECT_VERSION, guide)
        self.assertIn("actions/checkout@", local)
        self.assertIn("persist-credentials: false", local)
        self.assertIn(
            "uv sync --locked --python 3.10 --group test --group build", local
        )
        self.assertIn(
            "uv run --locked --group test --group build "
            "python scripts/verify.py local",
            local,
        )
        self.assertIn('version="$(python scripts/build.py --print-version)"', release)
        self.assertIn("python scripts/build.py\n", release)
        self.assertIn(
            'python scripts/build.py --check "dist/sketchup-mcp-$version.rbz"',
            release,
        )
        self.assertIn(
            "uv build --offline --no-build-isolation --out-dir dist", release
        )
        self.assertIn(
            "uv run --locked --group test --group build "
            "python scripts/check_python_distribution.py --dist-dir dist",
            release,
        )
        self.assertIn(
            "uv run --locked --group test --group build "
            "python scripts/verify.py release",
            release,
        )
        self.assertIn('--runtime-root artifacts/trusted-runtime', release)
        self.assertIn('--runtime-run-id "$RUNTIME_RUN_ID"', release)

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
