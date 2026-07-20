#!/usr/bin/env python3
"""Run the repository's headless verification gates through one entry point."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]


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
            "sketchup_runtime": {"status": "manual", "required": False},
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
        python_coverage = _python_metrics(python_report) if python_reported else None
        python_integration = _run(
            "Python integration",
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            environment,
        )

        ruby_gate = _run(
            "Headless Ruby coverage",
            ["ruby", "scripts/ruby_coverage.rb", "--json", str(ruby_report)],
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
    print("SketchUp runtime: MANUAL (run the desktop acceptance checklist)")
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.mode == "local":
        return verify_local(arguments.report)
    raise AssertionError(f"unsupported verification mode: {arguments.mode}")


if __name__ == "__main__":
    raise SystemExit(main())
