"""Compare command consumers with the authoritative public catalog."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Iterable, Mapping

from .command_catalog import CommandCatalog, load_command_catalog


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
    normalized = (observed - set(differently_named)) | set(
        differently_named.values()
    )
    accepted_names = set(accepted.names)

    return ConsumerParity(
        consumer=consumer,
        missing=tuple(name for name in accepted.names if name not in normalized),
        extra=tuple(sorted(normalized - accepted_names)),
        differently_named=differently_named,
    )


def _python_mcp_commands(source: str) -> set[str]:
    module = ast.parse(source)
    commands: set[str] = set()
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            called = decorator.func if isinstance(decorator, ast.Call) else decorator
            if (
                isinstance(called, ast.Attribute)
                and isinstance(called.value, ast.Name)
                and called.value.id == "mcp"
                and called.attr == "tool"
            ):
                commands.add(node.name)
    return commands


def _ruby_extension_commands(source: str) -> set[str]:
    dispatch_start = source.index("def handle_tool_call")
    dispatch_end = source.index("def create_component", dispatch_start)
    dispatch = source[dispatch_start:dispatch_end]
    commands: set[str] = set()
    for names in re.findall(r"^\s*when\s+(.+)$", dispatch, flags=re.MULTILINE):
        commands.update(re.findall(r'"([a-z][a-z0-9_]*)"', names))
    return commands


def _manifest_commands(source: str) -> set[str]:
    manifest = json.loads(source)
    return {tool["name"] for tool in manifest["tools"]}


def _readme_commands(source: str) -> set[str]:
    tools_heading = re.search(r"^#### Tools\s*$", source, flags=re.MULTILINE)
    if tools_heading is None:
        return set()
    next_heading = re.search(
        r"^#{1,4}\s+", source[tools_heading.end() :], flags=re.MULTILINE
    )
    section_end = (
        tools_heading.end() + next_heading.start()
        if next_heading is not None
        else len(source)
    )
    section = source[tools_heading.end() : section_end]
    return set(re.findall(r"^\s*[*-]\s+`([^`]+)`", section, flags=re.MULTILINE))


def repository_command_names(repo_root: str | Path) -> dict[str, set[str]]:
    """Read command names from each current consumer without executing it."""

    root = Path(repo_root)
    return {
        "python_mcp_server": _python_mcp_commands(
            (root / "src/sketchup_mcp/server.py").read_text(encoding="utf-8")
        ),
        "ruby_extension": _ruby_extension_commands(
            (root / "su_mcp/su_mcp/main.rb").read_text(encoding="utf-8")
        ),
        "manifest": _manifest_commands(
            (root / "sketchup.json").read_text(encoding="utf-8")
        ),
        "readme": _readme_commands(
            (root / "README.md").read_text(encoding="utf-8")
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
        for consumer, names in repository_command_names(repo_root).items()
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
