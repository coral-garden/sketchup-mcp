"""Public SketchUp command contract, independent of both runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class ArgumentContract:
    """One required or optional public command argument."""

    name: str
    type: str
    description: str
    default: Any = None
    constraints: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class CommandContract:
    """Arguments and observable result semantics for one public command."""

    name: str
    description: str
    required_arguments: tuple[ArgumentContract, ...]
    optional_arguments: tuple[ArgumentContract, ...]
    success: Mapping[str, Any]
    failures: tuple[str, ...]
    constraints: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class CommandCatalog:
    """The accepted command inventory shared by every runtime and document."""

    schema_version: int
    commands: tuple[CommandContract, ...]
    renamed_commands: Mapping[str, str]
    executable_aliases: Mapping[str, str]
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


class InvalidCommandArguments(ValueError):
    """Raw MCP arguments do not satisfy an authored command contract."""


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
        constraints=_freeze(
            {
                key: item
                for key, item in value.items()
                if key not in {"type", "description", "default"}
            }
        ),
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
    invalid_aliases = {
        old: new
        for old, new in raw["executable_aliases"].items()
        if raw["renamed_commands"].get(old) != new
    }
    if invalid_aliases:
        raise ValueError(f"Invalid executable aliases: {invalid_aliases}")


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
            constraints=_freeze(command.get("constraints", {})),
        )
        for command in raw["commands"]
    )
    return CommandCatalog(
        schema_version=raw["schema_version"],
        commands=commands,
        renamed_commands=_freeze(raw["renamed_commands"]),
        executable_aliases=_freeze(raw["executable_aliases"]),
        success_envelope=_freeze(raw["success_envelope"]),
        failure_semantics=_freeze(raw["failure_semantics"]),
    )


def manifest_tools(catalog: CommandCatalog | None = None) -> list[dict[str, Any]]:
    """Derive manifest tool schemas from the authoritative command catalog."""

    accepted = catalog or load_command_catalog()
    return [
        {
            "name": command.name,
            "description": command.description,
            "parameters": {
                "type": "object",
                "properties": {
                    argument.name: _json_schema(argument, optional=False)
                    for argument in command.required_arguments
                }
                | {
                    argument.name: _json_schema(argument, optional=True)
                    for argument in command.optional_arguments
                },
                "required": [
                    argument.name for argument in command.required_arguments
                ],
                "additionalProperties": False,
            },
        }
        for command in accepted.commands
    ]


def validate_command_arguments(
    command_name: str,
    arguments: Mapping[str, Any],
    catalog: CommandCatalog | None = None,
) -> None:
    """Validate raw MCP arguments before a command handler can coerce them."""

    accepted = catalog or load_command_catalog()
    try:
        command = accepted.command(command_name)
    except KeyError as error:
        raise InvalidCommandArguments(f"unknown command {command_name!r}") from error

    required = {argument.name: argument for argument in command.required_arguments}
    optional = {argument.name: argument for argument in command.optional_arguments}
    known_names = required.keys() | optional.keys()
    unknown_names = sorted(arguments.keys() - known_names)
    if unknown_names:
        raise InvalidCommandArguments(
            "unknown argument" + ("s" if len(unknown_names) != 1 else "") + ": "
            + ", ".join(unknown_names)
        )

    missing_names = sorted(required.keys() - arguments.keys())
    if missing_names:
        raise InvalidCommandArguments(
            "missing required argument"
            + ("s" if len(missing_names) != 1 else "")
            + ": "
            + ", ".join(missing_names)
        )

    contracts = required | optional
    for name, value in arguments.items():
        _validate_argument(name, value, contracts[name])

    for distinct_names in (command.constraints or {}).get(
        "distinct_arguments", ()
    ):
        values = [arguments.get(name) for name in distinct_names]
        if all(value is not None for value in values):
            normalized = [
                int(value) if isinstance(value, str) and value.isdecimal() else value
                for value in values
            ]
            if len(set(normalized)) != len(normalized):
                raise InvalidCommandArguments(
                    "arguments "
                    + ", ".join(distinct_names)
                    + " must identify different entities"
                )


def _validate_argument(name: str, value: Any, contract: ArgumentContract) -> None:
    constraints = contract.constraints or {}
    valid = True
    if contract.type == "entity_id":
        valid = (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 1
        ) or (
            isinstance(value, str)
            and re.fullmatch(r"[1-9][0-9]*", value) is not None
        )
    elif contract.type == "number[3]":
        valid = (
            isinstance(value, list)
            and len(value) == 3
            and all(_is_finite_number(item) for item in value)
            and (
                not constraints.get("positive")
                or all(item > 0 for item in value)
            )
        )
    elif contract.type == "integer[]":
        valid = isinstance(value, list) and all(
            isinstance(item, int) and not isinstance(item, bool) for item in value
        )
    elif contract.type == "string":
        valid = isinstance(value, str)
    elif contract.type == "number":
        valid = _is_finite_number(value)
    elif contract.type == "integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif contract.type == "boolean":
        valid = isinstance(value, bool)
    else:
        valid = False

    if not valid:
        raise InvalidCommandArguments(
            f"argument {name!r} must be {contract.type}"
        )
    if (
        constraints.get("positive")
        and contract.type in {"number", "integer"}
        and value <= 0
    ):
        raise InvalidCommandArguments(f"argument {name!r} must be positive")
    if "enum" in constraints and value not in constraints["enum"]:
        raise InvalidCommandArguments(
            f"argument {name!r} must be one of: "
            + ", ".join(str(item) for item in constraints["enum"])
        )
    if "min_length" in constraints and len(value) < constraints["min_length"]:
        raise InvalidCommandArguments(
            f"argument {name!r} must contain at least "
            f"{constraints['min_length']} character(s)"
        )
    if any(
        forbidden in value
        for forbidden in constraints.get("forbidden_substrings", ())
    ):
        raise InvalidCommandArguments(
            f"argument {name!r} contains a forbidden operation-management call"
        )
    if (
        "exclusive_minimum" in constraints
        and value <= constraints["exclusive_minimum"]
    ):
        raise InvalidCommandArguments(
            f"argument {name!r} must be greater than {constraints['exclusive_minimum']}"
        )
    if (
        "exclusive_maximum" in constraints
        and value >= constraints["exclusive_maximum"]
    ):
        raise InvalidCommandArguments(
            f"argument {name!r} must be less than {constraints['exclusive_maximum']}"
        )


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _json_schema(argument: ArgumentContract, optional: bool) -> dict[str, Any]:
    constraints = argument.constraints or {}
    if argument.type == "entity_id":
        schema: dict[str, Any] = {
            "anyOf": [
                {"type": "integer", "minimum": 1},
                {"type": "string", "pattern": "^[1-9][0-9]*$"},
            ]
        }
    elif argument.type == "number[3]":
        items: dict[str, Any] = {"type": "number"}
        if constraints.get("positive"):
            items["exclusiveMinimum"] = 0
        schema = {
            "type": "array",
            "items": items,
            "minItems": 3,
            "maxItems": 3,
        }
    elif argument.type == "integer[]":
        schema = {"type": "array", "items": {"type": "integer"}}
    else:
        schema = {
            "type": {
                "string": "string",
                "number": "number",
                "integer": "integer",
                "boolean": "boolean",
            }[argument.type]
        }
    schema["description"] = argument.description
    if constraints.get("positive") and argument.type in {"number", "integer"}:
        schema["exclusiveMinimum"] = 0
    if "enum" in constraints:
        schema["enum"] = list(constraints["enum"])
    if "min_length" in constraints:
        schema["minLength"] = constraints["min_length"]
    if "exclusive_minimum" in constraints:
        schema["exclusiveMinimum"] = constraints["exclusive_minimum"]
    if "exclusive_maximum" in constraints:
        schema["exclusiveMaximum"] = constraints["exclusive_maximum"]
    if optional and argument.default is not None:
        schema["default"] = _json_value(argument.default)
    return schema


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    return value
