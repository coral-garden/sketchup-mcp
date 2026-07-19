#!/usr/bin/env python3
"""Prepare, collect, and validate licensed SketchUp/TestUp runtime evidence."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
from typing import Any, Mapping
import zipfile

from extension_package import PackageError, build_package, check_package


REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_KIND = "sketchup_mcp.production_adapter_testup"
SCHEMA_VERSION = 3
CONTEXT_SCHEMA_VERSION = 2
MARKER_SCHEMA_VERSION = 2
SUPPORTED_OS_FAMILIES = frozenset({"windows", "macos"})
SUPPORTED_TESTUP_VERSION = "2.5.4"
COVERAGE_SCOPE = ("su_mcp/sketchup_adapter.rb",)
TEST_CLASS = "TC_ProductionAdapter"
RUN_MARKER_PREFIX = "test_run_id_"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
ARTIFACT_NAMES = {
    "testup_config": "testup-ci.generated.yml",
    "testup_results": "testup-results.json",
    "error_log": "testup-error.log",
    "runtime_report": "runtime-report.json",
    "suite_marker": "suite-marker.json",
    "log_directory": "logs",
}
RAW_ARTIFACT_KEYS = (
    "run_context",
    "testup_config",
    "testup_results",
    "testup_log",
    "testup_replay",
    "error_log",
    "runtime_report",
    "suite_marker",
)


class EvidenceError(ValueError):
    """Runtime evidence cannot satisfy the production-adapter acceptance gate."""


def _sha256(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _regular_bytes(path: Path, label: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise EvidenceError(f"{label} must be a regular file")
        return path.read_bytes()
    except OSError as error:
        raise EvidenceError(f"{label} is unreadable") from error


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_regular_bytes(path, label))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be a JSON object")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{label} is missing")
    return value


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise EvidenceError(f"{label} is invalid")
    return value


def _timestamp(value: object, label: str) -> datetime:
    text = _require_string(value, f"{label} timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceError(f"{label} timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise EvidenceError(f"{label} timestamp must include a timezone")
    return parsed


def _runtime_version(value: object, label: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", value):
        raise EvidenceError(f"{label} version is invalid")
    return tuple(int(part) for part in value.split("."))


def _catalog(repo_root: Path) -> tuple[str, list[str]]:
    path = repo_root / "src" / "sketchup_mcp" / "command_catalog.json"
    try:
        contents = path.read_bytes()
        document = json.loads(contents)
        commands = [item["name"] for item in document["commands"]]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise EvidenceError("command catalog is unreadable") from error
    return _sha256(contents), commands


def _project_version(repo_root: Path) -> str:
    try:
        return (repo_root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as error:
        raise EvidenceError("project version is unavailable") from error


def _manifest(repo_root: Path) -> dict[str, Any]:
    document = _read_json(
        repo_root / "testup" / "production_adapter" / "suite_manifest.json",
        "TestUp suite manifest",
    )
    scenarios = document.get("scenarios")
    commands = document.get("commands")
    for values, label in ((scenarios, "scenarios"), (commands, "commands")):
        if not isinstance(values, list) or not values or not all(
            isinstance(item, str) and item for item in values
        ):
            raise EvidenceError(f"TestUp suite {label} are invalid")
        if len(values) != len(set(values)):
            raise EvidenceError(f"TestUp suite {label} are duplicated")
    return document


def suite_sha256(suite_root: Path) -> str:
    """Hash the relative names and bytes of the complete TestUp suite."""

    digest = hashlib.sha256()
    if not suite_root.is_dir():
        return digest.hexdigest()
    for path in sorted(item for item in suite_root.rglob("*") if item.is_file()):
        relative = path.relative_to(suite_root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class RunContext:
    """Immutable identity and manual attestation for one prepared desktop run."""

    run_id: str
    created_at: str
    commit: str
    operator: str
    rbz_filename: str
    rbz_sha256: str
    config_sha256: str
    seed: int

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "RunContext":
        if document.get("schema_version") != CONTEXT_SCHEMA_VERSION:
            raise EvidenceError("run-context schema version differs")
        run_id = _require_sha256(document.get("run_id"), "run ID")
        created_at = _require_string(document.get("created_at"), "run-context time")
        _timestamp(created_at, "run-context")
        commit = document.get("commit")
        if not isinstance(commit, str) or not COMMIT_PATTERN.fullmatch(commit):
            raise EvidenceError("run-context commit is invalid")
        attestation = document.get("attestation")
        if not isinstance(attestation, dict):
            raise EvidenceError("runner attestation is missing")
        if attestation.get("type") != "manual" or attestation.get(
            "licensed_sketchup_confirmed"
        ) is not True:
            raise EvidenceError("licensed-runner manual attestation is missing")
        if attestation.get("single_testup_process_confirmed") is not True:
            raise EvidenceError("single-TestUp-process manual attestation is missing")
        operator = _require_string(attestation.get("operator"), "attesting operator")
        package = document.get("rbz")
        if not isinstance(package, dict):
            raise EvidenceError("run-context RBZ identity is missing")
        artifacts = document.get("artifacts")
        if artifacts != ARTIFACT_NAMES:
            raise EvidenceError("run-context artifact layout differs")
        seed = document.get("seed")
        if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 65_535:
            raise EvidenceError("run-context TestUp seed is invalid")
        return cls(
            run_id=run_id,
            created_at=created_at,
            commit=commit,
            operator=operator,
            rbz_filename=_require_string(package.get("filename"), "run-context RBZ filename"),
            rbz_sha256=_require_sha256(package.get("sha256"), "run-context RBZ hash"),
            config_sha256=_require_sha256(
                document.get("config_sha256"), "run-context config hash"
            ),
            seed=seed,
        )


@dataclass(frozen=True)
class ExactCoverage:
    """Exact line-and-branch measurement of the installed adapter source."""

    document: dict[str, Any]

    @classmethod
    def from_runtime(cls, runtime: Mapping[str, Any]) -> "ExactCoverage":
        if runtime.get("branch_supported") is not True:
            raise EvidenceError("branch coverage is unavailable in the SketchUp Ruby runtime")
        coverage = runtime.get("coverage")
        if not isinstance(coverage, dict):
            raise EvidenceError("coverage report is missing")
        if coverage.get("engine") != "ruby Coverage":
            raise EvidenceError("coverage engine is not Ruby Coverage")
        if coverage.get("scope") != list(COVERAGE_SCOPE):
            raise EvidenceError("coverage scope is not the production SketchUp adapter")
        _require_sha256(coverage.get("source_sha256"), "adapter source hash")
        for name, label in (("lines", "line"), ("branches", "branch")):
            metric = coverage.get(name)
            if not isinstance(metric, dict):
                raise EvidenceError(f"{label} coverage is missing")
            covered = metric.get("covered")
            total = metric.get("total")
            if (
                metric.get("percent") != 100.0
                or not isinstance(covered, int)
                or not isinstance(total, int)
                or total <= 0
                or covered != total
            ):
                raise EvidenceError(f"{label} coverage must be exactly 100%")
            if metric.get("missing") != []:
                raise EvidenceError(f"missing {name} remain in production-adapter coverage")
        return cls(dict(coverage))


@dataclass(frozen=True)
class RuntimeReport:
    """Validated report written by the final TestUp lifecycle test."""

    document: dict[str, Any]
    coverage: ExactCoverage

    @classmethod
    def from_document(
        cls,
        document: dict[str, Any],
        *,
        context: RunContext,
        repo_root: Path,
        package_manifest: Mapping[str, str],
    ) -> "RuntimeReport":
        if document.get("schema_version") != SCHEMA_VERSION:
            raise EvidenceError("runtime-report schema version differs")
        if document.get("run_id") != context.run_id:
            raise EvidenceError("runtime-report run ID differs")
        _timestamp(document.get("generated_at"), "runtime report")
        if document.get("commit") != context.commit:
            raise EvidenceError("runtime-report commit differs")
        if document.get("project_version") != _project_version(repo_root):
            raise EvidenceError("runtime-report project version differs")
        manifest = _manifest(repo_root)
        if document.get("expected_test_count") != len(manifest["scenarios"]) + 1:
            raise EvidenceError("runtime-report test count differs from the suite manifest")
        expected_suite = suite_sha256(repo_root / "testup" / "production_adapter")
        if document.get("suite_sha256") != expected_suite:
            raise EvidenceError("runtime-report suite hash differs from this checkout")
        catalog_sha, commands = _catalog(repo_root)
        if document.get("catalog_sha256") != catalog_sha or document.get(
            "commands"
        ) != commands:
            raise EvidenceError("runtime-report command catalog differs from this checkout")
        installed = document.get("installed_files")
        if installed != dict(package_manifest):
            raise EvidenceError("installed extension manifest differs from the supplied RBZ")
        coverage = ExactCoverage.from_runtime(document)
        if coverage.document["source_sha256"] != package_manifest.get(COVERAGE_SCOPE[0]):
            raise EvidenceError("covered adapter source differs from the installed package")
        return cls(document, coverage)


@dataclass(frozen=True)
class TestUpResults:
    """Exact verbose pass inventory emitted by TestUp's CI JSON reporter."""

    statistics: dict[str, int]
    test_names: tuple[str, ...]
    generated_at: datetime

    @classmethod
    def from_document(
        cls,
        document: Mapping[str, Any],
        expected_names: tuple[str, ...],
        expected_seed: int,
    ) -> "TestUpResults":
        status = document.get("status")
        statistics = document.get("statistics")
        metadata = document.get("metadata")
        if not isinstance(status, dict) or status.get("code") != "Success":
            raise EvidenceError("TestUp status is not Success")
        if not isinstance(statistics, dict):
            raise EvidenceError("TestUp statistics are missing")
        expected_count = len(expected_names)
        for name in ("failures", "errors", "skips"):
            if statistics.get(name) != 0:
                raise EvidenceError(f"TestUp {name} must be zero")
        if statistics.get("total") != expected_count or statistics.get("passes") != expected_count:
            raise EvidenceError("TestUp test and pass counts differ from the suite manifest")
        assertions = statistics.get("assertions")
        if not isinstance(assertions, int) or assertions <= 0:
            raise EvidenceError("TestUp assertions must be positive")
        if not isinstance(metadata, dict) or metadata.get(
            "generated_by"
        ) != "TestUp::CIJsonReporter":
            raise EvidenceError("TestUp result was not produced by the CI JSON reporter")
        options = metadata.get("options")
        if not isinstance(options, dict) or options.get("verbose") is not True:
            raise EvidenceError("TestUp verbose result output is required")
        if options.get("seed") != expected_seed:
            raise EvidenceError("TestUp result seed differs from the prepared run")
        passes = document.get("passes")
        if not isinstance(passes, list):
            raise EvidenceError("TestUp verbose pass inventory is missing")
        actual_names: list[str] = []
        for passed in passes:
            if (
                not isinstance(passed, dict)
                or passed.get("type") != "passed"
                or passed.get("class") != TEST_CLASS
                or not isinstance(passed.get("name"), str)
            ):
                raise EvidenceError("TestUp verbose pass entry is invalid")
            actual_names.append(passed["name"])
        if tuple(actual_names) != expected_names:
            raise EvidenceError("TestUp verbose pass names differ from the suite manifest")
        return cls(
            statistics={
                "total": expected_count,
                "assertions": assertions,
                "passes": expected_count,
                "failures": 0,
                "errors": 0,
                "skips": 0,
            },
            test_names=expected_names,
            generated_at=_timestamp(metadata.get("time"), "TestUp"),
        )


@dataclass(frozen=True)
class RawArtifactPaths:
    """Every raw input whose bytes are bound into final evidence."""

    run_context: Path
    testup_config: Path
    testup_results: Path
    testup_log: Path
    testup_replay: Path
    error_log: Path
    runtime_report: Path
    suite_marker: Path

    def items(self):
        return ((name, getattr(self, name)) for name in RAW_ARTIFACT_KEYS)


def discover_raw_artifacts(run_context: Path) -> RawArtifactPaths:
    """Discover the exact raw bundle inside one prepared run-ID workspace."""

    context_path = run_context.resolve()
    context = RunContext.from_document(_read_json(context_path, "run context"))
    directory = context_path.parent
    if context_path != directory / "run-context.json":
        raise EvidenceError("run context is outside the prepared run")
    if directory.name != f"run-{context.run_id}":
        raise EvidenceError("raw artifacts are outside the prepared run-ID workspace")
    log_directory = directory / ARTIFACT_NAMES["log_directory"]
    try:
        entries = list(log_directory.iterdir())
    except OSError as error:
        raise EvidenceError("TestUp FileReporter directory is unreadable") from error
    roles: dict[str, list[Path]] = {".log": [], ".run": []}
    for entry in entries:
        if entry.is_symlink() or not entry.is_file() or entry.suffix not in roles:
            raise EvidenceError("unexpected TestUp FileReporter artifact role")
        roles[entry.suffix].append(entry)
    for suffix, label in ((".log", "log"), (".run", "replay")):
        if len(roles[suffix]) != 1:
            raise EvidenceError(f"missing or duplicate TestUp {suffix} artifact")
    return RawArtifactPaths(
        run_context=context_path,
        testup_config=directory / ARTIFACT_NAMES["testup_config"],
        testup_results=directory / ARTIFACT_NAMES["testup_results"],
        testup_log=roles[".log"][0],
        testup_replay=roles[".run"][0],
        error_log=directory / ARTIFACT_NAMES["error_log"],
        runtime_report=directory / ARTIFACT_NAMES["runtime_report"],
        suite_marker=directory / ARTIFACT_NAMES["suite_marker"],
    )


def _render_testup_config(
    repo_root: Path, artifact_dir: Path, seed: int, run_id: str
) -> str:
    values = {
        "Path": str((repo_root / "testup" / "production_adapter").resolve()),
        "Output": str((artifact_dir / ARTIFACT_NAMES["testup_results"]).resolve()),
        "LogPath": str((artifact_dir / ARTIFACT_NAMES["log_directory"]).resolve()),
        "ErrorLogPath": str((artifact_dir / ARTIFACT_NAMES["error_log"]).resolve()),
        "SavedRunsPath": str((artifact_dir / ARTIFACT_NAMES["log_directory"]).resolve()),
    }
    lines = [f"{name}: {json.dumps(value)}" for name, value in values.items()]
    lines.extend(["KeepOpen: false", "Verbose: true", f"Seed: {seed}", "Tests:"])
    scenarios = _manifest(repo_root)["scenarios"]
    lines.extend(f"- {name}" for name in _expected_test_filters(scenarios, run_id))
    return "\n".join(lines) + "\n"


def package_manifest(package: Path) -> dict[str, str]:
    """Return the exact installed-file manifest carried by one RBZ."""

    try:
        with zipfile.ZipFile(package) as archive:
            names = [member.filename for member in archive.infolist() if not member.is_dir()]
            return {name: _sha256(archive.read(name)) for name in sorted(names)}
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise EvidenceError("RBZ is unreadable") from error


def _verify_reproducible_package(repo_root: Path, rbz_path: Path) -> tuple[bytes, dict[str, str]]:
    contents = _regular_bytes(rbz_path, "RBZ")
    try:
        check_package(repo_root, rbz_path)
        with tempfile.TemporaryDirectory(prefix="sketchup_mcp_rebuild_") as directory:
            rebuilt = build_package(repo_root, Path(directory)).path
            rebuilt_contents = rebuilt.read_bytes()
    except (OSError, PackageError) as error:
        raise EvidenceError("RBZ does not match the current package source") from error
    if rebuilt_contents != contents:
        raise EvidenceError("RBZ is not the deterministic build of this checkout")
    return contents, package_manifest(rbz_path)


def prepare_run(
    *,
    repo_root: Path,
    artifact_dir: Path,
    rbz_path: Path,
    commit: str,
    operator: str,
    licensed_runner_confirmed: bool,
    single_testup_process_confirmed: bool,
) -> Path:
    """Create a fresh run ID, concrete TestUp config, and manual attestation."""

    if not COMMIT_PATTERN.fullmatch(commit):
        raise EvidenceError("commit must be a full lowercase 40-character Git SHA")
    operator = operator.strip()
    if not operator:
        raise EvidenceError("attesting operator is missing")
    if not licensed_runner_confirmed:
        raise EvidenceError("licensed-runner manual attestation is required")
    if not single_testup_process_confirmed:
        raise EvidenceError("single-TestUp-process manual attestation is required")
    package_contents, _package_files = _verify_reproducible_package(repo_root, rbz_path)
    run_id = secrets.token_hex(32)
    seed = int(run_id[:8], 16) % 65_536
    created_at = datetime.now().astimezone().isoformat()
    artifact_root = artifact_dir.resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    destination = artifact_root / f"run-{run_id}"
    try:
        destination.mkdir()
    except FileExistsError as error:
        raise EvidenceError("generated run workspace already exists") from error
    (destination / ARTIFACT_NAMES["log_directory"]).mkdir(parents=True, exist_ok=True)
    targets = [destination / name for key, name in ARTIFACT_NAMES.items() if key != "log_directory"]
    targets.append(destination / "run-context.json")
    if any(path.exists() or path.is_symlink() for path in targets):
        raise EvidenceError("artifact directory contains output from an earlier run")

    config_path = destination / ARTIFACT_NAMES["testup_config"]
    config_contents = _render_testup_config(repo_root, destination, seed, run_id).encode(
        "utf-8"
    )
    config_path.write_bytes(config_contents)
    error_log = destination / ARTIFACT_NAMES["error_log"]
    error_log.write_bytes(b"")
    context = {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": created_at,
        "commit": commit,
        "attestation": {
            "type": "manual",
            "operator": operator,
            "licensed_sketchup_confirmed": True,
            "single_testup_process_confirmed": True,
        },
        "rbz": {"filename": rbz_path.name, "sha256": _sha256(package_contents)},
        "config_sha256": _sha256(config_contents),
        "seed": seed,
        "artifacts": ARTIFACT_NAMES,
    }
    context_path = destination / "run-context.json"
    context_path.write_text(json.dumps(context, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return context_path


def _validate_artifact_layout(paths: RawArtifactPaths, context: RunContext) -> Path:
    directory = paths.run_context.resolve().parent
    if directory.name != f"run-{context.run_id}":
        raise EvidenceError("raw artifacts are outside the prepared run-ID workspace")
    expected = {
        "run_context": directory / "run-context.json",
        "testup_config": directory / ARTIFACT_NAMES["testup_config"],
        "testup_results": directory / ARTIFACT_NAMES["testup_results"],
        "error_log": directory / ARTIFACT_NAMES["error_log"],
        "runtime_report": directory / ARTIFACT_NAMES["runtime_report"],
        "suite_marker": directory / ARTIFACT_NAMES["suite_marker"],
    }
    for name, path in expected.items():
        if getattr(paths, name).resolve() != path.resolve():
            raise EvidenceError(f"{name.replace('_', ' ')} is outside the prepared run")
    _validate_file_reporter_roles(paths, directory)
    if context.config_sha256 != _sha256(_regular_bytes(paths.testup_config, "TestUp config")):
        raise EvidenceError("TestUp config differs from the prepared run")
    _validate_artifact_times(paths, context)
    return directory


def _validate_file_reporter_roles(paths: RawArtifactPaths, artifact_dir: Path) -> None:
    log_root = (artifact_dir / ARTIFACT_NAMES["log_directory"]).resolve()
    try:
        entries = list(log_root.iterdir())
    except OSError as error:
        raise EvidenceError("TestUp FileReporter directory is unreadable") from error
    roles: dict[str, list[Path]] = {".log": [], ".run": []}
    for entry in entries:
        if entry.is_symlink() or not entry.is_file() or entry.suffix not in roles:
            raise EvidenceError("unexpected TestUp FileReporter artifact role")
        roles[entry.suffix].append(entry.resolve())
    for suffix in (".log", ".run"):
        label = f"TestUp {suffix}"
        if not roles[suffix]:
            raise EvidenceError(f"missing {label} FileReporter artifact")
        if len(roles[suffix]) > 1:
            raise EvidenceError(f"duplicate {label} FileReporter artifacts")
    expected = {".log": paths.testup_log.resolve(), ".run": paths.testup_replay.resolve()}
    for suffix, supplied in expected.items():
        if supplied != roles[suffix][0]:
            raise EvidenceError(f"TestUp {suffix} artifact is outside the prepared run")


def _validate_artifact_times(paths: RawArtifactPaths, context: RunContext) -> None:
    prepared_at = _timestamp(context.created_at, "run-context").timestamp()
    filesystem_resolution_seconds = 5
    for name, path in paths.items():
        try:
            modified_at = path.stat().st_mtime
        except OSError as error:
            raise EvidenceError(f"{name.replace('_', ' ')} is unreadable") from error
        if modified_at + filesystem_resolution_seconds < prepared_at:
            raise EvidenceError(f"{name.replace('_', ' ')} predates the prepared run")


def _assert_generated_config(
    repo_root: Path, artifact_dir: Path, path: Path, seed: int, run_id: str
) -> None:
    actual = _regular_bytes(path, "TestUp config")
    expected = _render_testup_config(repo_root, artifact_dir, seed, run_id).encode("utf-8")
    if actual != expected:
        raise EvidenceError("TestUp config is not the concrete generated config")
    if b"%CONFIG_DIR%" in actual:
        raise EvidenceError("TestUp config contains an unresolved path variable")


def _suite_marker(
    document: Mapping[str, Any], *, context: RunContext, scenarios: list[str]
) -> datetime:
    if document.get("schema_version") != MARKER_SCHEMA_VERSION:
        raise EvidenceError("suite-marker schema version differs")
    if document.get("run_id") != context.run_id:
        raise EvidenceError("suite-marker run ID differs")
    if document.get("test_class") != TEST_CLASS or document.get("scenarios") != scenarios:
        raise EvidenceError("suite-marker inventory differs from the suite manifest")
    if document.get("run_marker_test") != _run_marker_test_name(context.run_id):
        raise EvidenceError("suite-marker run test differs from the prepared run")
    return _timestamp(document.get("generated_at"), "suite marker")


def _run_marker_test_name(run_id: str) -> str:
    return f"{RUN_MARKER_PREFIX}{run_id}"


def _expected_test_names(scenarios: list[str], run_id: str) -> tuple[str, ...]:
    return tuple(
        sorted([*(f"test_{scenario}" for scenario in scenarios), _run_marker_test_name(run_id)])
    )


def _expected_test_filters(scenarios: list[str], run_id: str) -> tuple[str, ...]:
    return tuple(f"{TEST_CLASS}#{name}" for name in _expected_test_names(scenarios, run_id))


def _file_reporter_gate(
    paths: RawArtifactPaths,
    *,
    context: RunContext,
    repo_root: Path,
    expected_test_names: tuple[str, ...],
) -> None:
    try:
        log = _regular_bytes(paths.testup_log, "TestUp FileReporter log").decode("utf-8")
    except UnicodeDecodeError as error:
        raise EvidenceError("TestUp FileReporter log is not UTF-8") from error
    log_names = tuple(
        re.findall(
            rf"^{re.escape(TEST_CLASS)}#(test_[a-z0-9_]+) = .+ = \.$",
            log,
            re.MULTILINE,
        )
    )
    if log_names != expected_test_names:
        raise EvidenceError("TestUp FileReporter log test inventory differs from the prepared run")

    replay = _read_json(paths.testup_replay, "TestUp FileReporter replay")
    expected_replay_names = [f"{TEST_CLASS}#{name}" for name in expected_test_names]
    if replay.get("tests") != expected_replay_names:
        raise EvidenceError("TestUp FileReporter replay test inventory differs from the prepared run")
    if replay.get("seed") != context.seed:
        raise EvidenceError("TestUp FileReporter replay seed differs from the prepared run")
    if not isinstance(replay.get("test_suite"), str) or not replay["test_suite"]:
        raise EvidenceError("TestUp FileReporter replay suite is missing")
    replay_path = replay.get("path")
    if not isinstance(replay_path, str) or Path(replay_path).resolve() != (
        repo_root / "testup" / "production_adapter"
    ).resolve():
        raise EvidenceError("TestUp FileReporter replay suite path differs")


def _correlate_run(
    context: RunContext,
    runtime: RuntimeReport,
    marker_time: datetime,
    results: TestUpResults,
) -> None:
    context_time = _timestamp(context.created_at, "run-context")
    runtime_time = _timestamp(runtime.document.get("generated_at"), "runtime report")
    if runtime_time != marker_time:
        raise EvidenceError("runtime report and suite marker came from different runs")
    if not context_time <= runtime_time <= results.generated_at:
        raise EvidenceError("raw artifact timestamps are out of lifecycle order")
    if (results.generated_at - context_time).total_seconds() > 3600:
        raise EvidenceError("TestUp output is outside the prepared run window")


def _runtime_identity(runtime: Mapping[str, Any]) -> dict[str, str]:
    os_family = runtime.get("os_family")
    if os_family not in SUPPORTED_OS_FAMILIES:
        raise EvidenceError("runtime operating system is not designated Windows or macOS")
    identity = {"os_family": str(os_family)}
    for name in ("os_version", "architecture", "ruby_platform"):
        identity[name] = _require_string(runtime.get(name), f"runtime {name}")
    sketchup = _require_string(runtime.get("sketchup_version"), "SketchUp version")
    if _runtime_version(sketchup, "SketchUp") < (2024, 0):
        raise EvidenceError("SketchUp 2024 or newer is required")
    testup = _require_string(runtime.get("testup_version"), "TestUp version")
    if testup != SUPPORTED_TESTUP_VERSION:
        raise EvidenceError(f"TestUp {SUPPORTED_TESTUP_VERSION} is required")
    ruby = _require_string(runtime.get("ruby_version"), "Ruby version")
    if _runtime_version(ruby, "Ruby") < (3, 2):
        raise EvidenceError("Ruby 3.2 or newer is required for branch coverage")
    identity.update(
        sketchup_version=sketchup,
        testup_version=testup,
        ruby_version=ruby,
    )
    return identity


def _raw_artifacts(paths: RawArtifactPaths) -> dict[str, dict[str, str | int]]:
    artifacts = {}
    for name, path in paths.items():
        contents = _regular_bytes(path, name.replace("_", " "))
        artifacts[name] = {
            "filename": path.name,
            "sha256": _sha256(contents),
            "size": len(contents),
        }
    return artifacts


def collect_evidence(
    *,
    repo_root: Path,
    raw_paths: RawArtifactPaths,
    rbz_path: Path,
    commit: str,
) -> dict[str, Any]:
    """Combine a complete, run-correlated set of production runtime artifacts."""

    root = repo_root.resolve()
    if not COMMIT_PATTERN.fullmatch(commit):
        raise EvidenceError("commit must be a full lowercase 40-character Git SHA")
    context = RunContext.from_document(_read_json(raw_paths.run_context, "run context"))
    if context.commit != commit:
        raise EvidenceError("run-context commit differs from the requested commit")
    artifact_dir = _validate_artifact_layout(raw_paths, context)
    _assert_generated_config(
        root, artifact_dir, raw_paths.testup_config, context.seed, context.run_id
    )
    if _regular_bytes(raw_paths.error_log, "TestUp error log"):
        raise EvidenceError("TestUp error log is not empty")

    package_contents, package_manifest = _verify_reproducible_package(root, rbz_path)
    if context.rbz_filename != rbz_path.name or context.rbz_sha256 != _sha256(package_contents):
        raise EvidenceError("run-context RBZ identity differs from the supplied package")
    manifest = _manifest(root)
    runtime = RuntimeReport.from_document(
        _read_json(raw_paths.runtime_report, "runtime report"),
        context=context,
        repo_root=root,
        package_manifest=package_manifest,
    )
    marker_time = _suite_marker(
        _read_json(raw_paths.suite_marker, "suite marker"),
        context=context,
        scenarios=manifest["scenarios"],
    )
    results_document = _read_json(raw_paths.testup_results, "TestUp results")
    expected_names = _expected_test_names(manifest["scenarios"], context.run_id)
    results = TestUpResults.from_document(results_document, expected_names, context.seed)
    _file_reporter_gate(
        raw_paths,
        context=context,
        repo_root=root,
        expected_test_names=expected_names,
    )
    if results_document["metadata"].get("ruby_version") != runtime.document.get("ruby_version"):
        raise EvidenceError("runtime report and TestUp output name different Ruby runtimes")
    _correlate_run(context, runtime, marker_time, results)
    catalog_sha, commands = _catalog(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": EVIDENCE_KIND,
        "run_id": context.run_id,
        "created_at": runtime.document["generated_at"],
        "attestation": {
            "type": "manual",
            "operator": context.operator,
            "licensed_sketchup_confirmed": True,
            "single_testup_process_confirmed": True,
        },
        "source": {
            "commit": commit,
            "version": _project_version(root),
            "rbz": {"filename": rbz_path.name, "sha256": _sha256(package_contents)},
            "suite_sha256": suite_sha256(root / "testup" / "production_adapter"),
            "catalog_sha256": catalog_sha,
            "commands": commands,
            "installed_files": package_manifest,
        },
        "runtime": _runtime_identity(runtime.document),
        "testup": {
            "status": "success",
            **results.statistics,
            "test_class": TEST_CLASS,
            "tests": list(results.test_names),
        },
        "coverage": runtime.coverage.document,
        "raw_artifacts": _raw_artifacts(raw_paths),
    }


def validate_evidence(
    evidence: dict[str, Any],
    *,
    repo_root: Path,
    raw_paths: RawArtifactPaths,
    rbz_path: Path,
    expected_commit: str,
) -> None:
    """Rebuild evidence from explicit raw inputs and require an exact match."""

    rebuilt = collect_evidence(
        repo_root=repo_root,
        raw_paths=raw_paths,
        rbz_path=rbz_path,
        commit=expected_commit,
    )
    if evidence != rebuilt:
        raise EvidenceError("final evidence differs from its validated raw artifacts")


def _git_commit(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def validate_checkout(repo_root: Path, commit: str) -> None:
    """Require a clean checkout at one exact full commit."""

    if _git_commit(repo_root) != commit:
        raise EvidenceError("requested commit is not the checked-out Git commit")
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    if completed.stdout.strip():
        raise EvidenceError("source checkout is not clean; rebuild and rerun from one commit")


def _add_raw_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-context", type=Path, required=True)
    parser.add_argument("--testup-config", type=Path, required=True)
    parser.add_argument("--testup-results", type=Path, required=True)
    parser.add_argument("--testup-log", type=Path, required=True)
    parser.add_argument("--testup-replay", type=Path, required=True)
    parser.add_argument("--error-log", type=Path, required=True)
    parser.add_argument("--runtime-report", type=Path, required=True)
    parser.add_argument("--suite-marker", type=Path, required=True)


def _raw_paths(args: argparse.Namespace) -> RawArtifactPaths:
    return RawArtifactPaths(
        run_context=args.run_context,
        testup_config=args.testup_config,
        testup_results=args.testup_results,
        testup_log=args.testup_log,
        testup_replay=args.testup_replay,
        error_log=args.error_log,
        runtime_report=args.runtime_report,
        suite_marker=args.suite_marker,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    subparsers = parser.add_subparsers(dest="action", required=True)

    prepare = subparsers.add_parser("prepare", help="prepare a fresh TestUp run")
    prepare.add_argument("--artifact-dir", type=Path, required=True)
    prepare.add_argument("--rbz", type=Path, required=True)
    prepare.add_argument("--commit")
    prepare.add_argument("--operator", required=True)
    prepare.add_argument("--attest-licensed-runner", action="store_true", required=True)
    prepare.add_argument("--attest-single-testup-process", action="store_true", required=True)

    collect = subparsers.add_parser("collect", help="collect final evidence")
    _add_raw_arguments(collect)
    collect.add_argument("--rbz", type=Path, required=True)
    collect.add_argument("--commit")
    collect.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser("validate", help="validate final evidence")
    _add_raw_arguments(validate)
    validate.add_argument("--evidence", type=Path, required=True)
    validate.add_argument("--rbz", type=Path, required=True)
    validate.add_argument("--commit")
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.repo_root.resolve()
    commit = args.commit or _git_commit(root)
    try:
        validate_checkout(root, commit)
        if args.action == "prepare":
            context_path = prepare_run(
                repo_root=root,
                artifact_dir=args.artifact_dir,
                rbz_path=args.rbz,
                commit=commit,
                operator=args.operator,
                licensed_runner_confirmed=args.attest_licensed_runner,
                single_testup_process_confirmed=args.attest_single_testup_process,
            )
            print(context_path)
        elif args.action == "collect":
            evidence = collect_evidence(
                repo_root=root,
                raw_paths=_raw_paths(args),
                rbz_path=args.rbz,
                commit=commit,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"Collected SketchUp runtime evidence: {args.output}")
        else:
            validate_evidence(
                _read_json(args.evidence, "evidence"),
                repo_root=root,
                raw_paths=_raw_paths(args),
                rbz_path=args.rbz,
                expected_commit=commit,
            )
            print("SketchUp runtime evidence is current and complete")
    except EvidenceError as error:
        print(f"SketchUp runtime evidence failed: {error}", file=sys.stderr)
        return 1
    except (OSError, subprocess.CalledProcessError):
        print("SketchUp runtime evidence failed: filesystem or Git operation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
