"""Fail-closed validation of trusted GitHub and SketchUp release inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any

from sketchup_runtime_evidence import (
    EvidenceError,
    RawArtifactPaths,
    discover_raw_artifacts,
)
from sketchup_runtime_runner import RunnerError, validate_installation_bundle


EXPECTED_REPOSITORY = "coral-garden/sketchup-mcp"
RUNTIME_WORKFLOW_NAME = "SketchUp Runtime Evidence"
RUNTIME_WORKFLOW_PATH = ".github/workflows/sketchup-runtime.yml"
MAX_RUNTIME_AGE = timedelta(hours=24)
RUN_WORKSPACE_PATTERN = re.compile(r"run-([0-9a-f]{64})")
DISPATCHER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")


class TrustedReleaseError(ValueError):
    """Trusted release metadata or artifacts do not match the candidate."""


@dataclass(frozen=True)
class TrustedRun:
    """Validated GitHub provenance for one protected runtime workflow run."""

    dispatcher: str


@dataclass(frozen=True)
class TrustedRuntimeBundle:
    """Validated fixed-layout runtime files ready for public validators."""

    workspace: Path
    evidence: dict[str, Any]
    raw_paths: RawArtifactPaths
    retained_rbz: Path
    retained_wheel: Path
    retained_sdist: Path
    acceptance_dir: Path


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise TrustedReleaseError(f"{label} is missing or is not a regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedReleaseError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise TrustedReleaseError(f"{label} must be a JSON object")
    return value


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TrustedReleaseError(f"{label} is missing")
    return value


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise TrustedReleaseError(f"{label} is invalid")
    return value


def _expect(value: object, expected: object, label: str) -> None:
    if value != expected:
        raise TrustedReleaseError(f"{label} differs from the trusted release contract")


def _fresh_timestamp(value: object, label: str, now: datetime) -> datetime:
    if not isinstance(value, str) or not value:
        raise TrustedReleaseError(f"{label} timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TrustedReleaseError(f"{label} timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise TrustedReleaseError(f"{label} timestamp must include a timezone")
    age = now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)
    if age < timedelta(0):
        raise TrustedReleaseError(f"{label} timestamp is in the future")
    if age > MAX_RUNTIME_AGE:
        raise TrustedReleaseError(f"{label} is older than 24 hours")
    return parsed


def validate_trusted_run(
    document: dict[str, Any], *, run_id: int, commit: str, now: datetime
) -> TrustedRun:
    """Validate native GitHub run/artifact metadata and return its dispatcher."""

    _expect(document.get("schema_version"), 1, "trusted manifest schema")
    run = _object(document.get("run"), "GitHub workflow run")
    artifact = _object(document.get("artifact"), "GitHub runtime artifact")
    repository = _object(run.get("repository"), "GitHub repository")
    actor = _object(run.get("actor"), "GitHub workflow dispatcher")
    workflow_run = _object(artifact.get("workflow_run"), "artifact workflow run")

    _expect(_integer(run.get("id"), "GitHub run ID"), run_id, "GitHub run ID")
    _expect(repository.get("full_name"), EXPECTED_REPOSITORY, "GitHub repository")
    _expect(run.get("name"), RUNTIME_WORKFLOW_NAME, "runtime workflow name")
    _expect(run.get("path"), RUNTIME_WORKFLOW_PATH, "runtime workflow path")
    _expect(run.get("event"), "workflow_dispatch", "runtime workflow event")
    _expect(run.get("status"), "completed", "runtime workflow status")
    _expect(run.get("conclusion"), "success", "runtime workflow conclusion")
    _expect(run.get("head_sha"), commit, "runtime workflow commit")
    _fresh_timestamp(run.get("created_at"), "runtime workflow creation", now)
    _fresh_timestamp(run.get("updated_at"), "runtime workflow completion", now)
    dispatcher = actor.get("login")
    if not isinstance(dispatcher, str) or not DISPATCHER_PATTERN.fullmatch(dispatcher):
        raise TrustedReleaseError("GitHub workflow dispatcher is invalid")

    _integer(artifact.get("id"), "GitHub artifact ID")
    _expect(
        artifact.get("name"),
        f"sketchup-runtime-evidence-{run_id}",
        "runtime artifact name",
    )
    _expect(artifact.get("expired"), False, "runtime artifact expiry")
    _fresh_timestamp(artifact.get("created_at"), "runtime artifact creation", now)
    _fresh_timestamp(artifact.get("updated_at"), "runtime artifact update", now)
    _expect(
        _integer(workflow_run.get("id"), "artifact workflow run ID"),
        run_id,
        "artifact workflow run ID",
    )
    _expect(workflow_run.get("head_sha"), commit, "artifact workflow commit")
    return TrustedRun(dispatcher=dispatcher)


def _workspace(root: Path) -> tuple[Path, str]:
    if root.is_symlink() or not root.is_dir():
        raise TrustedReleaseError("trusted runtime root is missing or is not a directory")
    workspaces = [
        path
        for path in root.iterdir()
        if not path.is_symlink()
        and path.is_dir()
        and RUN_WORKSPACE_PATTERN.fullmatch(path.name)
    ]
    if len(workspaces) != 1:
        raise TrustedReleaseError(
            "trusted runtime root must contain exactly one run-ID workspace"
        )
    match = RUN_WORKSPACE_PATTERN.fullmatch(workspaces[0].name)
    if match is None:
        raise AssertionError("validated workspace did not match its pattern")
    return workspaces[0], match.group(1)


def load_runtime_bundle(
    *,
    repo_root: Path,
    runtime_root: Path,
    commit: str,
    dispatcher: str,
    version: str,
    now: datetime,
) -> TrustedRuntimeBundle:
    """Load one fixed-layout runtime artifact and validate its install proof."""

    workspace, run_id = _workspace(runtime_root)
    evidence = _read_json(workspace / "evidence.json", "SketchUp runtime evidence")
    _expect(evidence.get("run_id"), run_id, "evidence run ID")
    _fresh_timestamp(evidence.get("created_at"), "runtime evidence", now)
    rbz = workspace / f"sketchup-mcp-{version}.rbz"
    if rbz.is_symlink() or not rbz.is_file():
        raise TrustedReleaseError("retained RBZ is missing or is not a regular file")
    wheel = workspace / f"sketchup_mcp-{version}-py3-none-any.whl"
    sdist = workspace / f"sketchup_mcp-{version}.tar.gz"
    for path, label in (
        (wheel, "retained wheel"),
        (sdist, "retained source distribution"),
    ):
        if path.is_symlink() or not path.is_file():
            raise TrustedReleaseError(f"{label} is missing or is not a regular file")
    acceptance = workspace / "install-acceptance"
    if acceptance.is_symlink() or not acceptance.is_dir():
        raise TrustedReleaseError("install acceptance directory is missing or unsafe")
    _read_json(acceptance / "evidence.json", "install acceptance evidence")
    run_context = workspace / "run-context.json"
    try:
        validate_installation_bundle(
            repo_root=repo_root,
            run_context=run_context,
            rbz_path=rbz,
            expected_commit=commit,
            expected_dispatcher=dispatcher,
        )
        raw_paths = discover_raw_artifacts(run_context)
    except (EvidenceError, RunnerError, OSError) as error:
        raise TrustedReleaseError(f"trusted runtime bundle failed: {error}") from error
    return TrustedRuntimeBundle(
        workspace=workspace,
        evidence=evidence,
        raw_paths=raw_paths,
        retained_rbz=rbz,
        retained_wheel=wheel,
        retained_sdist=sdist,
        acceptance_dir=acceptance,
    )


def validator_arguments(raw_paths: RawArtifactPaths) -> list[str]:
    """Convert #15's public raw bundle into its public CLI arguments."""

    arguments = []
    for name, path in raw_paths.items():
        arguments.extend((f"--{name.replace('_', '-')}", str(path)))
    return arguments
