"""GitHub workflow and contributor-documentation verification contracts."""

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class VerificationContractTest(unittest.TestCase):
    def test_ci_runs_only_the_local_gate_on_an_unprivileged_hosted_runner(self):
        workflow_path = REPO_ROOT / ".github/workflows/verification.yml"
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn("pull_request:", workflow)
        self.assertIn("push:", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertNotIn("self-hosted", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("python scripts/verify.py local", workflow)
        self.assertIn("artifacts/verification/local.json", workflow)

        action_lines = [
            line.strip() for line in workflow.splitlines() if "uses:" in line
        ]
        self.assertGreater(len(action_lines), 0)
        for line in action_lines:
            with self.subTest(action=line):
                self.assertRegex(line, r"^uses: [^@]+@[0-9a-f]{40}(?:\s+#.*)?$")

    def test_runtime_and_release_workflows_are_manual_pinned_and_fail_closed(self):
        runtime = (REPO_ROOT / ".github/workflows/sketchup-runtime.yml").read_text(
            encoding="utf-8"
        )
        release = (REPO_ROOT / ".github/workflows/release-verification.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("name: SketchUp Runtime Evidence", runtime)
        self.assertIn("workflow_dispatch:", runtime)
        self.assertNotIn("pull_request", runtime)
        for input_name in (
            "operator:",
            "licensed_runner_confirmed:",
            "single_testup_process_confirmed:",
            "candidate_install_confirmed:",
        ):
            self.assertIn(input_name, runtime)
        self.assertNotRegex(
            runtime,
            r"(?:operator|licensed_runner_confirmed|single_testup_process_confirmed|"
            r"candidate_install_confirmed):\n(?: {8}.+\n)* {8}default:",
        )
        self.assertIn("LICENSED_RUNNER_CONFIRMED", runtime)
        self.assertIn("SINGLE_TESTUP_PROCESS_CONFIRMED", runtime)
        self.assertIn("CANDIDATE_INSTALL_CONFIRMED", runtime)
        self.assertGreaterEqual(runtime.count("-ne 'true'"), 3)
        self.assertGreaterEqual(runtime.count("== 'true'"), 3)
        self.assertIn("ATTESTING_OPERATOR: ${{ inputs.operator }}", runtime)
        self.assertIn("GITHUB_DISPATCHER: ${{ github.actor }}", runtime)
        self.assertNotIn("ATTESTING_OPERATOR: ${{ github.actor }}", runtime)
        self.assertIn("environment: sketchup-runtime", runtime)
        self.assertIn("[self-hosted, windows, sketchup-runtime, interactive]", runtime)
        self.assertIn("[self-hosted, macos, sketchup-runtime, interactive]", runtime)
        self.assertIn("git merge-base --is-ancestor", runtime)
        self.assertIn("origin/main", runtime)
        self.assertIn("persist-credentials: false", runtime)
        self.assertIn("permissions:\n  contents: read", runtime)
        self.assertIn("sketchup-runtime-evidence-${{ github.run_id }}", runtime)
        self.assertIn("SKETCHUP_WINDOWS_PLUGINS_DIR", runtime)
        self.assertIn("SKETCHUP_MACOS_PLUGINS_DIR", runtime)
        self.assertEqual(2, runtime.count("sketchup_runtime_runner.py prepare"))
        self.assertEqual(2, runtime.count("sketchup_runtime_runner.py collect"))
        self.assertEqual(2, runtime.count("sketchup_runtime_runner.py cleanup"))
        self.assertNotIn("sketchup_runtime_evidence.py collect", runtime)
        self.assertNotIn("--run-context',", runtime)
        self.assertGreaterEqual(runtime.count("-RubyStartup"), 2)
        self.assertGreaterEqual(runtime.count("if: always()"), 2)

        self.assertIn("name: Release Verification", release)
        self.assertIn("workflow_dispatch:", release)
        self.assertNotIn("pull_request", release)
        self.assertIn("runs-on: ubuntu-24.04", release)
        self.assertNotIn("self-hosted", release)
        self.assertIn("actions: read", release)
        self.assertNotIn("contents: write", release)
        self.assertIn("persist-credentials: false", release)
        self.assertIn("git merge-base --is-ancestor", release)
        self.assertIn("origin/main", release)
        self.assertIn("run-id:", release)
        self.assertIn("python scripts/verify.py release", release)
        self.assertNotRegex(release, r"https?://\$\{\{")
        self.assertNotIn("repository:", release)
        self.assertIn("path: artifacts/verification/", release)

        for workflow in (runtime, release):
            action_lines = [
                line.strip() for line in workflow.splitlines() if "uses:" in line
            ]
            self.assertGreater(len(action_lines), 0)
            for line in action_lines:
                with self.subTest(action=line):
                    self.assertRegex(
                        line, r"^uses: [^@]+@[0-9a-f]{40}(?:\s+#.*)?$"
                    )

    def test_contributor_docs_explain_local_runtime_and_release_gates(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        guide = (REPO_ROOT / "docs/testing/verification.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("docs/testing/verification.md", readme)
        self.assertIn("python scripts/verify.py local", guide)
        self.assertIn("100% line and branch coverage", guide)
        self.assertIn("SketchUp Runtime Evidence", guide)
        self.assertIn("sketchup-runtime", guide)
        self.assertIn("Release Verification", guide)
        self.assertIn("24 hours", guide)
        self.assertIn("full SHA", guide)
        self.assertIn("runtime_run_id", guide)
        self.assertIn("does not publish", guide)
        self.assertIn("Sketchup.install_from_archive(rbz, false)", guide)
        self.assertIn(".sketchup-mcp-runtime-runner.json", guide)
        self.assertIn("candidate-install-input.json", guide)
        self.assertIn("bootstrap input by filename, SHA-256, and byte", guide)
        self.assertIn("Sketchup.find_support_file('Plugins')", guide)
        self.assertIn("File.identical?", guide)
        self.assertIn("wrong-kind", guide)
        self.assertIn("workflow dispatcher", guide)
        self.assertIn("not treated as the operator", guide)
        self.assertIn(
            "separate `python`, `headless_ruby`, and `sketchup_runtime`", guide
        )


if __name__ == "__main__":
    unittest.main()
