"""Project version sourced from the release identity at the repository root."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _project_version() -> str:
    source_root = Path(__file__).resolve().parents[2]
    source_version = source_root / "VERSION"
    if (source_root / "pyproject.toml").is_file() and source_version.is_file():
        return source_version.read_text(encoding="utf-8").strip()
    try:
        return version("sketchup-mcp")
    except PackageNotFoundError as error:
        raise RuntimeError("SketchUp MCP project version is unavailable") from error


__version__ = _project_version()
