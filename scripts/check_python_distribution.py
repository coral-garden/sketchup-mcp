#!/usr/bin/env python3
"""Validate the versioned wheel and sdist, then install a wheel built from the sdist."""

from __future__ import annotations

import argparse
from email import policy
from email.parser import BytesParser
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from extension_package import PackageError, project_version


REPO_ROOT = Path(__file__).resolve().parents[1]


class DistributionError(ValueError):
    """A Python release artifact does not satisfy the distribution contract."""


def _version() -> str:
    try:
        return project_version(REPO_ROOT)
    except PackageError as error:
        raise DistributionError(str(error)) from error


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise DistributionError(f"{label} is missing or is not a regular file: {path}")
    return path


def _validate_metadata(contents: bytes, *, version: str, label: str) -> None:
    message = BytesParser(policy=policy.default).parsebytes(contents)
    if message.get("Name") != "sketchup-mcp":
        raise DistributionError(f"{label} project name differs")
    if message.get("Version") != version:
        raise DistributionError(f"{label} version differs")
    if message.get("Requires-Python") != ">=3.10":
        raise DistributionError(f"{label} Python requirement differs")


def _safe_archive_name(name: str, label: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DistributionError(f"unsafe path in {label}: {name!r}")
    return path


def _expected_package_modules() -> set[str]:
    package_root = REPO_ROOT / "src" / "sketchup_mcp"
    modules = {
        f"sketchup_mcp/{source.relative_to(package_root).as_posix()}"
        for source in package_root.rglob("*.py")
        if source.is_file() and not source.is_symlink()
    }
    if not modules:
        raise DistributionError("authoritative Python package source is unavailable")
    return modules


# Wheels and source distributions deliberately keep separate validators: their
# metadata roots and package layouts differ, so sharing layout logic would hide
# the format-specific release contract rather than simplify it.
def _check_wheel(path: Path, version: str, label: str) -> None:
    wheel = _regular_file(path, label)
    dist_info = f"sketchup_mcp-{version}.dist-info"
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise DistributionError(f"duplicate path in {label}")
            for name in names:
                _safe_archive_name(name, label)
            required = {
                "sketchup_mcp/__init__.py",
                "sketchup_mcp/command_catalog.json",
                f"{dist_info}/METADATA",
                f"{dist_info}/entry_points.txt",
                f"{dist_info}/WHEEL",
            }
            missing = sorted(required - set(names))
            if missing:
                raise DistributionError(f"{label} is missing members: {missing!r}")
            expected_modules = _expected_package_modules()
            packaged_modules = {
                name
                for name in names
                if name.startswith("sketchup_mcp/") and name.endswith(".py")
            }
            if packaged_modules != expected_modules:
                missing_modules = sorted(expected_modules - packaged_modules)
                extra_modules = sorted(packaged_modules - expected_modules)
                raise DistributionError(
                    f"{label} module inventory differs; missing={missing_modules!r}, "
                    f"extra={extra_modules!r}"
                )
            _validate_metadata(
                archive.read(f"{dist_info}/METADATA"),
                version=version,
                label=label,
            )
            entry_points = archive.read(
                f"{dist_info}/entry_points.txt"
            ).decode("utf-8")
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as error:
        raise DistributionError(f"{label} is unreadable: {wheel}") from error
    for expected in (
        "sketchup-mcp = sketchup_mcp.mcp_server:main",
        "sketchup = sketchup_mcp.mcp_server:mcp",
    ):
        if expected not in entry_points:
            raise DistributionError(f"{label} entry points differ")


def _check_sdist(path: Path, version: str) -> None:
    source = _regular_file(path, "source distribution")
    root = f"sketchup_mcp-{version}"
    try:
        with tarfile.open(source, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise DistributionError("duplicate path in source distribution")
            for member in members:
                path_name = _safe_archive_name(member.name, "source distribution")
                if path_name.parts[0] != root:
                    raise DistributionError("source distribution root differs")
                if member.issym() or member.islnk():
                    raise DistributionError(
                        f"link in source distribution: {member.name!r}"
                    )
            required = {
                f"{root}/PKG-INFO",
                f"{root}/VERSION",
                f"{root}/pyproject.toml",
                f"{root}/src/sketchup_mcp/command_catalog.json",
                *{
                    f"{root}/src/{module}"
                    for module in _expected_package_modules()
                },
            }
            missing = sorted(required - set(names))
            if missing:
                raise DistributionError(
                    f"source distribution is missing members: {missing!r}"
                )
            metadata_member = archive.extractfile(f"{root}/PKG-INFO")
            version_member = archive.extractfile(f"{root}/VERSION")
            if metadata_member is None or version_member is None:
                raise DistributionError("source distribution metadata is unavailable")
            metadata = metadata_member.read()
            packaged_version = version_member.read().decode("utf-8").strip()
    except (OSError, tarfile.TarError, UnicodeDecodeError) as error:
        raise DistributionError(f"source distribution is unreadable: {source}") from error
    _validate_metadata(metadata, version=version, label="source distribution")
    if packaged_version != version:
        raise DistributionError("source distribution VERSION differs")


def _run(command: list[str], *, cwd: Path) -> None:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise DistributionError(
            f"required executable is missing: {error.filename or command[0]}"
        ) from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise DistributionError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
            + (f": {detail}" if detail else "")
        )


def _installed_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _check_wheel_from_sdist(sdist: Path, version: str, python: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="sketchup-mcp-python-dist-") as directory:
        workspace = Path(directory)
        rebuilt = workspace / "rebuilt"
        _run(
            [
                "uv",
                "build",
                "--offline",
                "--no-build-isolation",
                "--wheel",
                "--out-dir",
                str(rebuilt),
                str(sdist),
            ],
            cwd=REPO_ROOT,
        )
        wheel = rebuilt / f"sketchup_mcp-{version}-py3-none-any.whl"
        _check_wheel(wheel, version, "wheel built from source distribution")

        environment = workspace / "venv"
        _run(
            ["uv", "venv", "--python", str(python), str(environment)],
            cwd=workspace,
        )
        installed_python = _installed_python(environment)
        requirements = workspace / "runtime-requirements.txt"
        _run(
            [
                "uv",
                "export",
                "--offline",
                "--locked",
                "--no-dev",
                "--no-default-groups",
                "--no-emit-project",
                "--output-file",
                str(requirements),
            ],
            cwd=REPO_ROOT,
        )
        _run(
            [
                "uv",
                "pip",
                "install",
                "--offline",
                "--require-hashes",
                "--python",
                str(installed_python),
                "--requirement",
                str(requirements),
            ],
            cwd=workspace,
        )
        _run(
            [
                "uv",
                "pip",
                "install",
                "--offline",
                "--python",
                str(installed_python),
                "--no-deps",
                str(wheel),
            ],
            cwd=workspace,
        )
        completed = subprocess.run(
            [
                str(installed_python),
                "-I",
                "-c",
                (
                    "import importlib.metadata as metadata; "
                    "from mcp.server.fastmcp import FastMCP; "
                    "import sketchup_mcp; "
                    "distribution = metadata.distribution('sketchup-mcp'); "
                    "entry_points = {(entry.group, entry.name): entry "
                    "for entry in distribution.entry_points}; "
                    "console = entry_points[('console_scripts', 'sketchup-mcp')]; "
                    "server = entry_points[('mcp', 'sketchup')]; "
                    "assert console.value == 'sketchup_mcp.mcp_server:main'; "
                    "assert server.value == 'sketchup_mcp.mcp_server:mcp'; "
                    "assert callable(console.load()); "
                    "assert isinstance(server.load(), FastMCP); "
                    "print(distribution.version); "
                    "print(sketchup_mcp.__version__); "
                    "print('entry-points-loaded')"
                ),
            ],
            cwd=workspace,
            env={
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTHONHOME", "PYTHONPATH"}
            },
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise DistributionError(
                "installed wheel import failed: "
                + (completed.stderr or completed.stdout).strip()
            )
        if completed.stdout.splitlines() != [version, version, "entry-points-loaded"]:
            raise DistributionError("installed wheel version or entry points differ")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Python interpreter used for the isolated installation check",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    version = _version()
    wheel_name = f"sketchup_mcp-{version}-py3-none-any.whl"
    sdist_name = f"sketchup_mcp-{version}.tar.gz"
    try:
        wheel = arguments.dist_dir / wheel_name
        sdist = arguments.dist_dir / sdist_name
        _check_wheel(wheel, version, "wheel")
        _check_sdist(sdist, version)
        _check_wheel_from_sdist(sdist, version, arguments.python)
    except DistributionError as error:
        print(f"Python distribution: FAIL ({error})", file=sys.stderr)
        return 1
    print("Python distribution: PASS")
    print(f"Version: {version}")
    print(f"Wheel: {wheel_name}")
    print(f"Source distribution: {sdist_name}")
    print("Wheel-from-sdist install: PASS")
    print("Entry points: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
