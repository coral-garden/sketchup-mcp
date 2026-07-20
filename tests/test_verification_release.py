"""Release verification trust and evidence contract tests."""

import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
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
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify.py"


def load_verifier():
    spec = importlib.util.spec_from_file_location("verification_cli", VERIFY_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("verification CLI could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseVerificationTest(unittest.TestCase):
    COMMIT = "a" * 40
    RUN_ID = 123456
    EVIDENCE_RUN_ID = "b" * 64
    NOW = datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "trusted-runtime"
        self.run = self.root / f"run-{self.EVIDENCE_RUN_ID}"
        (self.run / "logs").mkdir(parents=True)
        self.version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.rbz_name = f"sketchup-mcp-{self.version}.rbz"
        rbz_path = self.run / self.rbz_name
        package_files = {
            "su_mcp.rb": b"candidate loader\n",
            "su_mcp/sketchup_adapter.rb": b"candidate adapter\n",
        }
        with zipfile.ZipFile(rbz_path, "w") as archive:
            for name, contents in package_files.items():
                archive.writestr(name, contents)
        self.rbz_contents = rbz_path.read_bytes()
        self.package_manifest = {
            name: hashlib.sha256(contents).hexdigest()
            for name, contents in package_files.items()
        }
        config = "generated: true\n"
        context = {
            "schema_version": 2,
            "run_id": self.EVIDENCE_RUN_ID,
            "created_at": self._timestamp(),
            "commit": self.COMMIT,
            "attestation": {
                "type": "manual",
                "operator": "Ada Example",
                "licensed_sketchup_confirmed": True,
                "single_testup_process_confirmed": True,
            },
            "rbz": {
                "filename": self.rbz_name,
                "sha256": hashlib.sha256(self.rbz_contents).hexdigest(),
            },
            "config_sha256": hashlib.sha256(config.encode()).hexdigest(),
            "seed": 7,
            "artifacts": {
                "testup_config": "testup-ci.generated.yml",
                "testup_results": "testup-results.json",
                "error_log": "testup-error.log",
                "runtime_report": "runtime-report.json",
                "suite_marker": "suite-marker.json",
                "log_directory": "logs",
            },
        }
        for name, contents in {
            "run-context.json": json.dumps(context),
            "testup-ci.generated.yml": config,
            "testup-results.json": "{}",
            "testup-error.log": "",
            "runtime-report.json": "{}",
            "suite-marker.json": "{}",
            "evidence.json": json.dumps(
                {
                    "run_id": self.EVIDENCE_RUN_ID,
                    "created_at": self._timestamp(),
                    "coverage": {
                        "engine": "ruby Coverage",
                        "scope": ["su_mcp/sketchup_adapter.rb"],
                        "lines": {
                            "covered": 201,
                            "total": 201,
                            "percent": 100.0,
                            "missing": [],
                        },
                        "branches": {
                            "covered": 81,
                            "total": 81,
                            "percent": 100.0,
                            "missing": [],
                        },
                    },
                }
            ),
        }.items():
            (self.run / name).write_text(contents, encoding="utf-8")
        (self.run / "logs" / "testup.log").write_text("pass", encoding="utf-8")
        (self.run / "logs" / "testup.run").write_text("{}", encoding="utf-8")
        receipt = {
            "schema_version": 1,
            "kind": "sketchup_mcp.candidate_preclean",
            "created_at": self._timestamp(),
            "commit": self.COMMIT,
            "run_id": self.EVIDENCE_RUN_ID,
            "operator": "Ada Example",
            "dispatcher": "ada-login",
            "candidate_install_confirmed": True,
            "cleanup_targets": ["su_mcp.rb", "su_mcp/"],
            "removed_targets": ["su_mcp.rb", "su_mcp/"],
            "plugins_sentinel_sha256": "c" * 64,
        }
        receipt_path = self.run / "candidate-preclean.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        bootstrap_input = self.run / "candidate-install-input.json"
        bootstrap_input.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "sketchup_mcp.candidate_install_bootstrap",
                    "identity": {
                        "schema_version": 1,
                        "kind": "sketchup_mcp.candidate_install",
                        "commit": self.COMMIT,
                        "run_id": self.EVIDENCE_RUN_ID,
                        "version": self.version,
                        "operator": "Ada Example",
                        "dispatcher": "ada-login",
                        "candidate_install_confirmed": True,
                        "installed_files": self.package_manifest,
                    },
                    "rbz_path": str(rbz_path),
                    "plugins_dir": "/protected/Plugins",
                    "rbz": {
                        "filename": self.rbz_name,
                        "sha256": hashlib.sha256(self.rbz_contents).hexdigest(),
                        "size": len(self.rbz_contents),
                    },
                    "preclean": {
                        "filename": "candidate-preclean.json",
                        "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                        "size": len(receipt_path.read_bytes()),
                    },
                }
            ),
            encoding="utf-8",
        )
        bootstrap = self.run / "candidate-install.rb"
        bootstrap.write_text("# generated bootstrap\n", encoding="utf-8")
        install_log = self.run / "candidate-install.log"
        install_log.write_text(
            "install_from_archive:success\n"
            "installed_manifest:verified\n"
            "candidate_entrypoint:loaded\n"
            "candidate_install:success\n",
            encoding="utf-8",
        )
        marker = {
            "schema_version": 1,
            "kind": "sketchup_mcp.candidate_install",
            "status": "success",
            "created_at": self._timestamp(),
            "commit": self.COMMIT,
            "run_id": self.EVIDENCE_RUN_ID,
            "version": self.version,
            "operator": "Ada Example",
            "dispatcher": "ada-login",
            "candidate_install_confirmed": True,
            "rbz": {
                "filename": self.rbz_name,
                "sha256": hashlib.sha256(self.rbz_contents).hexdigest(),
                "size": len(self.rbz_contents),
            },
            "installed_files": self.package_manifest,
            "preclean_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            "bootstrap_sha256": hashlib.sha256(bootstrap.read_bytes()).hexdigest(),
            "bootstrap_input": {
                "filename": "candidate-install-input.json",
                "sha256": hashlib.sha256(bootstrap_input.read_bytes()).hexdigest(),
                "size": len(bootstrap_input.read_bytes()),
            },
            "install_log_sha256": hashlib.sha256(install_log.read_bytes()).hexdigest(),
            "loaded_adapter_sha256": self.package_manifest[
                "su_mcp/sketchup_adapter.rb"
            ],
        }
        (self.run / "candidate-install.json").write_text(
            json.dumps(marker), encoding="utf-8"
        )
        self.wheel_name = f"sketchup_mcp-{self.version}-py3-none-any.whl"
        self.sdist_name = f"sketchup_mcp-{self.version}.tar.gz"
        (self.run / self.wheel_name).write_bytes(b"retained wheel")
        (self.run / self.sdist_name).write_bytes(b"retained sdist")
        acceptance = self.run / "install-acceptance"
        acceptance.mkdir()
        (acceptance / "evidence.json").write_text("{}", encoding="utf-8")
        self.metadata = self._metadata()
        self._write_metadata()

    def _timestamp(self, *, hours_ago=1):
        return (self.NOW - timedelta(hours=hours_ago)).isoformat().replace(
            "+00:00", "Z"
        )

    def _metadata(self):
        return {
            "schema_version": 1,
            "run": {
                "id": self.RUN_ID,
                "name": "SketchUp Runtime Evidence",
                "path": ".github/workflows/sketchup-runtime.yml",
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "success",
                "head_sha": self.COMMIT,
                "created_at": self._timestamp(),
                "updated_at": self._timestamp(),
                "repository": {"full_name": "coral-garden/sketchup-mcp"},
                "actor": {"login": "ada-login"},
            },
            "artifact": {
                "id": 987,
                "name": f"sketchup-runtime-evidence-{self.RUN_ID}",
                "expired": False,
                "created_at": self._timestamp(),
                "updated_at": self._timestamp(),
                "workflow_run": {"id": self.RUN_ID, "head_sha": self.COMMIT},
            },
        }

    def _write_metadata(self):
        (self.root / "github-run.json").write_text(
            json.dumps(self.metadata), encoding="utf-8"
        )

    def _arguments(self):
        return [
            "release",
            "--runtime-root",
            str(self.root),
            "--runtime-run-id",
            str(self.RUN_ID),
            "--report",
            str(Path(self.temporary.name) / "release.json"),
        ]

    def _successful_subprocess(self, command, **_options):
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, self.COMMIT + "\n", "")
        if command[:2] == [sys.executable, str(REPO_ROOT / "scripts/build.py")]:
            destination = Path(command[command.index("--output-dir") + 1])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / self.rbz_name).write_bytes(self.rbz_contents)
        return subprocess.CompletedProcess(command, 0, "", "")

    def _pass_local(self, report):
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "mode": "local",
                    "status": "pass",
                    "scopes": {
                        "python": {
                            "status": "pass",
                            "coverage": {
                                "thresholds": {"lines": 100, "branches": 100},
                                "lines": {"covered": 590, "total": 590},
                                "branches": {"covered": 154, "total": 154},
                            },
                        },
                        "headless_ruby": {
                            "status": "pass",
                            "coverage": {
                                "thresholds": {"lines": 100, "branches": 100},
                                "lines": {"covered": 537, "total": 537},
                                "branches": {"covered": 175, "total": 175},
                            },
                        },
                        "sketchup_runtime": {
                            "status": "external",
                            "required": False,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        return 0

    def _run_release(self, verifier):
        output = io.StringIO()

        with mock.patch.object(verifier, "verify_local", side_effect=self._pass_local):
            with mock.patch.object(verifier, "_utc_now", return_value=self.NOW):
                with mock.patch.object(
                    verifier.subprocess,
                    "run",
                    side_effect=self._successful_subprocess,
                ) as run:
                    with contextlib.redirect_stdout(output):
                        status = verifier.main(self._arguments())
        return status, output.getvalue(), run.call_args_list

    def test_release_runs_local_rebuild_and_public_runtime_evidence_validator(self):
        verifier = load_verifier()

        status, output, calls = self._run_release(verifier)

        self.assertEqual(0, status, output)
        commands = [call.args[0] for call in calls]
        self.assertIn(["git", "rev-parse", "HEAD"], commands)
        build = next(command for command in commands if "scripts/build.py" in command[1])
        self.assertIn("--output-dir", build)
        validate = next(
            command
            for command in commands
            if "sketchup_runtime_evidence.py" in command[1]
        )
        self.assertEqual(sys.executable, validate[0])
        self.assertEqual("validate", validate[2])
        self.assertEqual(self.COMMIT, validate[validate.index("--commit") + 1])
        self.assertEqual(
            str(self.run / "evidence.json"),
            validate[validate.index("--evidence") + 1],
        )
        for option in (
            "--run-context",
            "--testup-config",
            "--testup-results",
            "--testup-log",
            "--testup-replay",
            "--error-log",
            "--runtime-report",
            "--suite-marker",
        ):
            self.assertIn(option, validate)
        install_validate = next(
            command for command in commands if "install_acceptance.py" in command[1]
        )
        self.assertEqual("validate", install_validate[2])
        self.assertEqual(
            str(self.run / "install-acceptance" / "evidence.json"),
            install_validate[install_validate.index("--evidence") + 1],
        )
        self.assertEqual(
            str(self.run / self.wheel_name),
            install_validate[install_validate.index("--wheel") + 1],
        )
        self.assertEqual("ada-login", install_validate[install_validate.index("--dispatcher") + 1])
        self.assertEqual(
            str(self.RUN_ID),
            install_validate[install_validate.index("--github-run-id") + 1],
        )
        self.assertIn("Install acceptance: PASS", output)
        self.assertIn("SketchUp runtime: PASS", output)
        self.assertIn("Release verification: PASS", output)

        report = json.loads(Path(self._arguments()[-1]).read_text(encoding="utf-8"))
        self.assertEqual("release", report["mode"])
        self.assertEqual("pass", report["status"])
        self.assertEqual(self.COMMIT, report["commit"])
        self.assertEqual(self.RUN_ID, report["runtime_workflow_run_id"])
        self.assertEqual(
            {
                "status": "pass",
                "coverage": {
                    "thresholds": {"lines": 100, "branches": 100},
                    "lines": {"covered": 590, "total": 590},
                    "branches": {"covered": 154, "total": 154},
                },
            },
            report["scopes"]["python"],
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
            report["scopes"]["headless_ruby"],
        )
        self.assertEqual("pass", report["scopes"]["sketchup_runtime"]["status"])
        self.assertEqual("pass", report["scopes"]["install_acceptance"]["status"])
        self.assertEqual(
            {"covered": 201, "total": 201},
            report["scopes"]["sketchup_runtime"]["coverage"]["lines"],
        )

    def test_release_fails_closed_for_untrusted_github_run_metadata(self):
        mutations = {
            "wrong schema": lambda value: value.update(schema_version=2),
            "wrong repository": lambda value: value["run"]["repository"].update(
                full_name="attacker/fork"
            ),
            "invalid dispatcher": lambda value: value["run"]["actor"].update(
                login="attacker/name"
            ),
            "wrong workflow name": lambda value: value["run"].update(name="Release"),
            "wrong workflow path": lambda value: value["run"].update(
                path=".github/workflows/other.yml"
            ),
            "wrong event": lambda value: value["run"].update(event="pull_request_target"),
            "incomplete run": lambda value: value["run"].update(status="in_progress"),
            "failed run": lambda value: value["run"].update(conclusion="failure"),
            "wrong SHA": lambda value: value["run"].update(head_sha="c" * 40),
            "wrong run ID": lambda value: value["run"].update(id=self.RUN_ID + 1),
            "wrong artifact": lambda value: value["artifact"].update(name="evidence"),
            "expired artifact": lambda value: value["artifact"].update(expired=True),
            "wrong artifact run": lambda value: value["artifact"]["workflow_run"].update(
                id=self.RUN_ID + 1
            ),
            "wrong artifact SHA": lambda value: value["artifact"]["workflow_run"].update(
                head_sha="c" * 40
            ),
            "expired run": lambda value: value["run"].update(
                updated_at=self._timestamp(hours_ago=25)
            ),
            "expired artifact timestamp": lambda value: value["artifact"].update(
                updated_at=self._timestamp(hours_ago=25)
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                self.metadata = self._metadata()
                mutate(self.metadata)
                self._write_metadata()
                verifier = load_verifier()

                status, output, calls = self._run_release(verifier)

                self.assertEqual(1, status, output)
                self.assertIn("Trusted GitHub runtime run: FAIL", output)
                self.assertFalse(
                    any(
                        "sketchup_runtime_evidence.py" in call.args[0][1]
                        for call in calls
                        if len(call.args[0]) > 1
                    )
                )

    def test_release_rejects_missing_or_malformed_github_run_metadata(self):
        manifest = self.root / "github-run.json"
        for label, contents in (("missing", None), ("malformed", "not-json")):
            with self.subTest(case=label):
                if contents is None:
                    manifest.unlink(missing_ok=True)
                else:
                    manifest.write_text(contents, encoding="utf-8")
                verifier = load_verifier()

                status, output, calls = self._run_release(verifier)

                self.assertEqual(1, status, output)
                self.assertIn("Trusted GitHub runtime run: FAIL", output)
                self.assertFalse(
                    any(
                        "sketchup_runtime_evidence.py" in call.args[0][1]
                        for call in calls
                        if len(call.args[0]) > 1
                    )
                )

    def test_release_reports_a_missing_raw_artifact_without_crashing(self):
        (self.run / "logs" / "testup.log").unlink()
        verifier = load_verifier()

        status, output, calls = self._run_release(verifier)

        self.assertEqual(1, status, output)
        self.assertIn("SketchUp runtime evidence: FAIL", output)
        self.assertIn("missing or duplicate TestUp .log artifact", output)
        self.assertFalse(
            any(
                "sketchup_runtime_evidence.py" in call.args[0][1]
                for call in calls
                if len(call.args[0]) > 1
            )
        )

    def test_release_rejects_missing_malformed_or_stale_runtime_evidence(self):
        cases = {
            "missing": None,
            "malformed": "not-json",
            "expired": json.dumps(
                {
                    "run_id": self.EVIDENCE_RUN_ID,
                    "created_at": self._timestamp(hours_ago=25),
                }
            ),
            "wrong workspace": json.dumps(
                {"run_id": "c" * 64, "created_at": self._timestamp()}
            ),
        }
        evidence = self.run / "evidence.json"
        for label, contents in cases.items():
            with self.subTest(case=label):
                if contents is None:
                    evidence.unlink(missing_ok=True)
                else:
                    evidence.write_text(contents, encoding="utf-8")
                verifier = load_verifier()

                status, output, _calls = self._run_release(verifier)

                self.assertEqual(1, status, output)
                self.assertIn("SketchUp runtime evidence: FAIL", output)

    def test_release_rejects_a_package_other_than_the_deterministic_rebuild(self):
        (self.run / self.rbz_name).write_bytes(b"different-rbz")
        verifier = load_verifier()

        status, output, calls = self._run_release(verifier)

        self.assertEqual(1, status, output)
        self.assertIn("RBZ", output)
        self.assertFalse(
            any(
                "sketchup_runtime_evidence.py" in call.args[0][1]
                for call in calls
                if len(call.args[0]) > 1
            )
        )

    def test_release_rejects_missing_or_mismatched_candidate_install_proof(self):
        marker_path = self.run / "candidate-install.json"
        original = marker_path.read_text(encoding="utf-8")
        cases = {
            "missing": None,
            "failed": lambda value: value.update(status="failure"),
            "wrong dispatcher": lambda value: value.update(dispatcher="other-login"),
            "wrong installed package": lambda value: value["installed_files"].update(
                {"su_mcp/sketchup_adapter.rb": "0" * 64}
            ),
            "wrong bootstrap": lambda value: value.update(bootstrap_sha256="0" * 64),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                if mutate is None:
                    marker_path.unlink(missing_ok=True)
                else:
                    marker = json.loads(original)
                    mutate(marker)
                    marker_path.write_text(json.dumps(marker), encoding="utf-8")
                verifier = load_verifier()

                status, output, calls = self._run_release(verifier)

                self.assertEqual(1, status, output)
                self.assertIn("candidate installation", output.lower())
                self.assertFalse(
                    any(
                        "sketchup_runtime_evidence.py" in call.args[0][1]
                        for call in calls
                        if len(call.args[0]) > 1
                    )
                )
                marker_path.write_text(original, encoding="utf-8")

    def test_release_rejects_duplicate_candidate_preclean_targets(self):
        receipt_path = self.run / "candidate-preclean.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["removed_targets"] = ["su_mcp.rb", "su_mcp.rb"]
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        input_path = self.run / "candidate-install-input.json"
        bootstrap_input = json.loads(input_path.read_text(encoding="utf-8"))
        bootstrap_input["preclean"] = {
            "filename": "candidate-preclean.json",
            "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            "size": len(receipt_path.read_bytes()),
        }
        input_path.write_text(json.dumps(bootstrap_input), encoding="utf-8")
        marker_path = self.run / "candidate-install.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["preclean_sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        marker["bootstrap_input"] = {
            "filename": "candidate-install-input.json",
            "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "size": len(input_path.read_bytes()),
        }
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        verifier = load_verifier()

        status, output, calls = self._run_release(verifier)

        self.assertEqual(1, status, output)
        self.assertIn("removed targets", output)
        self.assertFalse(
            any(
                "sketchup_runtime_evidence.py" in call.args[0][1]
                for call in calls
                if len(call.args[0]) > 1
            )
        )

    def test_release_revalidates_the_hash_size_and_contents_of_bootstrap_input(self):
        input_path = self.run / "candidate-install-input.json"
        marker_path = self.run / "candidate-install.json"
        original_input_text = input_path.read_text(encoding="utf-8")
        original_input = json.loads(original_input_text)
        original_marker = marker_path.read_text(encoding="utf-8")
        cases = ("unbound bytes", "self-consistent wrong identity")

        for case in cases:
            with self.subTest(case=case):
                document = json.loads(json.dumps(original_input))
                document["identity"]["operator"] = "Mallory Example"
                input_path.write_text(json.dumps(document), encoding="utf-8")
                if case == "self-consistent wrong identity":
                    marker = json.loads(marker_path.read_text(encoding="utf-8"))
                    marker["bootstrap_input"] = {
                        "filename": "candidate-install-input.json",
                        "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
                        "size": len(input_path.read_bytes()),
                    }
                    marker_path.write_text(json.dumps(marker), encoding="utf-8")
                verifier = load_verifier()

                status, output, calls = self._run_release(verifier)

                self.assertEqual(1, status, output)
                self.assertIn("bootstrap input", output.lower())
                self.assertFalse(
                    any(
                        "sketchup_runtime_evidence.py" in call.args[0][1]
                        for call in calls
                        if len(call.args[0]) > 1
                    )
                )
                input_path.write_text(original_input_text, encoding="utf-8")
                marker_path.write_text(original_marker, encoding="utf-8")

    def test_release_rejects_a_missing_or_failed_public_evidence_validator(self):
        for script_name in ("sketchup_runtime_evidence.py", "install_acceptance.py"):
            for failure in (FileNotFoundError(2, "missing"), 9):
                with self.subTest(script=script_name, failure=failure):
                    verifier = load_verifier()

                    def fail_validator(command, **options):
                        if len(command) > 1 and script_name in command[1]:
                            if isinstance(failure, BaseException):
                                raise failure
                            return subprocess.CompletedProcess(command, failure, "", "")
                        return self._successful_subprocess(command, **options)

                    output = io.StringIO()
                    with mock.patch.object(
                        verifier, "verify_local", side_effect=self._pass_local
                    ):
                        with mock.patch.object(verifier, "_utc_now", return_value=self.NOW):
                            with mock.patch.object(
                                verifier.subprocess, "run", side_effect=fail_validator
                            ):
                                with contextlib.redirect_stdout(output):
                                    status = verifier.main(self._arguments())

                    self.assertEqual(1, status)
                    self.assertIn("FAIL", output.getvalue())

    def test_release_rejects_missing_install_acceptance_artifacts(self):
        (self.run / "install-acceptance" / "evidence.json").unlink()
        verifier = load_verifier()

        status, output, calls = self._run_release(verifier)

        self.assertEqual(1, status)
        self.assertIn("install acceptance", output.lower())
        self.assertFalse(
            any("install_acceptance.py" in call.args[0][1] for call in calls if len(call.args[0]) > 1)
        )

    def test_install_acceptance_failure_preserves_the_passing_runtime_scope(self):
        verifier = load_verifier()

        def fail_install_acceptance(command, **options):
            if len(command) > 1 and "install_acceptance.py" in command[1]:
                return subprocess.CompletedProcess(command, 9, "", "")
            return self._successful_subprocess(command, **options)

        output = io.StringIO()
        with mock.patch.object(verifier, "verify_local", side_effect=self._pass_local):
            with mock.patch.object(verifier, "_utc_now", return_value=self.NOW):
                with mock.patch.object(
                    verifier.subprocess,
                    "run",
                    side_effect=fail_install_acceptance,
                ):
                    with contextlib.redirect_stdout(output):
                        status = verifier.main(self._arguments())

        report = json.loads(Path(self._arguments()[-1]).read_text(encoding="utf-8"))
        self.assertEqual(1, status)
        self.assertEqual("pass", report["scopes"]["sketchup_runtime"]["status"])
        self.assertNotIn("reason", report["scopes"]["sketchup_runtime"])
        self.assertEqual("fail", report["scopes"]["install_acceptance"]["status"])
        self.assertIn("validator failed", report["scopes"]["install_acceptance"]["reason"])
        self.assertIn("SketchUp runtime: PASS", output.getvalue())

    def test_release_rejects_missing_or_incomplete_local_scope_metrics(self):
        verifier = load_verifier()
        cases = (
            ("missing", None),
            (
                "collapsed",
                {
                    "schema_version": 1,
                    "mode": "local",
                    "status": "pass",
                    "scopes": {"python_and_headless_ruby": {"status": "pass"}},
                },
            ),
        )
        for label, document in cases:
            with self.subTest(case=label):
                def incomplete_local(report):
                    report.unlink(missing_ok=True)
                    if document is not None:
                        report.parent.mkdir(parents=True, exist_ok=True)
                        report.write_text(json.dumps(document), encoding="utf-8")
                    return 0

                output = io.StringIO()
                with mock.patch.object(
                    verifier, "verify_local", side_effect=incomplete_local
                ):
                    with mock.patch.object(verifier.subprocess, "run") as run:
                        with contextlib.redirect_stdout(output):
                            status = verifier.main(self._arguments())

                self.assertEqual(1, status, output.getvalue())
                self.assertIn("Local verification report: FAIL", output.getvalue())
                run.assert_not_called()
                release = json.loads(
                    Path(self._arguments()[-1]).read_text(encoding="utf-8")
                )
                self.assertEqual({"status": "fail", "coverage": None}, release["scopes"]["python"])
                self.assertEqual(
                    {"status": "fail", "coverage": None},
                    release["scopes"]["headless_ruby"],
                )

    def test_release_run_id_is_strictly_numeric_and_cannot_inject_paths_or_urls(self):
        verifier = load_verifier()
        for value in ("0", "-1", "12/../../etc", "https://example.test/12", "12;echo"):
            with self.subTest(value=value):
                with self.assertRaises(SystemExit):
                    verifier._parser().parse_args(
                        [
                            "release",
                            "--runtime-root",
                            str(self.root),
                            "--runtime-run-id",
                            value,
                        ]
                    )

if __name__ == "__main__":
    unittest.main()
