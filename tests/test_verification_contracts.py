"""GitHub workflow and verification-boundary contracts."""

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class VerificationContractTest(unittest.TestCase):
    def test_hosted_ci_verifies_candidates_and_builds_only_successful_main(self):
        workflow = (REPO_ROOT / ".github/workflows/verification.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("pull_request:", workflow)
        self.assertIn("push:", workflow)
        self.assertIn("branches:\n      - main", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertGreaterEqual(workflow.count("runs-on: ubuntu-24.04"), 2)
        self.assertNotIn("self-hosted", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertGreaterEqual(workflow.count("persist-credentials: false"), 2)
        self.assertIn("python scripts/verify.py local", workflow)
        self.assertIn("artifacts/verification/local.json", workflow)

        build = workflow.split("\n  build:", 1)[1]
        self.assertIn("needs: local", build)
        self.assertIn(
            "if: github.event_name == 'push' && github.ref == 'refs/heads/main'",
            build,
        )
        self.assertIn("python scripts/build.py --output-dir dist", build)
        self.assertIn("python scripts/build.py --check", build)
        self.assertIn("uv build --offline --no-build-isolation --out-dir dist", build)
        self.assertIn("python scripts/check_python_distribution.py --dist-dir dist", build)
        self.assertIn("SHA256SUMS", build)
        self.assertIn("name: main-build-${{ github.sha }}", build)
        self.assertIn("path: dist/", build)

        action_lines = [
            line.strip() for line in workflow.splitlines() if "uses:" in line
        ]
        self.assertGreater(len(action_lines), 0)
        for line in action_lines:
            with self.subTest(action=line):
                self.assertRegex(line, r"^uses: [^@]+@[0-9a-f]{40}(?:\s+#.*)?$")

    def test_desktop_automation_is_not_part_of_the_supported_workflow(self):
        for relative in (
            ".github/workflows/sketchup-runtime.yml",
            ".github/workflows/release-verification.yml",
            "scripts/sketchup_runtime_evidence.py",
            "scripts/sketchup_runtime_runner.py",
            "scripts/install_acceptance.py",
            "scripts/trusted_release.py",
        ):
            with self.subTest(path=relative):
                self.assertFalse((REPO_ROOT / relative).exists())

    def test_docs_explain_build_artifacts_and_manual_sketchup_acceptance(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        guide = (REPO_ROOT / "docs/testing/verification.md").read_text(
            encoding="utf-8"
        )
        manual = (REPO_ROOT / "docs/testing/sketchup-testup.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("docs/testing/verification.md", readme)
        self.assertIn("python scripts/verify.py local", guide)
        self.assertIn("100% line and branch coverage", guide)
        self.assertIn("main-build-", guide)
        self.assertIn("RBZ", guide)
        self.assertIn("wheel", guide)
        self.assertIn("source distribution", guide)
        self.assertIn("SHA256SUMS", guide)
        self.assertIn("manual", guide.lower())
        self.assertNotIn("self-hosted", guide.lower())
        self.assertNotIn("runtime_run_id", guide)
        self.assertNotIn("SketchUp Runtime Evidence", guide)

        self.assertIn("TestUp 2.5.4", manual)
        self.assertIn("TC_ProductionAdapter", manual)
        self.assertIn("21 tests", manual)
        self.assertIn("get_selection", manual)
        self.assertIn("manual acceptance", manual.lower())


if __name__ == "__main__":
    unittest.main()
