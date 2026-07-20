#!/usr/bin/env python3
"""Prepare, collect, and validate the protected clean-install MCP proof."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import time
from typing import Any, Mapping
from urllib.parse import unquote, urlparse
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
KIND = "sketchup_mcp.install_acceptance"
PREPARED_KIND = f"{KIND}.prepared"
WORKFLOW_PATH = ".github/workflows/sketchup-runtime.yml"
HOST = "127.0.0.1"
DEFAULT_PORT = 9876
MAX_AGE = timedelta(hours=24)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
DISPATCHER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
RAW_NAMES = (
    "prepared.json",
    "runtime-input.json",
    "mcp-host-config.json",
    "python-distribution.json",
    "startup.rb",
    "evidence.schema.json",
    "bridge-ready.json",
    "mcp-session.json",
    "bridge-exit.json",
)


class AcceptanceError(ValueError):
    """Install acceptance inputs cannot prove the protected workflow."""


def _sha256(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _reject_symlink_traversal(path: Path, label: str) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise AcceptanceError(f"{label} cannot traverse a symlink")


def _regular_bytes(path: Path, label: str) -> bytes:
    try:
        _reject_symlink_traversal(path, label)
        if path.is_symlink() or not path.is_file():
            raise AcceptanceError(f"{label} must be a regular file")
        return path.read_bytes()
    except OSError as error:
        raise AcceptanceError(f"{label} is unreadable") from error


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_regular_bytes(path, label))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AcceptanceError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise AcceptanceError(f"{label} must be a JSON object")
    return value


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _proof(path: Path, label: str) -> dict[str, Any]:
    contents = _regular_bytes(path, label)
    return {"filename": path.name, "sha256": _sha256(contents), "size": len(contents)}


def _timestamp(value: object, label: str, *, now: datetime | None = None) -> datetime:
    if not isinstance(value, str) or not value:
        raise AcceptanceError(f"{label} timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AcceptanceError(f"{label} timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise AcceptanceError(f"{label} timestamp must include a timezone")
    if now is not None:
        age = now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)
        if age < timedelta(0) or age > MAX_AGE:
            raise AcceptanceError(f"{label} timestamp is outside the 24-hour window")
    return parsed


def _safe_directory(path: Path, label: str) -> Path:
    _reject_symlink_traversal(path, label)
    if path.is_symlink() or not path.is_dir():
        raise AcceptanceError(f"{label} must be a real directory")
    resolved = path.resolve()
    return resolved


def _catalog(repo_root: Path) -> tuple[str, list[str]]:
    path = repo_root / "src" / "sketchup_mcp" / "command_catalog.json"
    contents = _regular_bytes(path, "command catalog")
    try:
        document = json.loads(contents)
        names = [command["name"] for command in document["commands"]]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise AcceptanceError("command catalog is invalid") from error
    if not names or not all(isinstance(name, str) and name for name in names):
        raise AcceptanceError("command catalog names are invalid")
    return _sha256(contents), names


def _project_version(repo_root: Path) -> str:
    try:
        version = (repo_root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as error:
        raise AcceptanceError("project version is unavailable") from error
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", version):
        raise AcceptanceError("project version is invalid")
    return version


def _redacted_executable(path: Path) -> str:
    parts = path.as_posix().split("/")
    if ".venv" in parts:
        index = parts.index(".venv")
        suffix = "/".join(parts[index + 1 :])
        return f"CHECKOUT/.venv/{suffix}"
    return f"PYTHON_ENV/{path.name}"


def _console_executable(python_executable: Path) -> Path:
    name = "sketchup-mcp.exe" if os.name == "nt" else "sketchup-mcp"
    return python_executable.parent / name


def _context(path: Path, commit: str) -> tuple[dict[str, Any], Path]:
    context = _read_json(path, "run context")
    if context.get("schema_version") != 2:
        raise AcceptanceError("run-context schema version differs")
    run_id = context.get("run_id")
    if not isinstance(run_id, str) or not SHA256_PATTERN.fullmatch(run_id):
        raise AcceptanceError("run ID is invalid")
    if context.get("commit") != commit or not COMMIT_PATTERN.fullmatch(commit):
        raise AcceptanceError("run-context commit differs")
    _timestamp(context.get("created_at"), "run-context")
    attestation = context.get("attestation")
    if not isinstance(attestation, dict):
        raise AcceptanceError("manual attestation is missing")
    if attestation.get("type") != "manual" or attestation.get(
        "licensed_sketchup_confirmed"
    ) is not True:
        raise AcceptanceError("licensed SketchUp manual attestation is missing")
    if attestation.get("single_testup_process_confirmed") is not True:
        raise AcceptanceError("single-process manual attestation is missing")
    operator = attestation.get("operator")
    if not isinstance(operator, str) or not operator.strip():
        raise AcceptanceError("attesting operator is missing")
    workspace = _safe_directory(path.parent, "run workspace")
    if workspace.name != f"run-{run_id}":
        raise AcceptanceError("run workspace does not match the random run ID")
    return context, workspace


def _candidate(path: Path, workspace: Path, label: str) -> Path:
    _regular_bytes(path, label)
    source = path.resolve()
    destination = workspace / source.name
    if source != destination:
        if destination.exists() or destination.is_symlink():
            if _proof(destination, label) != _proof(source, label):
                raise AcceptanceError(f"retained {label} differs")
        else:
            shutil.copyfile(source, destination)
    return destination


def _installed_distribution(
    *, version: str, wheel: Path, executable: Path, enforce: bool
) -> dict[str, Any]:
    console_executable = _console_executable(executable)
    document: dict[str, Any] = {
        "name": "sketchup-mcp",
        "version": version,
        "source": "exact candidate wheel",
        "wheel_sha256": _proof(wheel, "candidate wheel")["sha256"],
        "python": _redacted_executable(executable),
        "console_script": _redacted_executable(console_executable),
        "installation_verified": False,
    }
    if not enforce:
        return document
    if executable.resolve() != Path(sys.executable).resolve():
        raise AcceptanceError("installed Python executable differs from this process")
    _regular_bytes(console_executable, "installed sketchup-mcp console script")
    try:
        distribution = importlib.metadata.distribution("sketchup-mcp")
    except importlib.metadata.PackageNotFoundError as error:
        raise AcceptanceError("installed Python distribution is missing") from error
    if distribution.version != version:
        raise AcceptanceError("installed Python distribution version differs")
    direct_url_text = distribution.read_text("direct_url.json")
    try:
        direct_url = json.loads(direct_url_text) if direct_url_text else None
    except json.JSONDecodeError as error:
        raise AcceptanceError("installed Python distribution identity is invalid") from error
    url = direct_url.get("url") if isinstance(direct_url, dict) else None
    if not isinstance(url, str) or Path(unquote(urlparse(url).path)).name != wheel.name:
        raise AcceptanceError("installed Python distribution did not come from the exact wheel name")
    installed_files: dict[str, str] = {}
    try:
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                if name.endswith("/") or name.endswith("/RECORD"):
                    continue
                installed = Path(distribution.locate_file(name))
                installed_contents = _regular_bytes(
                    installed, f"installed Python distribution file {name}"
                )
                if installed_contents != archive.read(name):
                    raise AcceptanceError(
                        f"installed Python distribution file differs: {name}"
                    )
                installed_files[name] = _sha256(installed_contents)
    except (OSError, zipfile.BadZipFile) as error:
        raise AcceptanceError("candidate wheel cannot prove the installed distribution") from error
    if not installed_files:
        raise AcceptanceError("installed Python distribution file proof is empty")
    document["direct_url"] = "REDACTED/exact-candidate-wheel"
    document["installed_files"] = installed_files
    document["installation_verified"] = True
    return document


def prepare(
    *,
    repo_root: Path,
    run_context: Path,
    rbz_path: Path,
    wheel_path: Path,
    sdist_path: Path,
    commit: str,
    dispatcher: str,
    github_run_id: int,
    port: int,
    os_version: str,
    python_executable: Path,
    enforce_installed_distribution: bool = False,
    probe_port: bool = False,
    now: datetime | None = None,
) -> Path:
    """Create fixed adjacent input files for the static Ruby startup harness."""

    root = _safe_directory(repo_root, "repository root")
    context, workspace = _context(run_context, commit)
    if not DISPATCHER_PATTERN.fullmatch(dispatcher):
        raise AcceptanceError("dispatcher is invalid")
    if not isinstance(github_run_id, int) or isinstance(github_run_id, bool) or github_run_id <= 0:
        raise AcceptanceError("GitHub run ID is invalid")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise AcceptanceError("dedicated bridge port is invalid")
    if port == DEFAULT_PORT:
        raise AcceptanceError("install acceptance must not use the normal bridge port")
    if (
        not isinstance(os_version, str)
        or not os_version.strip()
        or os_version != os_version.strip()
        or "\n" in os_version
        or "\r" in os_version
    ):
        raise AcceptanceError("protected runner OS version is invalid")
    if probe_port:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((HOST, port))
        except OSError as error:
            raise AcceptanceError("dedicated bridge port is already in use") from error
    version = _project_version(root)
    rbz = _candidate(rbz_path, workspace, "candidate RBZ")
    wheel = _candidate(wheel_path, workspace, "candidate wheel")
    sdist = _candidate(sdist_path, workspace, "candidate source distribution")
    catalog_sha256, expected_tools = _catalog(root)
    acceptance = workspace / "install-acceptance"
    if acceptance.exists() or acceptance.is_symlink():
        raise AcceptanceError("install acceptance directory already exists")
    acceptance.mkdir()
    source_harness = root / "testup" / "install_acceptance" / "startup.rb"
    shutil.copyfile(source_harness, acceptance / "startup.rb")
    source_schema = root / "testup" / "install_acceptance" / "evidence.schema.json"
    shutil.copyfile(source_schema, acceptance / "evidence.schema.json")
    distribution = _installed_distribution(
        version=version,
        wheel=wheel,
        executable=python_executable,
        enforce=enforce_installed_distribution,
    )
    host_config = {
        "schema_version": 1,
        "kind": f"{KIND}.mcp_host_config",
        "transport": "stdio",
        "command": _redacted_executable(_console_executable(python_executable)),
        "args": [],
        "environment": {"SKETCHUP_MCP_BRIDGE_PORT": str(port)},
    }
    prepared = {
        "schema_version": 1,
        "kind": PREPARED_KIND,
        "producer": {"workflow": WORKFLOW_PATH, "github_run_id": github_run_id},
        "run_id": context["run_id"],
        "created_at": (now or datetime.now(timezone.utc)).isoformat(),
        "commit": commit,
        "version": version,
        "operator": context["attestation"]["operator"].strip(),
        "dispatcher": dispatcher,
        "github_run_id": github_run_id,
        "bridge_host": HOST,
        "bridge_port": port,
        "os_version": os_version,
        "catalog_sha256": catalog_sha256,
        "expected_tools": expected_tools,
        "mcp_host_config": host_config,
        "python_distribution": distribution,
        "candidates": {
            "rbz": _proof(rbz, "candidate RBZ"),
            "wheel": _proof(wheel, "candidate wheel"),
            "sdist": _proof(sdist, "candidate source distribution"),
        },
    }
    _write_json(acceptance / "prepared.json", prepared)
    _write_json(acceptance / "runtime-input.json", prepared)
    _write_json(acceptance / "mcp-host-config.json", host_config)
    _write_json(acceptance / "python-distribution.json", distribution)
    return acceptance


def _validate_prepared(
    *,
    repo_root: Path,
    acceptance_dir: Path,
    expected_commit: str,
    expected_dispatcher: str,
    now: datetime,
    expected_github_run_id: int | None = None,
) -> dict[str, Any]:
    acceptance = _safe_directory(acceptance_dir, "install acceptance directory")
    prepared = _read_json(acceptance / "prepared.json", "prepared install acceptance")
    if prepared.get("schema_version") != 1 or prepared.get("kind") != PREPARED_KIND:
        raise AcceptanceError("prepared install acceptance contract differs")
    if prepared.get("commit") != expected_commit or not COMMIT_PATTERN.fullmatch(expected_commit):
        raise AcceptanceError("prepared commit differs")
    if prepared.get("dispatcher") != expected_dispatcher:
        raise AcceptanceError("prepared dispatcher differs")
    producer = prepared.get("producer")
    if not isinstance(producer, dict) or producer.get("workflow") != WORKFLOW_PATH:
        raise AcceptanceError("prepared producer is not the protected runtime workflow")
    github_run_id = prepared.get("github_run_id")
    if (
        not isinstance(github_run_id, int)
        or isinstance(github_run_id, bool)
        or github_run_id <= 0
        or producer.get("github_run_id") != github_run_id
        or (expected_github_run_id is not None and github_run_id != expected_github_run_id)
    ):
        raise AcceptanceError("prepared GitHub run ID differs")
    run_id = prepared.get("run_id")
    if not isinstance(run_id, str) or not SHA256_PATTERN.fullmatch(run_id):
        raise AcceptanceError("prepared run ID is invalid")
    if acceptance.parent.name != f"run-{run_id}":
        raise AcceptanceError("prepared workspace differs from the run ID")
    run_context, context_workspace = _context(
        acceptance.parent / "run-context.json", expected_commit
    )
    context_operator = run_context["attestation"]["operator"].strip()
    if context_workspace != acceptance.parent or run_context["run_id"] != run_id:
        raise AcceptanceError("prepared identity differs from the run context")
    if prepared.get("operator") != context_operator:
        raise AcceptanceError("prepared operator differs from the run context")
    _timestamp(prepared.get("created_at"), "prepared install acceptance", now=now)
    if prepared.get("bridge_host") != HOST or prepared.get("bridge_port") == DEFAULT_PORT:
        raise AcceptanceError("prepared bridge endpoint is not dedicated loopback")
    catalog_sha256, names = _catalog(repo_root)
    if prepared.get("catalog_sha256") != catalog_sha256 or prepared.get("expected_tools") != names:
        raise AcceptanceError("prepared command catalog differs")
    if _read_json(acceptance / "runtime-input.json", "runtime input") != prepared:
        raise AcceptanceError("adjacent runtime input differs from preparation")
    if _read_json(acceptance / "mcp-host-config.json", "MCP host config") != prepared.get("mcp_host_config"):
        raise AcceptanceError("redacted MCP host config differs")
    host_config = prepared.get("mcp_host_config")
    allowed_commands = {
        "CHECKOUT/.venv/bin/sketchup-mcp",
        "CHECKOUT/.venv/Scripts/sketchup-mcp.exe",
    }
    if not isinstance(host_config, dict) or host_config != {
        "schema_version": 1,
        "kind": f"{KIND}.mcp_host_config",
        "transport": "stdio",
        "command": host_config.get("command"),
        "args": [],
        "environment": {"SKETCHUP_MCP_BRIDGE_PORT": str(prepared["bridge_port"])},
    } or host_config.get("command") not in allowed_commands:
        raise AcceptanceError("redacted MCP host config is invalid")
    distribution = prepared.get("python_distribution")
    if _read_json(acceptance / "python-distribution.json", "Python distribution") != distribution:
        raise AcceptanceError("Python distribution identity differs")
    if (
        not isinstance(distribution, dict)
        or distribution.get("name") != "sketchup-mcp"
        or distribution.get("version") != prepared.get("version")
        or distribution.get("source") != "exact candidate wheel"
        or distribution.get("console_script") != host_config.get("command")
        or distribution.get("installation_verified") is not True
        or distribution.get("wheel_sha256")
        != prepared.get("candidates", {}).get("wheel", {}).get("sha256")
        or distribution.get("direct_url") != "REDACTED/exact-candidate-wheel"
        or not isinstance(distribution.get("installed_files"), dict)
        or not distribution["installed_files"]
    ):
        raise AcceptanceError("exact installed Python distribution proof is missing")
    candidates = prepared.get("candidates")
    if not isinstance(candidates, dict) or set(candidates) != {"rbz", "wheel", "sdist"}:
        raise AcceptanceError("candidate artifact inventory differs")
    for label, candidate in candidates.items():
        if not isinstance(candidate, dict) or not isinstance(candidate.get("filename"), str):
            raise AcceptanceError(f"candidate {label} proof is invalid")
        if candidate != _proof(
            acceptance.parent / candidate["filename"], f"retained candidate {label}"
        ):
            raise AcceptanceError(f"retained candidate {label} proof differs")
    if _regular_bytes(acceptance / "evidence.schema.json", "evidence schema") != _regular_bytes(
        repo_root / "testup" / "install_acceptance" / "evidence.schema.json",
        "checked-in evidence schema",
    ):
        raise AcceptanceError("evidence schema differs from this checkout")
    return prepared


def _validate_ready(document: Mapping[str, Any], prepared: Mapping[str, Any], now: datetime) -> None:
    expected = {
        "schema_version": 1,
        "kind": f"{KIND}.ready",
        "run_id": prepared["run_id"],
        "commit": prepared["commit"],
        "version": prepared["version"],
        "catalog_sha256": prepared["catalog_sha256"],
        "port": prepared["bridge_port"],
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise AcceptanceError(f"bridge-ready {key.replace('_', ' ')} differs")
    if document.get("os_version") != prepared.get("os_version"):
        raise AcceptanceError("bridge-ready OS version differs")
    if not isinstance(document.get("sketchup_version"), str) or not document[
        "sketchup_version"
    ]:
        raise AcceptanceError("bridge-ready SketchUp version is missing")
    _timestamp(document.get("created_at"), "bridge-ready", now=now)


def _validate_session(document: Mapping[str, Any], prepared: Mapping[str, Any], now: datetime) -> None:
    if document.get("schema_version") != 1 or document.get("kind") != f"{KIND}.mcp_session":
        raise AcceptanceError("MCP session contract differs")
    if document.get("run_id") != prepared["run_id"] or document.get("initialized") is not True:
        raise AcceptanceError("MCP initialize proof differs")
    started = _timestamp(document.get("started_at"), "MCP session start", now=now)
    completed = _timestamp(document.get("completed_at"), "MCP session completion", now=now)
    if completed < started:
        raise AcceptanceError("MCP session timestamps are reversed")
    if document.get("tools") != prepared["expected_tools"]:
        raise AcceptanceError("MCP list_tools result differs from the command catalog")
    call = document.get("call")
    if not isinstance(call, dict) or call.get("name") != "get_selection" or call.get("arguments") != {}:
        raise AcceptanceError("MCP get_selection call differs")
    result = call.get("raw_call_tool_result")
    if not isinstance(result, dict) or result.get("isError") is not False:
        raise AcceptanceError("raw CallToolResult is unsuccessful")
    content = result.get("content")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        raise AcceptanceError("raw CallToolResult content differs")
    if content[0].get("type") != "text" or not isinstance(content[0].get("text"), str):
        raise AcceptanceError("raw CallToolResult text is missing")
    try:
        envelope = json.loads(content[0]["text"])
    except json.JSONDecodeError as error:
        raise AcceptanceError("raw CallToolResult envelope is invalid") from error
    expected_envelope = {
        "content": [{"type": "text", "text": '{"entities":[]}'}],
        "isError": False,
        "success": True,
    }
    if envelope != expected_envelope:
        raise AcceptanceError("get_selection did not prove the exact empty selection")


def _validate_exit(document: Mapping[str, Any], prepared: Mapping[str, Any], now: datetime) -> None:
    expected = {
        "schema_version": 1,
        "kind": f"{KIND}.exit",
        "run_id": prepared["run_id"],
        "status": "stopped",
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise AcceptanceError(f"bridge-exit {key.replace('_', ' ')} differs")
    _timestamp(document.get("created_at"), "bridge-exit", now=now)


def _raw_documents(
    *,
    repo_root: Path,
    acceptance_dir: Path,
    expected_commit: str,
    expected_dispatcher: str,
    now: datetime,
    expected_github_run_id: int | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    prepared = _validate_prepared(
        repo_root=repo_root,
        acceptance_dir=acceptance_dir,
        expected_commit=expected_commit,
        expected_dispatcher=expected_dispatcher,
        now=now,
        expected_github_run_id=expected_github_run_id,
    )
    documents = {
        "bridge-ready.json": _read_json(acceptance_dir / "bridge-ready.json", "bridge-ready marker"),
        "mcp-session.json": _read_json(acceptance_dir / "mcp-session.json", "MCP session"),
        "bridge-exit.json": _read_json(acceptance_dir / "bridge-exit.json", "bridge-exit marker"),
    }
    _validate_ready(documents["bridge-ready.json"], prepared, now)
    _validate_session(documents["mcp-session.json"], prepared, now)
    _validate_exit(documents["bridge-exit.json"], prepared, now)
    ready_at = _timestamp(documents["bridge-ready.json"]["created_at"], "bridge-ready")
    started_at = _timestamp(documents["mcp-session.json"]["started_at"], "MCP session start")
    completed_at = _timestamp(
        documents["mcp-session.json"]["completed_at"], "MCP session completion"
    )
    exit_at = _timestamp(documents["bridge-exit.json"]["created_at"], "bridge-exit")
    if not ready_at <= started_at <= completed_at <= exit_at:
        raise AcceptanceError("install acceptance lifecycle timestamps are out of order")
    return prepared, documents


def collect(
    *, repo_root: Path, acceptance_dir: Path, expected_commit: str, expected_dispatcher: str, now: datetime | None = None
) -> Path:
    """Bind the fixed raw install artifacts after validating their semantics."""

    current = now or datetime.now(timezone.utc)
    prepared, _documents = _raw_documents(
        repo_root=repo_root,
        acceptance_dir=acceptance_dir,
        expected_commit=expected_commit,
        expected_dispatcher=expected_dispatcher,
        now=current,
    )
    artifacts = {
        name: _proof(acceptance_dir / name, f"install acceptance {name}")
        for name in RAW_NAMES
    }
    evidence = {
        "$schema": "evidence.schema.json",
        "schema_version": 1,
        "kind": KIND,
        "status": "pass",
        "created_at": current.isoformat(),
        "producer": prepared["producer"],
        "run_id": prepared["run_id"],
        "commit": prepared["commit"],
        "version": prepared["version"],
        "operator": prepared["operator"],
        "dispatcher": prepared["dispatcher"],
        "candidates": prepared["candidates"],
        "artifacts": artifacts,
    }
    output = acceptance_dir / "evidence.json"
    _write_json(output, evidence)
    return output


def validate(
    *,
    repo_root: Path,
    acceptance_dir: Path,
    evidence_path: Path,
    rbz_path: Path,
    wheel_path: Path,
    sdist_path: Path,
    expected_commit: str,
    expected_dispatcher: str,
    expected_github_run_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate the public evidence, raw files, and exact candidate bytes."""

    current = now or datetime.now(timezone.utc)
    prepared, _documents = _raw_documents(
        repo_root=repo_root,
        acceptance_dir=acceptance_dir,
        expected_commit=expected_commit,
        expected_dispatcher=expected_dispatcher,
        now=current,
        expected_github_run_id=expected_github_run_id,
    )
    evidence = _read_json(evidence_path, "install acceptance evidence")
    expected_keys = {
        "$schema",
        "schema_version",
        "kind",
        "status",
        "created_at",
        "producer",
        "run_id",
        "commit",
        "version",
        "operator",
        "dispatcher",
        "candidates",
        "artifacts",
    }
    if set(evidence) != expected_keys:
        raise AcceptanceError("evidence fields differ from the schema")
    expected_values = {
        "$schema": "evidence.schema.json",
        "schema_version": 1,
        "kind": KIND,
        "status": "pass",
        "producer": prepared["producer"],
        "run_id": prepared["run_id"],
        "commit": expected_commit,
        "version": _project_version(repo_root),
        "operator": prepared["operator"],
        "dispatcher": expected_dispatcher,
        "candidates": prepared["candidates"],
    }
    for key, value in expected_values.items():
        if evidence.get(key) != value:
            raise AcceptanceError(f"evidence {key.replace('_', ' ')} differs")
    _timestamp(evidence.get("created_at"), "install acceptance evidence", now=current)
    artifacts = evidence.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(RAW_NAMES):
        raise AcceptanceError("evidence artifact inventory differs")
    for name in RAW_NAMES:
        if artifacts[name] != _proof(acceptance_dir / name, f"install acceptance {name}"):
            raise AcceptanceError(f"evidence artifact proof differs for {name}")
    supplied = {
        "rbz": _proof(rbz_path, "candidate RBZ"),
        "wheel": _proof(wheel_path, "candidate wheel"),
        "sdist": _proof(sdist_path, "candidate source distribution"),
    }
    if supplied != prepared["candidates"]:
        raise AcceptanceError("supplied candidate artifacts differ")
    return evidence


def _wait_for(path: Path, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_symlink():
            raise AcceptanceError(f"{label} must be a regular file")
        if path.is_file():
            return
        time.sleep(0.1)
    raise AcceptanceError(f"timed out waiting for {label}")


def ensure_stop_marker(*, acceptance_dir: Path, run_id: str) -> Path:
    """Create or verify the one identity-bound stop marker without following links."""

    acceptance = _safe_directory(acceptance_dir, "install acceptance directory")
    if not isinstance(run_id, str) or not SHA256_PATTERN.fullmatch(run_id):
        raise AcceptanceError("stop marker run ID is invalid")
    stop = acceptance / "stop"
    expected = run_id.encode("ascii") + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(stop, flags, 0o600)
    except FileExistsError:
        if _regular_bytes(stop, "stop marker") != expected:
            raise AcceptanceError("stop marker identity differs")
    except OSError as error:
        raise AcceptanceError("stop marker could not be created safely") from error
    else:
        with os.fdopen(descriptor, "wb") as output:
            output.write(expected)
    return stop


async def _run_mcp(prepared: Mapping[str, Any]) -> dict[str, Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    started = datetime.now(timezone.utc).isoformat()
    config = prepared.get("mcp_host_config")
    if not isinstance(config, dict):
        raise AcceptanceError("MCP host launch config is missing")
    command = config.get("command")
    arguments = config.get("args")
    configured_environment = config.get("environment")
    if (
        not isinstance(command, str)
        or Path(command).name not in {"sketchup-mcp", "sketchup-mcp.exe"}
        or arguments != []
        or not isinstance(configured_environment, dict)
        or set(configured_environment) != {"SKETCHUP_MCP_BRIDGE_PORT"}
        or not isinstance(configured_environment["SKETCHUP_MCP_BRIDGE_PORT"], str)
    ):
        raise AcceptanceError("MCP host launch config is invalid")
    actual_command = Path(sys.executable).parent / Path(command).name
    _regular_bytes(actual_command, "installed sketchup-mcp console script")
    server = StdioServerParameters(
        command=str(actual_command),
        args=arguments,
        env=dict(configured_environment),
    )
    async with stdio_client(server) as streams:
        async with ClientSession(*streams) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("get_selection", {})
    return {
        "schema_version": 1,
        "kind": f"{KIND}.mcp_session",
        "run_id": prepared["run_id"],
        "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "initialized": True,
        "initialize_result": initialized.model_dump(mode="json", by_alias=True, exclude_none=True),
        "tools": [tool.name for tool in tools.tools],
        "call": {
            "name": "get_selection",
            "arguments": {},
            "raw_call_tool_result": result.model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
        },
    }


def collect_live(
    *, repo_root: Path, acceptance_dir: Path, expected_commit: str, expected_dispatcher: str, timeout: float
) -> Path:
    """Run the official SDK client, then request the fixed graceful shutdown."""

    prepared = _validate_prepared(
        repo_root=repo_root,
        acceptance_dir=acceptance_dir,
        expected_commit=expected_commit,
        expected_dispatcher=expected_dispatcher,
        now=datetime.now(timezone.utc),
    )
    ready = acceptance_dir / "bridge-ready.json"
    exit_marker = acceptance_dir / "bridge-exit.json"
    try:
        _wait_for(ready, timeout, "bridge-ready marker")
        _write_json(acceptance_dir / "mcp-session.json", asyncio.run(_run_mcp(prepared)))
    finally:
        ensure_stop_marker(
            acceptance_dir=acceptance_dir, run_id=str(prepared["run_id"])
        )
    _wait_for(exit_marker, timeout, "bridge-exit marker")
    return collect(
        repo_root=repo_root,
        acceptance_dir=acceptance_dir,
        expected_commit=expected_commit,
        expected_dispatcher=expected_dispatcher,
    )


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    actions = command.add_subparsers(dest="action", required=True)
    prepare_parser = actions.add_parser("prepare")
    prepare_parser.add_argument("--run-context", type=Path, required=True)
    prepare_parser.add_argument("--rbz", type=Path, required=True)
    prepare_parser.add_argument("--wheel", type=Path, required=True)
    prepare_parser.add_argument("--sdist", type=Path, required=True)
    prepare_parser.add_argument("--commit", required=True)
    prepare_parser.add_argument("--dispatcher", required=True)
    prepare_parser.add_argument("--github-run-id", type=int, required=True)
    prepare_parser.add_argument("--port", type=int, required=True)
    prepare_parser.add_argument("--os-version", required=True)
    prepare_parser.add_argument("--github-output", type=Path)
    collect_parser = actions.add_parser("collect")
    collect_parser.add_argument("--acceptance-dir", type=Path, required=True)
    collect_parser.add_argument("--commit", required=True)
    collect_parser.add_argument("--dispatcher", required=True)
    collect_parser.add_argument("--timeout", type=float, default=90.0)
    stop_parser = actions.add_parser("signal-stop")
    stop_parser.add_argument("--acceptance-dir", type=Path, required=True)
    stop_parser.add_argument("--run-id", required=True)
    validate_parser = actions.add_parser("validate")
    validate_parser.add_argument("--acceptance-dir", type=Path, required=True)
    validate_parser.add_argument("--evidence", type=Path, required=True)
    validate_parser.add_argument("--rbz", type=Path, required=True)
    validate_parser.add_argument("--wheel", type=Path, required=True)
    validate_parser.add_argument("--sdist", type=Path, required=True)
    validate_parser.add_argument("--commit", required=True)
    validate_parser.add_argument("--dispatcher", required=True)
    validate_parser.add_argument("--github-run-id", type=int, required=True)
    return command


def main() -> int:
    arguments = parser().parse_args()
    try:
        if arguments.action == "prepare":
            acceptance = prepare(
                repo_root=arguments.repo_root,
                run_context=arguments.run_context,
                rbz_path=arguments.rbz,
                wheel_path=arguments.wheel,
                sdist_path=arguments.sdist,
                commit=arguments.commit,
                dispatcher=arguments.dispatcher,
                github_run_id=arguments.github_run_id,
                port=arguments.port,
                os_version=arguments.os_version,
                python_executable=Path(sys.executable),
                enforce_installed_distribution=True,
                probe_port=True,
            )
            if arguments.github_output:
                with arguments.github_output.open("a", encoding="utf-8") as output:
                    output.write(f"acceptance_dir={acceptance.resolve()}\n")
                    output.write(f"acceptance_startup={(acceptance / 'startup.rb').resolve()}\n")
            print(acceptance)
        elif arguments.action == "collect":
            print(
                collect_live(
                    repo_root=arguments.repo_root,
                    acceptance_dir=arguments.acceptance_dir,
                    expected_commit=arguments.commit,
                    expected_dispatcher=arguments.dispatcher,
                    timeout=arguments.timeout,
                )
            )
        elif arguments.action == "signal-stop":
            print(
                ensure_stop_marker(
                    acceptance_dir=arguments.acceptance_dir,
                    run_id=arguments.run_id,
                )
            )
        else:
            validate(
                repo_root=arguments.repo_root,
                acceptance_dir=arguments.acceptance_dir,
                evidence_path=arguments.evidence,
                rbz_path=arguments.rbz,
                wheel_path=arguments.wheel,
                sdist_path=arguments.sdist,
                expected_commit=arguments.commit,
                expected_dispatcher=arguments.dispatcher,
                expected_github_run_id=arguments.github_run_id,
            )
            print("Install acceptance evidence: PASS")
    except (AcceptanceError, OSError) as error:
        print(f"Install acceptance evidence: FAIL ({error})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
