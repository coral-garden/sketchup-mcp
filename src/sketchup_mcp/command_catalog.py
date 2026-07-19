"""Public SketchUp command contract, independent of both runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import json
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class ArgumentContract:
    """One required or optional public command argument."""

    name: str
    type: str
    description: str
    default: Any = None


@dataclass(frozen=True)
class CommandContract:
    """Arguments and observable result semantics for one public command."""

    name: str
    description: str
    required_arguments: tuple[ArgumentContract, ...]
    optional_arguments: tuple[ArgumentContract, ...]
    success: Mapping[str, Any]
    failures: tuple[str, ...]


@dataclass(frozen=True)
class CommandCatalog:
    """The accepted command inventory shared by every runtime and document."""

    schema_version: int
    commands: tuple[CommandContract, ...]
    renamed_commands: Mapping[str, str]
    success_envelope: Mapping[str, Any]
    failure_semantics: Mapping[str, Mapping[str, Any]]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(command.name for command in self.commands)

    def command(self, name: str) -> CommandContract:
        for command in self.commands:
            if command.name == name:
                return command
        raise KeyError(name)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _argument(name: str, value: Mapping[str, Any]) -> ArgumentContract:
    return ArgumentContract(
        name=name,
        type=value["type"],
        description=value["description"],
        default=_freeze(value.get("default")),
    )


def _validate(raw: Mapping[str, Any]) -> None:
    names = [command["name"] for command in raw["commands"]]
    if len(names) != len(set(names)):
        raise ValueError("Command catalog contains duplicate command names")

    known_failures = set(raw["failure_semantics"])
    for command in raw["commands"]:
        required = set(command["arguments"]["required"])
        optional = set(command["arguments"]["optional"])
        if required & optional:
            raise ValueError(
                f"Command {command['name']} defines arguments as both required and optional"
            )
        unknown_failures = set(command["failures"]) - known_failures
        if unknown_failures:
            raise ValueError(
                f"Command {command['name']} uses unknown failure semantics: "
                f"{', '.join(sorted(unknown_failures))}"
            )

    canonical_names = set(names)
    invalid_renames = {
        old: new
        for old, new in raw["renamed_commands"].items()
        if old in canonical_names or new not in canonical_names
    }
    if invalid_renames:
        raise ValueError(f"Invalid command renames: {invalid_renames}")


@lru_cache(maxsize=1)
def load_command_catalog() -> CommandCatalog:
    """Load and validate the packaged catalog without importing runtime code."""

    catalog_path = files("sketchup_mcp").joinpath("command_catalog.json")
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    _validate(raw)

    commands = tuple(
        CommandContract(
            name=command["name"],
            description=command["description"],
            required_arguments=tuple(
                _argument(name, value)
                for name, value in command["arguments"]["required"].items()
            ),
            optional_arguments=tuple(
                _argument(name, value)
                for name, value in command["arguments"]["optional"].items()
            ),
            success=_freeze(command["success"]),
            failures=tuple(command["failures"]),
        )
        for command in raw["commands"]
    )
    return CommandCatalog(
        schema_version=raw["schema_version"],
        commands=commands,
        renamed_commands=_freeze(raw["renamed_commands"]),
        success_envelope=_freeze(raw["success_envelope"]),
        failure_semantics=_freeze(raw["failure_semantics"]),
    )
