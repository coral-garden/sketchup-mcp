"""Generate catalog-governed command blocks in repository documentation."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from .command_catalog import CommandCatalog, load_command_catalog


START_MARKER = "<!-- command-catalog:start -->"
END_MARKER = "<!-- command-catalog:end -->"


def _readme_commands(catalog: CommandCatalog) -> str:
    return "\n".join(
        f"* `{command.name}` - {command.description}" for command in catalog.commands
    )


def _catalog_document(catalog: CommandCatalog) -> str:
    canonical = "\n".join(f"- `{command.name}`" for command in catalog.commands)
    executable = "\n".join(
        f"- `{old}` executes `{new}`."
        for old, new in catalog.executable_aliases.items()
    )
    migration_only = "\n".join(
        f"- `{old}` must become `{new}` and is not executable."
        for old, new in catalog.renamed_commands.items()
        if old not in catalog.executable_aliases
    )
    return (
        "The accepted public command names are:\n\n"
        f"{canonical}\n\n"
        "Executable compatibility aliases:\n\n"
        f"{executable}\n\n"
        "Migration-only rename guidance:\n\n"
        f"{migration_only}"
    )


DOCUMENTS: dict[str, Callable[[CommandCatalog], str]] = {
    "README.md": _readme_commands,
    "docs/command-catalog.md": _catalog_document,
}


def _generated_source(source: str, generated: str) -> str:
    try:
        before, remainder = source.split(START_MARKER, 1)
        _current, after = remainder.split(END_MARKER, 1)
    except ValueError as error:
        raise ValueError("document is missing command-catalog markers") from error
    return (
        before
        + START_MARKER
        + "\n"
        + generated.rstrip()
        + "\n"
        + END_MARKER
        + after
    )


def stale_documents(
    repo_root: str | Path,
    catalog: CommandCatalog | None = None,
) -> tuple[str, ...]:
    root = Path(repo_root)
    accepted = catalog or load_command_catalog()
    stale = []
    for relative_path, render in DOCUMENTS.items():
        path = root / relative_path
        source = path.read_text(encoding="utf-8")
        if source != _generated_source(source, render(accepted)):
            stale.append(relative_path)
    return tuple(stale)


def check_documents(
    repo_root: str | Path,
    catalog: CommandCatalog | None = None,
) -> bool:
    return not stale_documents(repo_root, catalog)


def write_documents(
    repo_root: str | Path,
    catalog: CommandCatalog | None = None,
) -> None:
    root = Path(repo_root)
    accepted = catalog or load_command_catalog()
    for relative_path, render in DOCUMENTS.items():
        path = root / relative_path
        source = path.read_text(encoding="utf-8")
        path.write_text(
            _generated_source(source, render(accepted)),
            encoding="utf-8",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root", nargs="?", default=".")
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.write:
        write_documents(arguments.repo_root)
        return 0
    stale = stale_documents(arguments.repo_root)
    if stale:
        print("Stale generated command documents: " + ", ".join(stale))
        return 1
    print("Generated command documents are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
