#!/usr/bin/env python3
"""Run the repository's verification gates through one entry point."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from trusted_release import (  # noqa: E402
    TrustedReleaseError,
    load_runtime_bundle,
    validate_trusted_run,
    validator_arguments,
)


COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


class VerificationError(ValueError):
    """A release input cannot satisfy the verification contract."""


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    source = str(REPO_ROOT / "src")
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source if not current else os.pathsep.join((source, current))
    )
    executable_directory = str(Path(sys.executable).absolute().parent)
    current_path = environment.get("PATH")
    environment["PATH"] = (
        executable_directory
        if not current_path
        else os.pathsep.join((executable_directory, current_path))
    )
    return environment


def _run(
    label: str,
    command: list[str],
    environment: dict[str, str],
) -> bool:
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=environment,
            check=False,
        )
    except FileNotFoundError as error:
        executable = error.filename or command[0]
        print(f"{label}: FAIL (required executable missing: {executable})")
        return False
    if completed.returncode == 0:
        return True
    print(f"{label}: FAIL (exit status {completed.returncode})")
    return False


def _read_report(path: Path, label: str) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"{label}: FAIL (machine-readable report missing)")
        return None
    except (OSError, json.JSONDecodeError) as error:
        print(f"{label}: FAIL (machine-readable report invalid: {error})")
        return None
    if not isinstance(value, dict):
        print(f"{label}: FAIL (machine-readable report must be a JSON object)")
        return None
    return value


def _counts(
    value: object,
    *,
    covered_key: str,
    total_key: str,
    label: str,
) -> tuple[int, int] | None:
    if not isinstance(value, dict):
        print(f"{label}: FAIL (coverage metrics are missing)")
        return None
    covered = value.get(covered_key)
    total = value.get(total_key)
    if (
        not isinstance(covered, int)
        or isinstance(covered, bool)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total <= 0
    ):
        print(f"{label}: FAIL (coverage counts are invalid)")
        return None
    if covered != total:
        print(f"{label}: FAIL (coverage is {covered}/{total}; required 100%)")
        return None
    return covered, total


def _python_metrics(path: Path) -> dict[str, tuple[int, int]] | None:
    report = _read_report(path, "Python coverage")
    if report is None:
        return None
    totals = report.get("totals")
    lines = _counts(
        totals,
        covered_key="covered_lines",
        total_key="num_statements",
        label="Python line coverage",
    )
    branches = _counts(
        totals,
        covered_key="covered_branches",
        total_key="num_branches",
        label="Python branch coverage",
    )
    if lines is None or branches is None:
        return None
    return {"lines": lines, "branches": branches}


def _ruby_metrics(path: Path) -> dict[str, tuple[int, int]] | None:
    report = _read_report(path, "Headless Ruby coverage")
    if report is None:
        return None
    if (
        report.get("schema_version") != 1
        or report.get("scope") != "headless_ruby"
        or report.get("thresholds") != {"lines": 100, "branches": 100}
    ):
        print("Headless Ruby coverage: FAIL (report contract differs)")
        return None
    lines = _counts(
        report.get("lines"),
        covered_key="covered",
        total_key="total",
        label="Headless Ruby line coverage",
    )
    branches = _counts(
        report.get("branches"),
        covered_key="covered",
        total_key="total",
        label="Headless Ruby branch coverage",
    )
    if lines is None or branches is None:
        return None
    return {"lines": lines, "branches": branches}


def _print_metrics(label: str, metrics: dict[str, tuple[int, int]]) -> None:
    lines = metrics["lines"]
    branches = metrics["branches"]
    print(
        f"{label}: lines {lines[0]}/{lines[1]}, "
        f"branches {branches[0]}/{branches[1]} (thresholds: 100%/100%)"
    )


def _coverage_document(
    metrics: dict[str, tuple[int, int]] | None,
) -> dict[str, object] | None:
    if metrics is None:
        return None
    return {
        "thresholds": {"lines": 100, "branches": 100},
        "lines": {
            "covered": metrics["lines"][0],
            "total": metrics["lines"][1],
        },
        "branches": {
            "covered": metrics["branches"][0],
            "total": metrics["branches"][1],
        },
    }


def _write_summary(
    path: Path,
    *,
    passed: bool,
    python_passed: bool,
    python_coverage: dict[str, tuple[int, int]] | None,
    python_reason: str | None,
    ruby_passed: bool,
    ruby_coverage: dict[str, tuple[int, int]] | None,
    ruby_reason: str | None,
) -> bool:
    python_scope = {
        "status": "pass" if python_passed else "fail",
        "coverage": _coverage_document(python_coverage),
    }
    ruby_scope = {
        "status": "pass" if ruby_passed else "fail",
        "coverage": _coverage_document(ruby_coverage),
    }
    if python_reason is not None:
        python_scope["reason"] = python_reason
    if ruby_reason is not None:
        ruby_scope["reason"] = ruby_reason
    document = {
        "schema_version": 1,
        "mode": "local",
        "status": "pass" if passed else "fail",
        "scopes": {
            "python": python_scope,
            "headless_ruby": ruby_scope,
            "sketchup_runtime": {"status": "external", "required": False},
        },
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        print(f"Verification report: FAIL (cannot write {path}: {error})")
        return False
    print(f"Verification report: {path}")
    return True


def verify_local(report_path: Path) -> int:
    environment = _environment()
    with tempfile.TemporaryDirectory(prefix="sketchup-mcp-verification-") as directory:
        reports = Path(directory)
        python_report = reports / "python-coverage.json"
        ruby_report = reports / "ruby-coverage.json"
        python_environment = environment.copy()
        python_environment["COVERAGE_FILE"] = str(reports / ".coverage")

        python_gate = _run(
            "Python coverage",
            [str(REPO_ROOT / "scripts/test_python_coverage.sh")],
            python_environment,
        )
        python_reported = python_gate and _run(
            "Python coverage report",
            [
                sys.executable,
                "-m",
                "coverage",
                "json",
                "--pretty-print",
                "-o",
                str(python_report),
            ],
            python_environment,
        )
        python_coverage = (
            _python_metrics(python_report) if python_reported else None
        )
        python_integration = _run(
            "Python integration",
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            environment,
        )

        ruby_gate = _run(
            "Headless Ruby coverage",
            [
                "ruby",
                "scripts/ruby_coverage.rb",
                "--json",
                str(ruby_report),
            ],
            environment,
        )
        ruby_coverage = _ruby_metrics(ruby_report) if ruby_gate else None
        ruby_integration = _run(
            "Headless Ruby integration",
            ["ruby", "-Itest", "test/headless.rb"],
            environment,
        )

    python_passed = python_coverage is not None and python_integration
    ruby_passed = ruby_coverage is not None and ruby_integration
    if not python_gate:
        python_reason = "coverage gate failed"
    elif not python_reported:
        python_reason = "coverage report command failed"
    elif python_coverage is None:
        python_reason = "machine-readable coverage report missing or invalid"
    elif not python_integration:
        python_reason = "integration suite failed"
    else:
        python_reason = None
    if not ruby_gate:
        ruby_reason = "coverage gate failed"
    elif ruby_coverage is None:
        ruby_reason = "machine-readable coverage report missing or invalid"
    elif not ruby_integration:
        ruby_reason = "integration suite failed"
    else:
        ruby_reason = None

    if python_coverage is not None:
        _print_metrics("Python coverage", python_coverage)
    if ruby_coverage is not None:
        _print_metrics("Headless Ruby coverage", ruby_coverage)
    print(f"Python: {'PASS' if python_passed else 'FAIL'}")
    print(f"Headless Ruby: {'PASS' if ruby_passed else 'FAIL'}")
    print("SketchUp runtime: EXTERNAL (not available in local verification)")
    passed = python_passed and ruby_passed
    report_written = _write_summary(
        report_path,
        passed=passed,
        python_passed=python_passed,
        python_coverage=python_coverage,
        python_reason=python_reason,
        ruby_passed=ruby_passed,
        ruby_coverage=ruby_coverage,
        ruby_reason=ruby_reason,
    )
    passed = passed and report_written
    print(f"Local verification: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise VerificationError(f"{label} is missing")
    return value


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise VerificationError(f"{label} is missing or is not a regular file")
    return path


def _head_commit(environment: dict[str, str]) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise VerificationError("git is required for release verification") from error
    commit = completed.stdout.strip() if completed.returncode == 0 else ""
    if not COMMIT_PATTERN.fullmatch(commit):
        raise VerificationError("HEAD is not available as a full lowercase 40-character Git SHA")
    return commit


def _release_report(
    path: Path,
    *,
    passed: bool,
    local_scopes: dict[str, object] | None,
    runtime_passed: bool,
    runtime_coverage: dict[str, object] | None,
    commit: str | None,
    run_id: int,
    reason: str | None,
) -> bool:
    runtime: dict[str, object] = {"status": "pass" if runtime_passed else "fail"}
    if runtime_coverage is not None:
        runtime["coverage"] = runtime_coverage
    if reason is not None:
        runtime["reason"] = reason
    document = {
        "schema_version": 1,
        "mode": "release",
        "status": "pass" if passed else "fail",
        "commit": commit,
        "runtime_workflow_run_id": run_id,
        "scopes": {
            "python": (
                local_scopes["python"]
                if local_scopes is not None
                else {"status": "fail", "coverage": None}
            ),
            "headless_ruby": (
                local_scopes["headless_ruby"]
                if local_scopes is not None
                else {"status": "fail", "coverage": None}
            ),
            "sketchup_runtime": runtime,
        },
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as error:
        print(f"Verification report: FAIL (cannot write {path}: {error})")
        return False
    print(f"Verification report: {path}")
    return True


def _local_release_scopes(path: Path) -> dict[str, object] | None:
    document = _read_report(path, "Local verification report")
    if document is None:
        return None
    if (
        document.get("schema_version") != 1
        or document.get("mode") != "local"
        or document.get("status") != "pass"
    ):
        print("Local verification report: FAIL (report contract differs)")
        return None
    scopes = document.get("scopes")
    if not isinstance(scopes, dict):
        print("Local verification report: FAIL (runtime scopes are missing)")
        return None
    selected: dict[str, object] = {}
    for name, label in (("python", "Python"), ("headless_ruby", "Headless Ruby")):
        scope = scopes.get(name)
        if not isinstance(scope, dict) or scope.get("status") != "pass":
            print(f"Local verification report: FAIL ({label} scope did not pass)")
            return None
        coverage = scope.get("coverage")
        if not isinstance(coverage, dict) or coverage.get("thresholds") != {
            "lines": 100,
            "branches": 100,
        }:
            print(f"Local verification report: FAIL ({label} thresholds differ)")
            return None
        for metric in ("lines", "branches"):
            counts = coverage.get(metric)
            if (
                not isinstance(counts, dict)
                or not isinstance(counts.get("covered"), int)
                or isinstance(counts.get("covered"), bool)
                or not isinstance(counts.get("total"), int)
                or isinstance(counts.get("total"), bool)
                or counts.get("total", 0) <= 0
                or counts.get("covered") != counts.get("total")
            ):
                print(
                    f"Local verification report: FAIL ({label} {metric} counts differ)"
                )
                return None
        selected[name] = {
            "status": "pass",
            "coverage": {
                "thresholds": {"lines": 100, "branches": 100},
                "lines": dict(coverage["lines"]),
                "branches": dict(coverage["branches"]),
            },
        }
    return selected


def _runtime_release_coverage(evidence: dict[str, object]) -> dict[str, object]:
    coverage = evidence.get("coverage")
    if not isinstance(coverage, dict):
        raise VerificationError("runtime evidence coverage is missing")
    result: dict[str, object] = {
        "thresholds": {"lines": 100, "branches": 100}
    }
    for metric in ("lines", "branches"):
        counts = coverage.get(metric)
        if not isinstance(counts, dict):
            raise VerificationError(f"runtime {metric} coverage is missing")
        covered = counts.get("covered")
        total = counts.get("total")
        if (
            not isinstance(covered, int)
            or isinstance(covered, bool)
            or not isinstance(total, int)
            or isinstance(total, bool)
            or total <= 0
            or covered != total
        ):
            raise VerificationError(f"runtime {metric} coverage is not exactly 100%")
        result[metric] = {"covered": covered, "total": total}
    return result


def verify_release(runtime_root: Path, run_id: int, report_path: Path) -> int:
    local_report = REPO_ROOT / "artifacts" / "verification" / "local.json"
    local_gate_passed = verify_local(local_report) == 0
    local_scopes = _local_release_scopes(local_report) if local_gate_passed else None
    local_passed = local_scopes is not None
    runtime_passed = False
    runtime_coverage: dict[str, object] | None = None
    commit: str | None = None
    reason: str | None = None
    phase = "SketchUp runtime evidence"
    environment = _environment()

    if local_passed:
        try:
            commit = _head_commit(environment)
            phase = "Trusted GitHub runtime run"
            metadata = _read_report(runtime_root / "github-run.json", "Trusted GitHub runtime run")
            if metadata is None:
                raise VerificationError("trusted GitHub runtime manifest is unavailable")
            trusted_run = validate_trusted_run(
                metadata, run_id=run_id, commit=commit, now=_utc_now()
            )
            print("Trusted GitHub runtime run: PASS")

            phase = "SketchUp runtime evidence"
            version = _required_string(
                (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip(),
                "project version",
            )
            bundle = load_runtime_bundle(
                repo_root=REPO_ROOT,
                runtime_root=runtime_root,
                commit=commit,
                dispatcher=trusted_run.dispatcher,
                version=version,
                now=_utc_now(),
            )
            evidence = bundle.evidence
            runtime_coverage = _runtime_release_coverage(evidence)
            raw_arguments = validator_arguments(bundle.raw_paths)

            rbz_name = f"sketchup-mcp-{version}.rbz"
            retained_rbz = bundle.retained_rbz
            with tempfile.TemporaryDirectory(
                prefix="sketchup-mcp-release-verification-"
            ) as directory:
                build_dir = Path(directory)
                if not _run(
                    "Deterministic RBZ rebuild",
                    [
                        sys.executable,
                        str(REPO_ROOT / "scripts/build.py"),
                        "--output-dir",
                        str(build_dir),
                    ],
                    environment,
                ):
                    raise VerificationError("deterministic RBZ rebuild failed")
                rebuilt_rbz = _regular_file(build_dir / rbz_name, "rebuilt RBZ")
                if retained_rbz.read_bytes() != rebuilt_rbz.read_bytes():
                    raise VerificationError(
                        "retained RBZ differs from deterministic rebuild"
                    )
                validate_command = [
                    sys.executable,
                    str(REPO_ROOT / "scripts/sketchup_runtime_evidence.py"),
                    "validate",
                    *raw_arguments,
                    "--evidence",
                    str(bundle.workspace / "evidence.json"),
                    "--rbz",
                    str(rebuilt_rbz),
                    "--commit",
                    commit,
                ]
                if not _run(
                    "SketchUp runtime evidence", validate_command, environment
                ):
                    raise VerificationError("public runtime evidence validator failed")
            runtime_passed = True
            print("SketchUp runtime: PASS")
        except (OSError, TrustedReleaseError, VerificationError) as error:
            reason = str(error)
            print(f"{phase}: FAIL ({reason})")
    else:
        reason = "local verification failed"
        print("SketchUp runtime evidence: FAIL (local verification failed first)")

    passed = local_passed and runtime_passed
    report_written = _release_report(
        report_path,
        passed=passed,
        local_scopes=local_scopes,
        runtime_passed=runtime_passed,
        runtime_coverage=runtime_coverage,
        commit=commit,
        run_id=run_id,
        reason=reason,
    )
    passed = passed and report_written
    print(f"Release verification: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def _positive_run_id(value: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise argparse.ArgumentTypeError("runtime run ID must contain decimal digits only")
    run_id = int(value)
    if run_id <= 0:
        raise argparse.ArgumentTypeError("runtime run ID must be positive")
    return run_id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    local = subparsers.add_parser("local", help="run hosted Python and Ruby gates")
    local.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "artifacts" / "verification" / "local.json",
        help="machine-readable aggregate report path",
    )
    release = subparsers.add_parser(
        "release", help="require fresh trusted SketchUp runtime evidence"
    )
    release.add_argument(
        "--runtime-root",
        type=Path,
        required=True,
        help="fixed directory containing github-run.json and one run-ID workspace",
    )
    release.add_argument("--runtime-run-id", type=_positive_run_id, required=True)
    release.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "artifacts" / "verification" / "release.json",
        help="machine-readable aggregate release report path",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.mode == "local":
        return verify_local(arguments.report)
    if arguments.mode == "release":
        return verify_release(
            arguments.runtime_root, arguments.runtime_run_id, arguments.report
        )
    raise AssertionError(f"unsupported verification mode: {arguments.mode}")


if __name__ == "__main__":
    raise SystemExit(main())
