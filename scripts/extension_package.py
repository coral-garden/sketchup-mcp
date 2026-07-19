"""Build and validate the installable SketchUp extension package."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import zipfile


ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ARCHIVE_MODE = stat.S_IFREG | 0o644
ROOT_LOADER = "su_mcp.rb"
SUPPORT_DIRECTORY = "su_mcp"
GENERATED_MEMBERS = frozenset(
    {f"{SUPPORT_DIRECTORY}/VERSION", f"{SUPPORT_DIRECTORY}/command_catalog.json"}
)
VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)
REQUIRE_RELATIVE_PATTERN = re.compile(
    r"^[ \t]*require_relative[ \t]*(?:\([ \t]*)?"
    r"(?P<quote>['\"])(?P<target>[^'\"\r\n]+)(?P=quote)[ \t]*\)?",
    re.MULTILINE,
)
SKETCHUP_EXTENSION_PATTERN = re.compile(
    r"SketchupExtension\.new\(\s*(?:'[^']*'|\"[^\"]*\")\s*,\s*"
    r"(?P<quote>['\"])(?P<target>[^'\"]+)(?P=quote)"
)


class PackageError(ValueError):
    """The source tree or RBZ does not satisfy the extension package contract."""


@dataclass(frozen=True)
class PackageReport:
    """Observable identity of one validated extension package."""

    path: Path
    version: str
    sha256: str
    files: tuple[str, ...]


def project_version(repo_root: Path) -> str:
    """Return the authoritative project version."""

    version_file = repo_root / "VERSION"
    try:
        value = version_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise PackageError(f"project version is unavailable: {version_file}") from error
    if not VERSION_PATTERN.fullmatch(value):
        raise PackageError(f"invalid project version: {value!r}")
    return value


def artifact_name(repo_root: Path) -> str:
    """Return the RBZ filename derived from the project version."""

    return f"sketchup-mcp-{project_version(repo_root)}.rbz"


def _source_files(repo_root: Path) -> dict[str, bytes]:
    loader = repo_root / ROOT_LOADER
    support = repo_root / SUPPORT_DIRECTORY
    catalog = repo_root / "src" / "sketchup_mcp" / "command_catalog.json"
    version_file = repo_root / "VERSION"

    if loader.is_symlink():
        raise PackageError(f"symbolic link is not allowed in package source: {loader}")
    if not loader.is_file():
        raise PackageError(f"extension loader is unavailable: {loader}")
    if not support.is_dir() or support.is_symlink():
        raise PackageError(f"support directory is unavailable or unsafe: {support}")

    files = {ROOT_LOADER: loader.read_bytes()}
    for source in sorted(support.rglob("*")):
        if source.is_symlink():
            raise PackageError(f"symbolic link is not allowed in package source: {source}")
        if source.is_dir():
            continue
        if not source.is_file():
            raise PackageError(f"unsupported package source: {source}")
        relative = source.relative_to(repo_root).as_posix()
        if PurePosixPath(relative).name == ROOT_LOADER:
            raise PackageError(f"additional extension loader is not allowed: {relative}")
        if relative in GENERATED_MEMBERS:
            raise PackageError(f"generated package member must not exist in source: {source}")
        files[relative] = source.read_bytes()

    try:
        files[f"{SUPPORT_DIRECTORY}/VERSION"] = version_file.read_bytes()
        files[f"{SUPPORT_DIRECTORY}/command_catalog.json"] = catalog.read_bytes()
    except OSError as error:
        raise PackageError(f"generated package input is unavailable: {error.filename}") from error
    return files


def _archive_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=ARCHIVE_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = ARCHIVE_MODE << 16
    return info


def build_package(repo_root: Path, output_dir: Path) -> PackageReport:
    """Build, validate, and return one deterministic RBZ."""

    root = repo_root.resolve()
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    artifact = destination / artifact_name(root)
    files = _source_files(root)

    with zipfile.ZipFile(artifact, "w") as archive:
        for name, contents in sorted(files.items()):
            archive.writestr(
                _archive_info(name),
                contents,
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    return check_package(root, artifact)


def _validate_member(member: zipfile.ZipInfo) -> None:
    name = member.filename
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or path.is_absolute()
        or path.as_posix() != name
        or (path.parts and path.parts[0].endswith(":"))
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PackageError(f"unsafe path in extension package: {name!r}")
    mode = member.external_attr >> 16
    if stat.S_IFMT(mode) == stat.S_IFLNK:
        raise PackageError(f"symbolic link in extension package: {name!r}")
    if member.is_dir():
        raise PackageError(f"directory entries are not allowed: {name!r}")


def _validate_loader(files: dict[str, bytes]) -> None:
    try:
        loader = files[ROOT_LOADER].decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackageError("extension loader is not UTF-8") from error
    if "require_relative 'su_mcp/version'" not in loader:
        raise PackageError("extension loader does not load the packaged project version")
    if "SketchupExtension.new('SketchUp MCP', 'su_mcp/main')" not in loader:
        raise PackageError("extension loader does not target su_mcp/main")
    if "extension.version = SU_MCP::VERSION" not in loader:
        raise PackageError("extension loader does not expose the project version")


def _load_target(source: str, target: str, *, relative: bool) -> str:
    if "\\" in target:
        raise PackageError(f"unsafe Ruby load target in {source}: {target!r}")
    joined = (
        str(PurePosixPath(source).parent / target)
        if relative
        else target
    )
    normalized = posixpath.normpath(joined)
    path = PurePosixPath(normalized)
    if path.is_absolute() or normalized == ".." or normalized.startswith("../"):
        raise PackageError(f"unsafe Ruby load target in {source}: {target!r}")
    return normalized if path.suffix else f"{normalized}.rb"


def _ruby_sources(files: dict[str, bytes]) -> dict[str, str]:
    sources = {}
    for name, contents in files.items():
        if not name.endswith(".rb"):
            continue
        try:
            sources[name] = contents.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PackageError(f"Ruby source is not UTF-8: {name}") from error
    return sources


def _validate_load_paths(files: dict[str, bytes]) -> None:
    sources = _ruby_sources(files)
    extension_loaders = []
    for source, contents in sources.items():
        for match in REQUIRE_RELATIVE_PATTERN.finditer(contents):
            target = _load_target(source, match.group("target"), relative=True)
            if target not in files:
                raise PackageError(
                    f"missing require_relative target: {source} -> {target}"
                )
        for match in SKETCHUP_EXTENSION_PATTERN.finditer(contents):
            extension_loaders.append((source, match.group("target")))

    if len(extension_loaders) != 1 or extension_loaders[0][0] != ROOT_LOADER:
        raise PackageError(
            "extension package must contain exactly one root SketchupExtension loader"
        )
    source, declared_target = extension_loaders[0]
    target = _load_target(source, declared_target, relative=False)
    if target not in files:
        raise PackageError(f"missing SketchupExtension load target: {source} -> {target}")


def check_package(repo_root: Path, artifact: Path) -> PackageReport:
    """Validate one RBZ's paths, bytes, loader, metadata, and reproducibility fields."""

    root = repo_root.resolve()
    package = artifact.resolve()
    expected = _source_files(root)
    expected_names = tuple(sorted(expected))
    try:
        with zipfile.ZipFile(package) as archive:
            members = archive.infolist()
            for member in members:
                _validate_member(member)
            names = tuple(member.filename for member in members)
            if len(names) != len(set(names)):
                raise PackageError("duplicate path in extension package")
            if names != expected_names:
                raise PackageError(
                    f"extension package layout differs: expected {expected_names!r}, got {names!r}"
                )
            files = {name: archive.read(name) for name in names}
            for member in members:
                if member.date_time != ARCHIVE_TIMESTAMP:
                    raise PackageError(f"non-deterministic timestamp: {member.filename}")
                if member.external_attr >> 16 != ARCHIVE_MODE:
                    raise PackageError(f"non-deterministic permissions: {member.filename}")
                if member.compress_type != zipfile.ZIP_DEFLATED:
                    raise PackageError(f"unexpected compression: {member.filename}")
    except (OSError, zipfile.BadZipFile) as error:
        raise PackageError(f"extension package is unreadable: {package}") from error

    for name in expected_names:
        if files[name] != expected[name]:
            raise PackageError(f"extension package bytes differ: {name}")
    if package.name != artifact_name(root):
        raise PackageError(
            f"extension package filename differs: expected {artifact_name(root)!r}"
        )
    _validate_loader(files)
    _validate_load_paths(files)

    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    return PackageReport(package, project_version(root), digest, expected_names)
