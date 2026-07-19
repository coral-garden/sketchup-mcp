import hashlib
import json
from datetime import datetime, timedelta
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from extension_package import build_package  # noqa: E402
from sketchup_runtime_evidence import (  # noqa: E402
    ARTIFACT_NAMES,
    EVIDENCE_KIND,
    EvidenceError,
    RawArtifactPaths,
    collect_evidence,
    prepare_run,
    suite_sha256,
    validate_evidence,
)


COMMIT = "0123456789abcdef0123456789abcdef01234567"


class SketchupRuntimeEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package_directory = tempfile.TemporaryDirectory()
        cls.rbz = build_package(REPO_ROOT, Path(cls.package_directory.name)).path
        cls.rbz_bytes = cls.rbz.read_bytes()
        cls.package_manifest = cls.read_package_manifest(cls.rbz)

    @classmethod
    def tearDownClass(cls):
        cls.package_directory.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.context_path = prepare_run(
            repo_root=REPO_ROOT,
            artifact_dir=self.directory,
            rbz_path=self.rbz,
            commit=COMMIT,
            operator="Release Operator",
            licensed_runner_confirmed=True,
            single_testup_process_confirmed=True,
        )
        self.context = self.read_json(self.context_path)
        self.run_directory = self.context_path.parent
        self.scenarios = self.manifest()["scenarios"]
        self.generated_at = datetime.fromisoformat(self.context["created_at"]) + timedelta(
            seconds=10
        )
        self.write_raw_artifacts()

    def tearDown(self):
        self.temporary.cleanup()

    def test_file_reporter_replay_is_a_first_class_raw_artifact(self):
        self.assertIn("testup_replay", RawArtifactPaths.__dataclass_fields__)
        evidence = self.collect()
        replay = evidence["raw_artifacts"]["testup_replay"]

        self.assertEqual(self.path("testup_replay").name, replay["filename"])
        self.assertEqual(self.path("testup_replay").stat().st_size, replay["size"])
        self.assertRegex(replay["sha256"], r"^[0-9a-f]{64}$")

    def test_file_reporter_requires_exactly_one_log_and_one_replay(self):
        cases = (
            ("duplicate TestUp .log", self.path("testup_log"), "duplicate.log", b"duplicate"),
            ("duplicate TestUp .run", self.path("testup_replay"), "duplicate.run", b"{}"),
        )
        for message, _original, duplicate_name, contents in cases:
            with self.subTest(message=message):
                duplicate = self.path("testup_log").parent / duplicate_name
                duplicate.write_bytes(contents)
                with self.assertRaisesRegex(EvidenceError, message):
                    self.collect()
                duplicate.unlink()

        for message, original in (
            ("missing TestUp .log", self.path("testup_log")),
            ("missing TestUp .run", self.path("testup_replay")),
        ):
            with self.subTest(message=message):
                contents = original.read_bytes()
                original.unlink()
                with self.assertRaisesRegex(EvidenceError, message):
                    self.collect()
                original.write_bytes(contents)

    def test_file_reporter_class_filter_does_not_count_as_exact_requested_tests(self):
        replay = self.read_json(self.path("testup_replay"))
        replay["tests"] = ["TC_ProductionAdapter#"]
        self.write_json(self.path("testup_replay"), replay)

        with self.assertRaisesRegex(EvidenceError, "replay test inventory"):
            self.collect()

    def test_file_reporter_replay_rejects_missing_or_extra_requested_tests(self):
        replay = self.read_json(self.path("testup_replay"))
        exact_filters = list(replay["tests"])
        cases = {
            "missing": exact_filters[:-1],
            "extra": [*exact_filters, "TC_ProductionAdapter#test_not_requested"],
        }
        for name, filters in cases.items():
            with self.subTest(name=name):
                replay["tests"] = filters
                self.write_json(self.path("testup_replay"), replay)
                with self.assertRaisesRegex(EvidenceError, "replay test inventory"):
                    self.collect()

    def test_file_reporter_rejects_unexpected_artifact_roles(self):
        (self.path("testup_log").parent / "unexpected.txt").write_text(
            "not a FileReporter role", encoding="utf-8"
        )

        with self.assertRaisesRegex(EvidenceError, "unexpected TestUp FileReporter"):
            self.collect()

    def test_run_id_marker_binds_json_log_replay_and_runtime_artifacts(self):
        evidence = self.collect()
        marker = f"test_run_id_{self.context['run_id']}"

        self.assertIn(marker, evidence["testup"]["tests"])
        self.assertIn(marker, self.path("testup_log").read_text(encoding="utf-8"))
        self.assertIn(
            f"TC_ProductionAdapter#{marker}",
            self.read_json(self.path("testup_replay"))["tests"],
        )

    def test_stale_same_window_results_cannot_replace_the_dynamic_run_marker(self):
        results = self.make_testup_results()
        expected = f"test_run_id_{self.context['run_id']}"
        stale = f"test_run_id_{'f' * 64}"
        marker = next(item for item in results["passes"] if item["name"] == expected)
        marker["name"] = stale
        self.write_json(self.path("testup_results"), results)

        with self.assertRaisesRegex(EvidenceError, "pass names"):
            self.collect()

    def test_tampered_file_reporter_log_or_replay_cannot_be_attributed_to_the_run(self):
        marker = f"test_run_id_{self.context['run_id']}"
        log = self.path("testup_log")
        original_log = log.read_text(encoding="utf-8")
        log.write_text(original_log.replace(marker, f"test_run_id_{'e' * 64}"), encoding="utf-8")
        with self.assertRaisesRegex(EvidenceError, "FileReporter log test inventory"):
            self.collect()
        log.write_text(original_log, encoding="utf-8")

        replay = self.read_json(self.path("testup_replay"))
        replay["tests"] = [
            name.replace(marker, f"test_run_id_{'d' * 64}") for name in replay["tests"]
        ]
        self.write_json(self.path("testup_replay"), replay)
        with self.assertRaisesRegex(EvidenceError, "FileReporter replay test inventory"):
            self.collect()

    def test_collects_a_complete_same_run_with_exact_package_and_raw_hashes(self):
        evidence = self.collect()

        self.assertEqual(EVIDENCE_KIND, evidence["kind"])
        self.assertEqual(self.context["run_id"], evidence["run_id"])
        self.assertEqual("manual", evidence["attestation"]["type"])
        self.assertTrue(evidence["attestation"]["licensed_sketchup_confirmed"])
        self.assertEqual(self.package_manifest, evidence["source"]["installed_files"])
        self.assertEqual(self.expected_test_names(), evidence["testup"]["tests"])
        self.assertEqual(
            {
                "run_context",
                "testup_config",
                "testup_results",
                "testup_log",
                "testup_replay",
                "error_log",
                "runtime_report",
                "suite_marker",
            },
            set(evidence["raw_artifacts"]),
        )
        self.assertNotIn(str(self.directory), json.dumps(evidence))

        validate_evidence(
            evidence,
            repo_root=REPO_ROOT,
            raw_paths=self.raw_paths(),
            rbz_path=self.rbz,
            expected_commit=COMMIT,
        )

    def test_prepare_uses_random_ids_and_a_concrete_verbose_testup_config(self):
        second = self.directory / "second"
        other_context = prepare_run(
            repo_root=REPO_ROOT,
            artifact_dir=second,
            rbz_path=self.rbz,
            commit=COMMIT,
            operator="Other Operator",
            licensed_runner_confirmed=True,
            single_testup_process_confirmed=True,
        )
        other = self.read_json(other_context)
        config = self.path("testup_config").read_text(encoding="utf-8")

        self.assertNotEqual(self.context["run_id"], other["run_id"])
        self.assertRegex(self.context["run_id"], r"^[0-9a-f]{64}$")
        self.assertNotIn("%CONFIG_DIR%", config)
        self.assertIn(f'ErrorLogPath: {json.dumps(str(self.path("error_log").resolve()))}', config)
        self.assertIn("Verbose: true", config)
        self.assertIn("KeepOpen: false", config)
        self.assertIn(f"Seed: {self.context['seed']}", config)
        config_filters = [
            line.removeprefix("- ")
            for line in config.splitlines()[config.splitlines().index("Tests:") + 1 :]
        ]
        self.assertEqual(self.expected_test_filters(), config_filters)
        self.assertNotIn("TC_ProductionAdapter#", config_filters)
        self.assertEqual(
            hashlib.sha256(config.encode()).hexdigest(), self.context["config_sha256"]
        )

    def test_prepare_creates_a_unique_run_id_workspace_for_every_run(self):
        root = self.directory / "workspace-root"
        first = prepare_run(
            repo_root=REPO_ROOT,
            artifact_dir=root,
            rbz_path=self.rbz,
            commit=COMMIT,
            operator="Release Operator",
            licensed_runner_confirmed=True,
            single_testup_process_confirmed=True,
        )
        second = prepare_run(
            repo_root=REPO_ROOT,
            artifact_dir=root,
            rbz_path=self.rbz,
            commit=COMMIT,
            operator="Release Operator",
            licensed_runner_confirmed=True,
            single_testup_process_confirmed=True,
        )

        self.assertNotEqual(first.parent, second.parent)
        self.assertEqual(f"run-{self.read_json(first)['run_id']}", first.parent.name)
        self.assertEqual(f"run-{self.read_json(second)['run_id']}", second.parent.name)

    def test_run_context_must_keep_its_prepared_filename(self):
        renamed = self.run_directory / "renamed-context.json"
        self.context_path.rename(renamed)
        self.context_path = renamed

        with self.assertRaisesRegex(EvidenceError, "run context is outside"):
            self.collect()

    def test_context_records_the_trusted_operators_single_process_attestation(self):
        attestation = self.context["attestation"]

        self.assertTrue(attestation["licensed_sketchup_confirmed"])
        self.assertTrue(attestation["single_testup_process_confirmed"])

    def test_raw_artifacts_must_be_created_after_the_prepared_context_time(self):
        stale_time = datetime.fromisoformat(self.context["created_at"]).timestamp() - 60
        os.utime(self.path("testup_results"), (stale_time, stale_time))

        with self.assertRaisesRegex(EvidenceError, "predates the prepared run"):
            self.collect()

    def test_rejects_failed_skipped_or_incomplete_testup_runs(self):
        for field in ("failures", "errors", "skips"):
            with self.subTest(field=field):
                results = self.make_testup_results()
                results["statistics"][field] = 1
                results["status"]["code"] = "Failed"
                self.write_json(self.path("testup_results"), results)
                with self.assertRaisesRegex(EvidenceError, "status|" + field):
                    self.collect()
                self.write_json(self.path("testup_results"), self.make_testup_results())

        results = self.make_testup_results()
        results["statistics"]["total"] -= 1
        self.write_json(self.path("testup_results"), results)
        with self.assertRaisesRegex(EvidenceError, "counts"):
            self.collect()

    def test_rejects_non_verbose_or_wrongly_named_passes_including_final_test(self):
        results = self.make_testup_results()
        results["metadata"]["options"]["verbose"] = False
        self.write_json(self.path("testup_results"), results)
        with self.assertRaisesRegex(EvidenceError, "verbose"):
            self.collect()

        results = self.make_testup_results()
        results["passes"][-1]["name"] = "test_report_written_outside_lifecycle"
        self.write_json(self.path("testup_results"), results)
        with self.assertRaisesRegex(EvidenceError, "pass names"):
            self.collect()

    def test_rejects_mismatched_run_ids_and_incomplete_suite_markers(self):
        runtime = self.runtime_report()
        runtime["run_id"] = "f" * 64
        self.write_json(self.path("runtime_report"), runtime)
        with self.assertRaisesRegex(EvidenceError, "run ID"):
            self.collect()

        self.write_json(self.path("runtime_report"), self.runtime_report())
        marker = self.suite_marker()
        marker["scenarios"] = marker["scenarios"][:-1]
        self.write_json(self.path("suite_marker"), marker)
        with self.assertRaisesRegex(EvidenceError, "marker inventory"):
            self.collect()

    def test_rejects_unavailable_or_less_than_exact_coverage(self):
        mutations = (
            ("branch_supported", lambda report: report.update(branch_supported=False), "branch coverage"),
            ("line_percent", lambda report: report["coverage"]["lines"].update(percent=99.99), "line coverage"),
            ("branch_percent", lambda report: report["coverage"]["branches"].update(percent=99.99), "branch coverage"),
            ("missing_line", lambda report: report["coverage"]["lines"].update(missing=[9]), "missing lines"),
            ("missing_branch", lambda report: report["coverage"]["branches"].update(missing=["if@9:else"]), "missing branches"),
        )
        for name, mutation, message in mutations:
            with self.subTest(name=name):
                report = self.runtime_report()
                mutation(report)
                self.write_json(self.path("runtime_report"), report)
                with self.assertRaisesRegex(EvidenceError, message):
                    self.collect()
                self.write_json(self.path("runtime_report"), self.runtime_report())

    def test_rejects_every_form_of_installed_package_manifest_drift(self):
        cases = {
            "missing": lambda files: files.pop(next(iter(files))),
            "extra": lambda files: files.update({"su_mcp/stale.rb": "0" * 64}),
            "hash": lambda files: files.update({"su_mcp/sketchup_adapter.rb": "1" * 64}),
        }
        for name, mutation in cases.items():
            with self.subTest(name=name):
                report = self.runtime_report()
                mutation(report["installed_files"])
                self.write_json(self.path("runtime_report"), report)
                with self.assertRaisesRegex(EvidenceError, "installed extension manifest"):
                    self.collect()

    def test_rejects_a_non_deterministic_or_different_package(self):
        changed = self.directory / self.rbz.name
        changed.write_bytes(self.rbz_bytes + b"trailing-package-drift")

        with self.assertRaisesRegex(EvidenceError, "deterministic build"):
            collect_evidence(
                repo_root=REPO_ROOT,
                raw_paths=self.raw_paths(),
                rbz_path=changed,
                commit=COMMIT,
            )

    def test_validation_rehashes_every_explicit_raw_artifact(self):
        evidence = self.collect()
        log = self.path("testup_log")
        log.write_text(log.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        with self.assertRaisesRegex(EvidenceError, "raw artifacts"):
            validate_evidence(
                evidence,
                repo_root=REPO_ROOT,
                raw_paths=self.raw_paths(),
                rbz_path=self.rbz,
                expected_commit=COMMIT,
            )

    def test_validation_rehashes_the_file_reporter_replay(self):
        evidence = self.collect()
        replay = self.path("testup_replay")
        replay.write_text(replay.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        with self.assertRaisesRegex(EvidenceError, "raw artifacts"):
            validate_evidence(
                evidence,
                repo_root=REPO_ROOT,
                raw_paths=self.raw_paths(),
                rbz_path=self.rbz,
                expected_commit=COMMIT,
            )

    def test_rejects_nonempty_error_logs_without_copying_sensitive_content(self):
        secret = "private-user-path-and-runtime-backtrace"
        self.path("error_log").write_text(secret, encoding="utf-8")

        with self.assertRaises(EvidenceError) as raised:
            self.collect()

        self.assertEqual("TestUp error log is not empty", str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))

    def test_rejects_unsupported_runtime_and_catalog_or_suite_drift(self):
        cases = (
            ("operating system", lambda report: report.update(os_family="linux")),
            ("SketchUp", lambda report: report.update(sketchup_version="2023.1")),
            ("TestUp", lambda report: report.update(testup_version="2.5.3")),
            ("Ruby", lambda report: report.update(ruby_version="2.7.1")),
            ("command catalog", lambda report: report.update(commands=[])),
            ("suite hash", lambda report: report.update(suite_sha256="1" * 64)),
        )
        for message, mutation in cases:
            with self.subTest(message=message):
                report = self.runtime_report()
                mutation(report)
                self.write_json(self.path("runtime_report"), report)
                with self.assertRaisesRegex(EvidenceError, message):
                    self.collect()

    def test_suite_manifest_exactly_matches_test_methods_and_final_report_is_last(self):
        manifest = self.manifest()
        _, catalog_commands = self.catalog_identity()
        suite = (REPO_ROOT / "testup/production_adapter/TC_ProductionAdapter.rb").read_text(
            encoding="utf-8"
        )
        test_methods = re.findall(r"^  def test_([a-z0-9_]+)$", suite, re.MULTILINE)

        self.assertEqual(catalog_commands, manifest["commands"])
        self.assertEqual(test_methods, manifest["scenarios"])
        self.assertEqual(sorted(test_methods), test_methods)
        self.assertEqual("zz_write_runtime_report", test_methods[-1])
        self.assertEqual(22, len(test_methods))
        self.assertIn(
            "define_method(SketchupMcpTestUp.run_marker_test_name)", suite
        )
        self.assertNotIn("Minitest.after_run", suite)
        self.assertNotIn(
            "Minitest.after_run",
            (REPO_ROOT / "testup/production_adapter/support.rb").read_text(encoding="utf-8"),
        )

    def test_loader_starts_line_and_branch_coverage_only_for_explicit_testup_runs(self):
        loader = (REPO_ROOT / "su_mcp.rb").read_text(encoding="utf-8")

        self.assertIn("SKETCHUP_MCP_TESTUP_COVERAGE", loader)
        self.assertIn("Coverage.start(lines: true, branches: true)", loader)
        self.assertLess(loader.index("Coverage.start"), loader.index("SketchupExtension.new"))

    def test_evidence_schema_names_attestation_package_tests_and_raw_artifacts(self):
        schema = self.read_json(
            REPO_ROOT / "testup/production_adapter/evidence.schema.json"
        )

        self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
        self.assertEqual(3, schema["properties"]["schema_version"]["const"])
        self.assertIn("attestation", schema["required"])
        self.assertIn("raw_artifacts", schema["required"])
        self.assertIn("installed_files", schema["properties"]["source"]["required"])
        self.assertIn("tests", schema["properties"]["testup"]["required"])
        self.assertIn(
            "single_testup_process_confirmed",
            schema["properties"]["attestation"]["required"],
        )
        self.assertIn(
            "testup_replay", schema["properties"]["raw_artifacts"]["required"]
        )
        self.assertIn("size", schema["$defs"]["rawArtifact"]["required"])

    def test_runtime_workflow_documents_both_platforms_and_security_limits(self):
        workflow = (REPO_ROOT / "docs/testing/sketchup-testup.md").read_text(
            encoding="utf-8"
        )

        for phrase in (
            "Windows 11",
            "macOS",
            "TestUp 2.5.4",
            "SketchUp 2024",
            "prepare",
            "SKETCHUP_MCP_TESTUP_RUN_ID",
            "--suite-marker",
            "--testup-log",
            "manual attestation",
            "does not cryptographically prove",
            "does not satisfy issue #15",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, workflow)
        self.assertNotIn("same-OS-user", workflow)
        self.assertNotIn("same user", workflow.lower())

    def test_windows_workflow_waits_then_selects_both_file_reporter_roles(self):
        workflow = (REPO_ROOT / "docs/testing/sketchup-testup.md").read_text(
            encoding="utf-8"
        )
        windows = workflow.split("## Run on Windows 11", 1)[1].split(
            "## Run on macOS", 1
        )[0]

        self.assertIn("Start-Process", windows)
        self.assertRegex(windows, r"(?s)Start-Process.+-Wait")
        self.assertLess(windows.index("-Wait"), windows.index("Get-ChildItem"))
        self.assertIn("$logs.Count -ne 1", windows)
        self.assertIn("$replays.Count -ne 1", windows)
        self.assertIn("$unexpected.Count -ne 0", windows)
        self.assertIn("--testup-replay", windows)

    def collect(self):
        return collect_evidence(
            repo_root=REPO_ROOT,
            raw_paths=self.raw_paths(),
            rbz_path=self.rbz,
            commit=COMMIT,
        )

    def write_raw_artifacts(self):
        self.write_json(self.path("runtime_report"), self.runtime_report())
        self.write_json(self.path("suite_marker"), self.suite_marker())
        self.write_json(self.path("testup_results"), self.make_testup_results())
        self.path("testup_log").parent.mkdir(parents=True, exist_ok=True)
        self.path("testup_log").write_text(
            "\n".join(
                f"TC_ProductionAdapter#{name} = 0.01 s = ."
                for name in self.expected_test_names()
            )
            + "\n",
            encoding="utf-8",
        )
        self.write_json(
            self.path("testup_replay"),
            {
                "test_suite": "production_adapter",
                "path": str((REPO_ROOT / "testup/production_adapter").resolve()),
                "seed": self.context["seed"],
                "tests": sorted(
                    f"TC_ProductionAdapter#{name}" for name in self.expected_test_names()
                ),
            },
        )

    def runtime_report(self):
        catalog_sha, commands = self.catalog_identity()
        return {
            "schema_version": 3,
            "run_id": self.context["run_id"],
            "generated_at": self.generated_at.isoformat(),
            "branch_supported": True,
            "expected_test_count": len(self.expected_test_names()),
            "suite_sha256": suite_sha256(REPO_ROOT / "testup/production_adapter"),
            "catalog_sha256": catalog_sha,
            "commands": commands,
            "project_version": (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip(),
            "commit": COMMIT,
            "installed_files": dict(self.package_manifest),
            "os_family": "windows",
            "os_version": "11",
            "architecture": "x64",
            "sketchup_version": "2024.0.553",
            "testup_version": "2.5.4",
            "ruby_version": "3.2.2",
            "ruby_platform": "x64-mingw-ucrt",
            "coverage": {
                "engine": "ruby Coverage",
                "scope": ["su_mcp/sketchup_adapter.rb"],
                "source_sha256": self.package_manifest["su_mcp/sketchup_adapter.rb"],
                "lines": {"covered": 100, "total": 100, "percent": 100.0, "missing": []},
                "branches": {"covered": 40, "total": 40, "percent": 100.0, "missing": []},
            },
        }

    def suite_marker(self):
        return {
            "schema_version": 2,
            "run_id": self.context["run_id"],
            "generated_at": self.generated_at.isoformat(),
            "test_class": "TC_ProductionAdapter",
            "scenarios": self.scenarios,
            "run_marker_test": f"test_run_id_{self.context['run_id']}",
        }

    def make_testup_results(self):
        names = self.expected_test_names()
        return {
            "status": {"code": "Success", "color": "green"},
            "statistics": {
                "total": len(names),
                "assertions": 150,
                "failures": 0,
                "errors": 0,
                "skips": 0,
                "passes": len(names),
            },
            "metadata": {
                "generated_by": "TestUp::CIJsonReporter",
                "ruby_version": "3.2.2",
                "time": (self.generated_at + timedelta(seconds=10)).isoformat(),
                "options": {"verbose": True, "seed": self.context["seed"]},
            },
            "fails": [],
            "skips": [],
            "passes": [
                {
                    "type": "passed",
                    "class": "TC_ProductionAdapter",
                    "name": name,
                    "assertions": 1,
                    "time": 0.1,
                }
                for name in names
            ],
        }

    def expected_test_names(self):
        return sorted(
            [
                *(f"test_{scenario}" for scenario in self.scenarios),
                f"test_run_id_{self.context['run_id']}",
            ]
        )

    def expected_test_filters(self):
        return [f"TC_ProductionAdapter#{name}" for name in self.expected_test_names()]

    def raw_paths(self):
        return RawArtifactPaths(
            run_context=self.context_path,
            testup_config=self.path("testup_config"),
            testup_results=self.path("testup_results"),
            testup_log=self.path("testup_log"),
            testup_replay=self.path("testup_replay"),
            error_log=self.path("error_log"),
            runtime_report=self.path("runtime_report"),
            suite_marker=self.path("suite_marker"),
        )

    def path(self, name):
        if name in {"testup_log", "testup_replay"}:
            suffix = ".log" if name == "testup_log" else ".run"
            return self.run_directory / ARTIFACT_NAMES["log_directory"] / f"testup{suffix}"
        return self.run_directory / ARTIFACT_NAMES[name]

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    @staticmethod
    def read_json(path):
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def manifest():
        return json.loads(
            (REPO_ROOT / "testup/production_adapter/suite_manifest.json").read_text(
                encoding="utf-8"
            )
        )

    @staticmethod
    def catalog_identity():
        contents = (REPO_ROOT / "src/sketchup_mcp/command_catalog.json").read_bytes()
        catalog = json.loads(contents)
        return (
            hashlib.sha256(contents).hexdigest(),
            [command["name"] for command in catalog["commands"]],
        )

    @staticmethod
    def read_package_manifest(path):
        with zipfile.ZipFile(path) as archive:
            return {
                name: hashlib.sha256(archive.read(name)).hexdigest()
                for name in sorted(archive.namelist())
            }


if __name__ == "__main__":
    unittest.main()
