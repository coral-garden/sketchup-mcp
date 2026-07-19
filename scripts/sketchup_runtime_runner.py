#!/usr/bin/env python3
"""Prepare, install, collect, and clean a protected SketchUp runtime run."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import subprocess
import sys
from typing import Any, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from extension_package import PackageError, project_version  # noqa: E402
from sketchup_runtime_evidence import (  # noqa: E402
    EvidenceError,
    RunContext,
    collect_evidence,
    discover_raw_artifacts,
    package_manifest,
    prepare_run,
    validate_checkout,
    validate_evidence,
)


EXPECTED_REPOSITORY = "coral-garden/sketchup-mcp"
REPO_ROOT = SCRIPT_ROOT.parent
PLUGINS_SENTINEL = ".sketchup-mcp-runtime-runner.json"
SENTINEL_DOCUMENT = {
    "schema_version": 1,
    "kind": "sketchup_mcp.protected_runtime_plugins",
    "repository": EXPECTED_REPOSITORY,
}
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
DISPATCHER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
BOOTSTRAP_INPUT_FILENAME = "candidate-install-input.json"
BOOTSTRAP_INPUT_KIND = "sketchup_mcp.candidate_install_bootstrap"


class RunnerError(ValueError):
    """The protected runner cannot safely execute the requested lifecycle."""


def _plugins_root(path: Path) -> Path:
    if not path.is_absolute():
        raise RunnerError("Plugins directory must be absolute")
    if path.is_symlink() or not path.is_dir() or path.name.casefold() != "plugins":
        raise RunnerError("Plugins directory is missing or unsafe")
    root = path.resolve()
    sentinel = root / PLUGINS_SENTINEL
    try:
        if sentinel.is_symlink() or not sentinel.is_file():
            raise RunnerError("protected runner Plugins sentinel is missing")
        document = json.loads(sentinel.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunnerError("protected runner Plugins sentinel is invalid") from error
    if document != SENTINEL_DOCUMENT:
        raise RunnerError("protected runner Plugins sentinel differs")
    return root


def clean_extension_install(plugins_dir: Path) -> tuple[str, ...]:
    """Remove only this extension's exact install targets from a sentinel root."""

    root = _plugins_root(plugins_dir)
    targets = (("su_mcp.rb", root / "su_mcp.rb"), ("su_mcp/", root / "su_mcp"))
    removable: list[tuple[str, Path]] = []
    for name, target in targets:
        if target.parent != root:
            raise AssertionError("candidate cleanup target escaped Plugins root")
        if not os.path.lexists(target):
            continue
        try:
            metadata = os.lstat(target)
        except OSError as error:
            raise RunnerError(f"candidate cleanup target is unreadable: {name}") from error
        if name == "su_mcp.rb":
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise RunnerError("candidate loader must be a non-symlink regular file")
        elif (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or _is_junction_or_reparse(target, metadata)
            or os.path.ismount(target)
        ):
            raise RunnerError(
                "candidate support must be a non-symlink, non-reparse, "
                "non-mount directory"
            )
        removable.append((name, target))

    removed = []
    for name, target in removable:
        if name == "su_mcp.rb":
            target.unlink()
        else:
            shutil.rmtree(target)
        removed.append(name)
    return tuple(removed)


def _is_junction_or_reparse(path: Path, metadata: os.stat_result) -> bool:
    """Recognize Windows junctions on both Python 3.10 and Python 3.12+."""

    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None:
        try:
            if is_junction():
                return True
        except OSError as error:
            raise RunnerError("candidate support junction state is unreadable") from error
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def sha256_file(path: Path) -> str:
    """Hash one regular file used by the trusted runner protocol."""

    if path.is_symlink() or not path.is_file():
        raise RunnerError(f"required regular file is missing: {path.name}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise RunnerError(f"{label} must be a regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunnerError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise RunnerError(f"{label} must be a JSON object")
    return value


def _serialized_absolute_path(value: object, *, name: str, label: str) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise RunnerError(f"{label} path is invalid")
    paths = (PurePosixPath(value), PureWindowsPath(value))
    if not any(path.is_absolute() and path.name == name for path in paths):
        raise RunnerError(f"{label} path is invalid")
    return value


def _validated_identity(
    *, commit: str, run_id: str, version: str, operator: str, dispatcher: str
) -> None:
    if not COMMIT_PATTERN.fullmatch(commit):
        raise RunnerError("commit must be a full lowercase Git SHA")
    if not SHA256_PATTERN.fullmatch(run_id):
        raise RunnerError("run ID must be a lowercase SHA-256 value")
    if not VERSION_PATTERN.fullmatch(version):
        raise RunnerError("project version is invalid")
    if (
        operator != operator.strip()
        or not 2 <= len(operator) <= 100
        or not operator.isprintable()
        or operator.casefold() in {"automatic", "default", "github.actor"}
    ):
        raise RunnerError("named physical operator is invalid")
    if not DISPATCHER_PATTERN.fullmatch(dispatcher):
        raise RunnerError("GitHub dispatcher is invalid")


def _validated_manifest(installed_files: dict[str, str]) -> dict[str, str]:
    if not installed_files or "su_mcp.rb" not in installed_files:
        raise RunnerError("candidate package manifest is incomplete")
    if "su_mcp/sketchup_adapter.rb" not in installed_files:
        raise RunnerError("candidate adapter is missing from the package manifest")
    for name, digest in installed_files.items():
        parts = Path(name).parts
        if (
            name != Path(name).as_posix()
            or not parts
            or any(part in {"", ".", ".."} for part in parts)
            or not (name == "su_mcp.rb" or name.startswith("su_mcp/"))
        ):
            raise RunnerError("candidate package manifest contains an unsafe path")
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise RunnerError("candidate package manifest contains an invalid digest")
    return dict(sorted(installed_files.items()))


@dataclass(frozen=True)
class CandidateInstallIdentity:
    """Validated immutable identity and attestation for one candidate install."""

    commit: str
    run_id: str
    version: str
    operator: str
    dispatcher: str
    installed_files: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _validated_identity(
            commit=self.commit,
            run_id=self.run_id,
            version=self.version,
            operator=self.operator,
            dispatcher=self.dispatcher,
        )
        expected = tuple(_validated_manifest(dict(self.installed_files)).items())
        if self.installed_files != expected:
            raise RunnerError("candidate package manifest is not canonical")

    @classmethod
    def create(
        cls,
        *,
        commit: str,
        run_id: str,
        version: str,
        operator: str,
        dispatcher: str,
        installed_files: dict[str, str],
    ) -> "CandidateInstallIdentity":
        manifest = _validated_manifest(installed_files)
        return cls(
            commit=commit,
            run_id=run_id,
            version=version,
            operator=operator,
            dispatcher=dispatcher,
            installed_files=tuple(manifest.items()),
        )

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "sketchup_mcp.candidate_install",
            "commit": self.commit,
            "run_id": self.run_id,
            "version": self.version,
            "operator": self.operator,
            "dispatcher": self.dispatcher,
            "candidate_install_confirmed": True,
            "installed_files": dict(self.installed_files),
        }


INSTALL_BOOTSTRAP_SOURCE = """# Static SketchUp MCP candidate installer. Do not edit.
require 'digest'
require 'find'
require 'json'
require 'time'

bootstrap_path = File.expand_path(__FILE__)
workspace = File.dirname(bootstrap_path)
input_path = File.expand_path('candidate-install-input.json', __dir__)
marker_path = File.join(workspace, 'candidate-install.json')
install_log_path = File.join(workspace, 'candidate-install.log')
events = []
identity = {}
input_proof = nil

def regular_file_proof(path)
  stat = File.lstat(path)
  raise 'proof_source_symlink' if stat.symlink?
  raise 'proof_source_not_regular' unless stat.file?
  {
    'filename' => File.basename(path),
    'sha256' => Digest::SHA256.file(path).hexdigest,
    'size' => stat.size
  }
end

def candidate_path(root, member)
  path = File.expand_path(File.join(root, *member.split('/')))
  prefix = root.end_with?(File::SEPARATOR) ? root : "#{root}#{File::SEPARATOR}"
  raise 'installed_path_outside_plugins' unless path.start_with?(prefix)
  path
end

def same_directory_identity?(actual, configured)
  return File.identical?(actual, configured) if File.respond_to?(:identical?)

  # Legacy Ruby fallback: require both canonical-path and device/inode identity.
  actual_stat = File.stat(actual)
  configured_stat = File.stat(configured)
  File.realpath(actual) == File.realpath(configured) &&
    actual_stat.dev == configured_stat.dev &&
    actual_stat.ino == configured_stat.ino
rescue StandardError
  raise 'runtime_plugins_identity_unavailable'
end

begin
  input_bytes = File.binread(input_path)
  input_proof = regular_file_proof(input_path)
  input_document = JSON.parse(input_bytes)
  unless input_document.fetch('schema_version') == 1
    raise 'bootstrap_input_schema_differs'
  end
  unless input_document.fetch('kind') == 'sketchup_mcp.candidate_install_bootstrap'
    raise 'bootstrap_input_kind_differs'
  end
  identity = input_document.fetch('identity')
  rbz_path = input_document.fetch('rbz_path')
  plugins_dir = input_document.fetch('plugins_dir')
  begin
    actual_plugins_dir = Sketchup.find_support_file('Plugins')
  rescue StandardError
    raise 'runtime_plugins_unavailable'
  end
  unless actual_plugins_dir.is_a?(String) &&
         !actual_plugins_dir.empty? &&
         File.directory?(actual_plugins_dir)
    raise 'runtime_plugins_unavailable'
  end
  unless same_directory_identity?(actual_plugins_dir, plugins_dir)
    raise 'runtime_plugins_identity_differs'
  end
  rbz_proof = regular_file_proof(rbz_path)
  raise 'candidate_rbz_proof_differs' unless rbz_proof == input_document.fetch('rbz')
  preclean_path = File.join(workspace, 'candidate-preclean.json')
  preclean_proof = regular_file_proof(preclean_path)
  unless preclean_proof == input_document.fetch('preclean')
    raise 'candidate_preclean_proof_differs'
  end

  installed = Sketchup.install_from_archive(rbz_path, false)
  raise 'install_from_archive_returned_false' unless installed
  events << 'install_from_archive:success'

  expected = identity.fetch('installed_files')
  installed_files = [File.join(plugins_dir, 'su_mcp.rb')]
  support_root = File.join(plugins_dir, 'su_mcp')
  Find.find(support_root) do |path|
    installed_files << path unless path == support_root
  end
  installed_files.sort!
  actual = installed_files.filter_map do |path|
    stat = File.lstat(path)
    raise 'installed_source_symlink' if stat.symlink?
    next if stat.directory?
    raise 'installed_source_not_regular' unless stat.file?
    prefix = "#{plugins_dir}#{File::SEPARATOR}"
    member = File.expand_path(path).delete_prefix(prefix).tr('\\\\', '/')
    [member, Digest::SHA256.file(path).hexdigest]
  end.to_h.sort.to_h
  raise 'installed_manifest_differs' unless actual == expected
  events << 'installed_manifest:verified'

  loader = candidate_path(plugins_dir, 'su_mcp.rb')
  load loader
  raise 'installed_version_unavailable' unless defined?(SU_MCP::VERSION)
  raise 'installed_version_differs' unless SU_MCP::VERSION == identity.fetch('version')
  unless defined?(SU_MCP::SketchupAdapter)
    raise 'installed_adapter_not_loaded'
  end
  source = SU_MCP::SketchupAdapter.instance_method(:initialize).source_location
  raise 'installed_adapter_source_unavailable' unless source
  adapter_path = candidate_path(plugins_dir, 'su_mcp/sketchup_adapter.rb')
  unless File.expand_path(source.fetch(0)) == adapter_path
    raise 'installed_adapter_source_differs'
  end
  expected_adapter = expected.fetch('su_mcp/sketchup_adapter.rb')
  installed_adapter = Digest::SHA256.file(adapter_path).hexdigest
  raise 'installed_adapter_bytes_differ' unless installed_adapter == expected_adapter
  events << 'candidate_entrypoint:loaded'
  events << 'candidate_install:success'
  File.write(install_log_path, events.join("\\n") + "\\n", mode: 'wb')

  marker = identity.merge(
    'status' => 'success',
    'created_at' => Time.now.iso8601,
    'rbz' => rbz_proof,
    'preclean_sha256' => preclean_proof.fetch('sha256'),
    'bootstrap_input' => input_proof,
    'bootstrap_sha256' => Digest::SHA256.file(bootstrap_path).hexdigest,
    'install_log_sha256' => Digest::SHA256.file(install_log_path).hexdigest,
    'loaded_adapter_sha256' => expected_adapter
  )
  File.write(marker_path, JSON.pretty_generate(marker) + "\\n", mode: 'wb')
rescue StandardError => error
  events << "candidate_install:failure:#{error.message}"
  File.write(install_log_path, events.join("\\n") + "\\n", mode: 'wb')
  failure = identity.merge(
    'status' => 'failure',
    'created_at' => Time.now.iso8601,
    'error' => error.message,
    'bootstrap_sha256' => Digest::SHA256.file(bootstrap_path).hexdigest,
    'install_log_sha256' => Digest::SHA256.file(install_log_path).hexdigest
  )
  failure['bootstrap_input'] = input_proof unless input_proof.nil?
  File.write(marker_path, JSON.pretty_generate(failure) + "\\n", mode: 'wb')
  warn "SketchUp MCP candidate install failed: #{error.message}"
  raise
end
"""


def write_install_bootstrap(
    *,
    workspace: Path,
    rbz_path: Path,
    plugins_dir: Path,
    identity: CandidateInstallIdentity,
    preclean_receipt: Path,
) -> Path:
    """Write the Ruby startup that installs and loads one exact RBZ candidate."""

    root = _plugins_root(plugins_dir)
    destination = workspace.resolve()
    if workspace.is_symlink() or not destination.is_dir():
        raise RunnerError("runtime workspace is missing or unsafe")
    rbz = rbz_path.resolve()
    receipt = preclean_receipt.resolve()
    bootstrap = destination / "candidate-install.rb"
    bootstrap_input = destination / BOOTSTRAP_INPUT_FILENAME
    marker = destination / "candidate-install.json"
    install_log = destination / "candidate-install.log"
    if any(
        path.exists() or path.is_symlink()
        for path in (bootstrap, bootstrap_input, marker, install_log)
    ):
        raise RunnerError("candidate installation artifacts already exist")
    input_document = {
        "schema_version": 1,
        "kind": BOOTSTRAP_INPUT_KIND,
        "identity": identity.document(),
        "rbz_path": str(rbz),
        "plugins_dir": str(root),
        "rbz": {
            "filename": rbz.name,
            "sha256": sha256_file(rbz),
            "size": rbz.stat().st_size,
        },
        "preclean": {
            "filename": receipt.name,
            "sha256": sha256_file(receipt),
            "size": receipt.stat().st_size,
        },
    }
    bootstrap_input.write_text(
        json.dumps(input_document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    bootstrap.write_text(INSTALL_BOOTSTRAP_SOURCE, encoding="utf-8")
    return bootstrap


def prepare_runtime(
    *,
    repo_root: Path,
    artifact_dir: Path,
    rbz_path: Path,
    plugins_dir: Path,
    commit: str,
    operator: str,
    dispatcher: str,
    licensed_runner_confirmed: bool,
    single_testup_process_confirmed: bool,
    candidate_install_confirmed: bool,
) -> Path:
    """Prepare an attested runtime workspace and exact candidate installation."""

    operator = operator.strip()
    _validated_identity(
        commit=commit,
        run_id="0" * 64,
        version="0.0.0",
        operator=operator,
        dispatcher=dispatcher,
    )
    if not licensed_runner_confirmed:
        raise RunnerError("licensed-runner manual attestation is required")
    if not single_testup_process_confirmed:
        raise RunnerError("single-TestUp-process manual attestation is required")
    if not candidate_install_confirmed:
        raise RunnerError("exact-candidate-install manual attestation is required")
    root = repo_root.resolve()
    validate_checkout(root, commit)
    version = project_version(root)
    context_path = prepare_run(
        repo_root=root,
        artifact_dir=artifact_dir,
        rbz_path=rbz_path,
        commit=commit,
        operator=operator,
        licensed_runner_confirmed=True,
        single_testup_process_confirmed=True,
    )
    context = RunContext.from_document(_read_json(context_path, "run context"))
    removed_targets = clean_extension_install(plugins_dir)
    workspace = context_path.parent
    retained_rbz = workspace / rbz_path.name
    if retained_rbz.exists() or retained_rbz.is_symlink():
        raise RunnerError("retained candidate RBZ already exists")
    shutil.copyfile(rbz_path, retained_rbz)
    sentinel = plugins_dir.resolve() / PLUGINS_SENTINEL
    receipt = workspace / "candidate-preclean.json"
    receipt_document = {
        "schema_version": 1,
        "kind": "sketchup_mcp.candidate_preclean",
        "created_at": datetime.now().astimezone().isoformat(),
        "commit": commit,
        "run_id": context.run_id,
        "operator": operator,
        "dispatcher": dispatcher,
        "candidate_install_confirmed": True,
        "cleanup_targets": ["su_mcp.rb", "su_mcp/"],
        "removed_targets": list(removed_targets),
        "plugins_sentinel_sha256": sha256_file(sentinel),
    }
    receipt.write_text(
        json.dumps(receipt_document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    identity = CandidateInstallIdentity.create(
        commit=commit,
        run_id=context.run_id,
        version=version,
        operator=operator,
        dispatcher=dispatcher,
        installed_files=package_manifest(retained_rbz),
    )
    write_install_bootstrap(
        workspace=workspace,
        rbz_path=retained_rbz,
        plugins_dir=plugins_dir,
        identity=identity,
        preclean_receipt=receipt,
    )
    return context_path


def validate_installation_bundle(
    *,
    repo_root: Path = REPO_ROOT,
    run_context: Path,
    rbz_path: Path,
    expected_commit: str,
    expected_dispatcher: str | None = None,
) -> dict[str, Any]:
    """Validate the independent exact-candidate install marker and its inputs."""

    context = RunContext.from_document(_read_json(run_context, "run context"))
    workspace = run_context.resolve().parent
    if workspace.name != f"run-{context.run_id}":
        raise RunnerError("candidate installation workspace differs from the run ID")
    marker_path = workspace / "candidate-install.json"
    log_path = workspace / "candidate-install.log"
    bootstrap_path = workspace / "candidate-install.rb"
    bootstrap_input_path = workspace / BOOTSTRAP_INPUT_FILENAME
    receipt_path = workspace / "candidate-preclean.json"
    marker = _read_json(marker_path, "candidate installation marker")
    bootstrap_input = _read_json(bootstrap_input_path, "candidate bootstrap input")
    receipt = _read_json(receipt_path, "candidate pre-clean receipt")
    manifest = package_manifest(rbz_path)

    dispatcher = marker.get("dispatcher")
    if not isinstance(dispatcher, str) or not DISPATCHER_PATTERN.fullmatch(dispatcher):
        raise RunnerError("candidate installation dispatcher is invalid")
    if expected_dispatcher is not None and dispatcher != expected_dispatcher:
        raise RunnerError("candidate installation dispatcher differs from GitHub run")
    identity = CandidateInstallIdentity.create(
        commit=expected_commit,
        run_id=context.run_id,
        version=project_version(repo_root),
        operator=context.operator,
        dispatcher=dispatcher,
        installed_files=manifest,
    )
    rbz_proof = {
        "filename": rbz_path.name,
        "sha256": sha256_file(rbz_path),
        "size": rbz_path.stat().st_size,
    }
    preclean_proof = {
        "filename": receipt_path.name,
        "sha256": sha256_file(receipt_path),
        "size": receipt_path.stat().st_size,
    }
    serialized_rbz_path = _serialized_absolute_path(
        bootstrap_input.get("rbz_path"), name=rbz_path.name, label="candidate RBZ"
    )
    serialized_plugins_dir = bootstrap_input.get("plugins_dir")
    if not isinstance(serialized_plugins_dir, str):
        raise RunnerError("candidate bootstrap input Plugins path is invalid")
    plugin_paths = (
        PurePosixPath(serialized_plugins_dir),
        PureWindowsPath(serialized_plugins_dir),
    )
    if not any(
        path.is_absolute() and path.name.casefold() == "plugins"
        for path in plugin_paths
    ):
        raise RunnerError("candidate bootstrap input Plugins path is invalid")
    expected_input = {
        "schema_version": 1,
        "kind": BOOTSTRAP_INPUT_KIND,
        "identity": identity.document(),
        "rbz_path": serialized_rbz_path,
        "plugins_dir": serialized_plugins_dir,
        "rbz": rbz_proof,
        "preclean": preclean_proof,
    }
    if bootstrap_input != expected_input:
        raise RunnerError("candidate bootstrap input differs")

    expected_values = {
        "schema_version": 1,
        "kind": "sketchup_mcp.candidate_install",
        "status": "success",
        "commit": expected_commit,
        "run_id": context.run_id,
        "version": identity.version,
        "operator": context.operator,
        "candidate_install_confirmed": True,
        "installed_files": manifest,
        "preclean_sha256": preclean_proof["sha256"],
        "bootstrap_input": {
            "filename": BOOTSTRAP_INPUT_FILENAME,
            "sha256": sha256_file(bootstrap_input_path),
            "size": bootstrap_input_path.stat().st_size,
        },
        "bootstrap_sha256": sha256_file(bootstrap_path),
        "install_log_sha256": sha256_file(log_path),
        "loaded_adapter_sha256": manifest.get("su_mcp/sketchup_adapter.rb"),
        "rbz": rbz_proof,
    }
    for name, expected in expected_values.items():
        if marker.get(name) != expected:
            raise RunnerError(f"candidate installation {name.replace('_', ' ')} differs")
    created_at = marker.get("created_at")
    try:
        if not isinstance(created_at, str) or datetime.fromisoformat(
            created_at.replace("Z", "+00:00")
        ).tzinfo is None:
            raise ValueError
    except ValueError as error:
        raise RunnerError("candidate installation timestamp is invalid") from error
    expected_receipt = {
        "schema_version": 1,
        "kind": "sketchup_mcp.candidate_preclean",
        "commit": expected_commit,
        "run_id": context.run_id,
        "operator": context.operator,
        "dispatcher": dispatcher,
        "candidate_install_confirmed": True,
        "cleanup_targets": ["su_mcp.rb", "su_mcp/"],
    }
    for name, expected in expected_receipt.items():
        if receipt.get(name) != expected:
            raise RunnerError(f"candidate pre-clean {name.replace('_', ' ')} differs")
    removed = receipt.get("removed_targets")
    if removed not in (
        [],
        ["su_mcp.rb"],
        ["su_mcp/"],
        ["su_mcp.rb", "su_mcp/"],
    ):
        raise RunnerError("candidate pre-clean removed targets are invalid")
    sentinel_sha = receipt.get("plugins_sentinel_sha256")
    if not isinstance(sentinel_sha, str) or not SHA256_PATTERN.fullmatch(sentinel_sha):
        raise RunnerError("candidate pre-clean sentinel digest is invalid")
    expected_log = (
        "install_from_archive:success\n"
        "installed_manifest:verified\n"
        "candidate_entrypoint:loaded\n"
        "candidate_install:success\n"
    )
    if log_path.read_text(encoding="utf-8") != expected_log:
        raise RunnerError("candidate installation log differs")
    return marker


def collect_runtime(
    *, repo_root: Path, run_context: Path, rbz_path: Path, commit: str
) -> Path:
    """Collect and validate #15 evidence plus the candidate install marker."""

    root = repo_root.resolve()
    validate_checkout(root, commit)
    workspace = run_context.resolve().parent
    if rbz_path.resolve().parent != workspace:
        raise RunnerError("candidate RBZ is outside the prepared run workspace")
    validate_installation_bundle(
        repo_root=root,
        run_context=run_context,
        rbz_path=rbz_path,
        expected_commit=commit,
    )
    raw_paths = discover_raw_artifacts(run_context)
    evidence = collect_evidence(
        repo_root=root,
        raw_paths=raw_paths,
        rbz_path=rbz_path,
        commit=commit,
    )
    output = workspace / "evidence.json"
    if output.exists() or output.is_symlink():
        raise RunnerError("final runtime evidence already exists")
    output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    validate_evidence(
        evidence,
        repo_root=root,
        raw_paths=raw_paths,
        rbz_path=rbz_path,
        expected_commit=commit,
    )
    return output


def _write_github_output(path: Path, context_path: Path) -> None:
    workspace = context_path.resolve().parent
    context = RunContext.from_document(_read_json(context_path, "run context"))
    candidates = tuple(workspace.glob("sketchup-mcp-*.rbz"))
    if len(candidates) != 1:
        raise RunnerError("prepared workspace must contain exactly one candidate RBZ")
    values = {
        "run_context": context_path.resolve(),
        "workspace": workspace,
        "bootstrap": workspace / "candidate-install.rb",
        "testup_config": workspace / "testup-ci.generated.yml",
        "rbz": candidates[0],
        "run_id": context.run_id,
        "runtime_report": workspace / "runtime-report.json",
        "suite_marker": workspace / "suite-marker.json",
    }
    with path.open("a", encoding="utf-8") as output:
        for name, value in values.items():
            text = str(value)
            if "\n" in text or "\r" in text:
                raise RunnerError("GitHub output path contains a newline")
            output.write(f"{name}={text}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    subparsers = parser.add_subparsers(dest="action", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--artifact-dir", type=Path, required=True)
    prepare.add_argument("--rbz", type=Path, required=True)
    prepare.add_argument("--plugins-dir", type=Path, required=True)
    prepare.add_argument("--commit", required=True)
    prepare.add_argument("--operator", required=True)
    prepare.add_argument("--dispatcher", required=True)
    prepare.add_argument("--attest-licensed-runner", action="store_true", required=True)
    prepare.add_argument(
        "--attest-single-testup-process", action="store_true", required=True
    )
    prepare.add_argument("--attest-candidate-install", action="store_true", required=True)
    prepare.add_argument("--github-output", type=Path)

    collect = subparsers.add_parser("collect")
    collect.add_argument("--run-context", type=Path, required=True)
    collect.add_argument("--rbz", type=Path, required=True)
    collect.add_argument("--commit", required=True)

    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--plugins-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "prepare":
            context_path = prepare_runtime(
                repo_root=args.repo_root,
                artifact_dir=args.artifact_dir,
                rbz_path=args.rbz,
                plugins_dir=args.plugins_dir,
                commit=args.commit,
                operator=args.operator,
                dispatcher=args.dispatcher,
                licensed_runner_confirmed=args.attest_licensed_runner,
                single_testup_process_confirmed=args.attest_single_testup_process,
                candidate_install_confirmed=args.attest_candidate_install,
            )
            if args.github_output is not None:
                _write_github_output(args.github_output, context_path)
            print(context_path)
        elif args.action == "collect":
            print(
                collect_runtime(
                    repo_root=args.repo_root,
                    run_context=args.run_context,
                    rbz_path=args.rbz,
                    commit=args.commit,
                )
            )
        else:
            removed = clean_extension_install(args.plugins_dir)
            print("Candidate cleanup: " + (", ".join(removed) if removed else "clean"))
    except (
        EvidenceError,
        PackageError,
        RunnerError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        print(f"SketchUp runtime runner failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
