"""Compare command consumers with the authoritative public catalog."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterable, Mapping

from .command_catalog import CommandCatalog, load_command_catalog
from .command_docs import stale_documents


@dataclass(frozen=True)
class ConsumerParity:
    """Name-level differences between one consumer and the public catalog."""

    consumer: str
    missing: tuple[str, ...]
    extra: tuple[str, ...]
    differently_named: Mapping[str, str]

    @property
    def in_sync(self) -> bool:
        return not (self.missing or self.extra or self.differently_named)

    def as_dict(self) -> dict[str, object]:
        return {
            "consumer": self.consumer,
            "in_sync": self.in_sync,
            "missing": list(self.missing),
            "extra": list(self.extra),
            "differently_named": dict(self.differently_named),
        }


def compare_commands(
    consumer: str,
    command_names: Iterable[str],
    catalog: CommandCatalog | None = None,
) -> ConsumerParity:
    """Report name differences without importing or starting either runtime."""

    accepted = catalog or load_command_catalog()
    observed = set(command_names)
    differently_named = {
        name: accepted.renamed_commands[name]
        for name in sorted(observed & set(accepted.renamed_commands))
    }
    executable_aliases = observed & set(accepted.executable_aliases)
    normalized = (observed - executable_aliases) | set(
        accepted.executable_aliases[name] for name in executable_aliases
    )
    accepted_names = set(accepted.names)

    return ConsumerParity(
        consumer=consumer,
        missing=tuple(name for name in accepted.names if name not in normalized),
        extra=tuple(sorted(normalized - accepted_names)),
        differently_named=differently_named,
    )


def _python_mcp_commands(root: Path) -> set[str]:
    script = """
import asyncio
import json
from sketchup_mcp.server import mcp
print(json.dumps([tool.name for tool in asyncio.run(mcp.list_tools())]))
"""
    environment = os.environ.copy()
    existing_path = environment.get("PYTHONPATH")
    source_path = str(root / "src")
    environment["PYTHONPATH"] = (
        source_path if not existing_path else os.pathsep.join((source_path, existing_path))
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(json.loads(completed.stdout))


def _ruby_execution_commands(root: Path) -> set[str]:
    script = """
require 'json'
require './su_mcp/su_mcp/command_catalog'
require './su_mcp/su_mcp/sketchup_adapter'
catalog = SU_MCP::CommandCatalog.new
adapter = SU_MCP::SketchupAdapter.new(commands: Object.new, model: Object.new)
puts JSON.generate(catalog.names.select { |name| adapter.respond_to?(name) })
"""
    completed = subprocess.run(
        ["ruby", "-e", script],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(json.loads(completed.stdout))


def _package_catalog_is_exact(root: Path) -> bool:
    script = """
require 'tmpdir'
require './su_mcp/package_contract'
Dir.mktmpdir do |staging_root|
  packaged = SU_MCP::PackageContract.stage_catalog(
    repo_root: Dir.pwd,
    staging_root: staging_root
  )
  source = File.join(Dir.pwd, 'src', 'sketchup_mcp', 'command_catalog.json')
  puts(File.binread(source) == File.binread(packaged) ? 'true' : 'false')
end
"""
    completed = subprocess.run(
        ["ruby", "-e", script],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() == "true"


def repository_command_names(
    repo_root: str | Path,
    catalog: CommandCatalog | None = None,
) -> dict[str, set[str]]:
    """Observe live registrations, execution reachability, docs, and packaging."""

    root = Path(repo_root).resolve()
    accepted = catalog or load_command_catalog()
    accepted_names = set(accepted.names)
    try:
        stale = set(stale_documents(root, accepted))
    except (OSError, ValueError):
        stale = {"README.md", "docs/command-catalog.md"}
    return {
        "fastmcp_registration": _python_mcp_commands(root),
        "ruby_execution": _ruby_execution_commands(root),
        "readme": set() if "README.md" in stale else accepted_names,
        "command_docs": (
            set() if "docs/command-catalog.md" in stale else accepted_names
        ),
        "package_catalog": (
            accepted_names if _package_catalog_is_exact(root) else set()
        ),
    }


def inspect_repository(
    repo_root: str | Path,
    catalog: CommandCatalog | None = None,
) -> tuple[ConsumerParity, ...]:
    """Compare all current command consumers with the accepted inventory."""

    accepted = catalog or load_command_catalog()
    return tuple(
        compare_commands(consumer, names, accepted)
        for consumer, names in repository_command_names(repo_root, accepted).items()
    )


def _render_text(reports: tuple[ConsumerParity, ...]) -> str:
    lines = []
    for report in reports:
        lines.append(f"{report.consumer}: {'in sync' if report.in_sync else 'out of sync'}")
        lines.append(
            "  missing: " + (", ".join(report.missing) if report.missing else "none")
        )
        lines.append("  extra: " + (", ".join(report.extra) if report.extra else "none"))
        renamed = ", ".join(
            f"{old} -> {new}" for old, new in report.differently_named.items()
        )
        lines.append("  differently named: " + (renamed or "none"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare public command consumers with the authoritative catalog."
    )
    parser.add_argument("repo_root", nargs="?", default=".")
    parser.add_argument("--json", action="store_true", dest="as_json")
    arguments = parser.parse_args(argv)

    reports = inspect_repository(arguments.repo_root)
    in_sync = all(report.in_sync for report in reports)
    if arguments.as_json:
        print(
            json.dumps(
                {
                    "in_sync": in_sync,
                    "consumers": [report.as_dict() for report in reports],
                },
                indent=2,
            )
        )
    else:
        print(_render_text(reports))
    return 0 if in_sync else 1


if __name__ == "__main__":
    sys.exit(main())
