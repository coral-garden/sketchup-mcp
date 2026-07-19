import copy
import json
from pathlib import Path
import unittest
from unittest import mock

from sketchup_mcp.command_catalog import (
    ArgumentContract,
    CommandCatalog,
    CommandContract,
    InvalidCommandArguments,
    manifest_tools,
    validate_command_arguments,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def one_argument_catalog(argument):
    return CommandCatalog(
        schema_version=1,
        commands=(
            CommandContract(
                name="example",
                description="Validate one example argument.",
                failure_action="validating an example",
                required_arguments=(argument,),
                optional_arguments=(),
                success={},
                failures=(),
            ),
        ),
        renamed_commands={},
        executable_aliases={},
        success_envelope={},
        failure_semantics={},
    )


def validate_one(argument, value):
    validate_command_arguments(
        "example", {argument.name: value}, one_argument_catalog(argument)
    )


class CommandCatalogCoverageTest(unittest.TestCase):
    def setUp(self):
        self.catalog_path = REPO_ROOT / "src/sketchup_mcp/command_catalog.json"
        self.raw_catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))

    def load_raw_catalog(self, raw):
        from sketchup_mcp import command_catalog as module

        resource = mock.Mock()
        resource.joinpath.return_value = resource
        resource.read_text.return_value = json.dumps(raw)
        module.load_command_catalog.cache_clear()
        with mock.patch.object(module, "files", return_value=resource):
            try:
                return module.load_command_catalog()
            finally:
                module.load_command_catalog.cache_clear()

    def assert_catalog_error(self, mutation, message):
        raw = copy.deepcopy(self.raw_catalog)
        mutation(raw)
        with self.assertRaisesRegex(ValueError, message):
            self.load_raw_catalog(raw)

    def test_catalog_loader_rejects_each_authored_inventory_conflict(self):
        self.assert_catalog_error(
            lambda raw: raw["commands"].append(copy.deepcopy(raw["commands"][0])),
            "duplicate command names",
        )

        def overlap(raw):
            command = raw["commands"][0]
            command["arguments"]["required"]["type"] = command["arguments"][
                "optional"
            ]["type"]

        self.assert_catalog_error(overlap, "both required and optional")
        self.assert_catalog_error(
            lambda raw: raw["commands"][0]["failures"].append("unknown_failure"),
            "unknown failure semantics",
        )
        self.assert_catalog_error(
            lambda raw: raw["renamed_commands"].update(
                {"create_component": "get_selection", "old": "missing"}
            ),
            "Invalid command renames",
        )
        self.assert_catalog_error(
            lambda raw: raw["executable_aliases"].update(
                {"get_selected_components": "create_component"}
            ),
            "Invalid executable aliases",
        )

    def test_catalog_loader_freezes_nested_authored_values(self):
        catalog = self.load_raw_catalog(copy.deepcopy(self.raw_catalog))

        self.assertEqual(11, len(catalog.commands))
        self.assertEqual("create_component", catalog.command("create_component").name)
        with self.assertRaises(KeyError):
            catalog.command("missing")
        with self.assertRaises(TypeError):
            catalog.renamed_commands["new"] = "value"

    def test_argument_name_errors_are_precise_for_singular_and_plural_cases(self):
        argument_a = ArgumentContract("first", "string", "First.")
        argument_b = ArgumentContract("second", "string", "Second.")
        catalog = CommandCatalog(
            schema_version=1,
            commands=(
                CommandContract(
                    "example",
                    "Example.",
                    "validating",
                    (argument_a, argument_b),
                    (),
                    {},
                    (),
                ),
            ),
            renamed_commands={},
            executable_aliases={},
            success_envelope={},
            failure_semantics={},
        )
        with self.assertRaisesRegex(InvalidCommandArguments, "unknown command"):
            validate_command_arguments("missing", {}, catalog)
        with self.assertRaisesRegex(InvalidCommandArguments, "unknown argument: third"):
            validate_command_arguments(
                "example", {"first": "a", "second": "b", "third": "c"}, catalog
            )
        with self.assertRaisesRegex(InvalidCommandArguments, "unknown arguments: fourth, third"):
            validate_command_arguments(
                "example",
                {"first": "a", "second": "b", "third": "c", "fourth": "d"},
                catalog,
            )
        with self.assertRaisesRegex(InvalidCommandArguments, "missing required argument: second"):
            validate_command_arguments("example", {"first": "a"}, catalog)
        with self.assertRaisesRegex(
            InvalidCommandArguments, "missing required arguments: first, second"
        ):
            validate_command_arguments("example", {}, catalog)

    def test_distinct_entity_constraints_compare_integer_and_decimal_string_ids(self):
        left = ArgumentContract("left", "entity_id", "Left.")
        right = ArgumentContract("right", "entity_id", "Right.")
        optional = ArgumentContract("optional", "entity_id", "Optional.")
        catalog = CommandCatalog(
            schema_version=1,
            commands=(
                CommandContract(
                    "example",
                    "Example.",
                    "validating",
                    (left, right),
                    (optional,),
                    {},
                    (),
                    constraints={
                        "distinct_arguments": (("left", "right"), ("left", "optional"))
                    },
                ),
            ),
            renamed_commands={},
            executable_aliases={},
            success_envelope={},
            failure_semantics={},
        )
        validate_command_arguments("example", {"left": "1", "right": 2}, catalog)
        with self.assertRaisesRegex(InvalidCommandArguments, "different entities"):
            validate_command_arguments("example", {"left": "1", "right": 1}, catalog)

    def test_every_argument_type_accepts_valid_values_and_rejects_invalid_values(self):
        cases = (
            (ArgumentContract("value", "entity_id", "Value."), (1, "2"), (True, 0, "0")),
            (
                ArgumentContract("value", "number[3]", "Value."),
                ([0, 1.5, 2],),
                ([1, 2], [0, float("inf"), 2], "bad"),
            ),
            (
                ArgumentContract(
                    "value", "number[3]", "Value.", constraints={"positive": True}
                ),
                ([1, 2, 3],),
                ([0, 2, 3],),
            ),
            (ArgumentContract("value", "integer[]", "Value."), ([1, 2], []), ([1, True], "bad")),
            (ArgumentContract("value", "string", "Value."), ("yes",), (1,)),
            (ArgumentContract("value", "number", "Value."), (1, 1.5), (True, float("inf"), "bad")),
            (ArgumentContract("value", "integer", "Value."), (1,), (True, 1.5)),
            (ArgumentContract("value", "boolean", "Value."), (True, False), (1,)),
            (ArgumentContract("value", "unknown", "Value."), (), ("anything",)),
        )
        for argument, valid_values, invalid_values in cases:
            for value in valid_values:
                with self.subTest(type=argument.type, value=value, valid=True):
                    validate_one(argument, value)
            for value in invalid_values:
                with self.subTest(type=argument.type, value=value, valid=False):
                    with self.assertRaises(InvalidCommandArguments):
                        validate_one(argument, value)

    def test_every_scalar_constraint_accepts_and_rejects_observable_inputs(self):
        cases = (
            (
                ArgumentContract(
                    "value", "number", "Value.", constraints={"positive": True}
                ),
                0.5,
                0,
            ),
            (
                ArgumentContract(
                    "value",
                    "string",
                    "Value.",
                    constraints={"enum": ("a", "b")},
                ),
                "a",
                "c",
            ),
            (
                ArgumentContract(
                    "value", "string", "Value.", constraints={"min_length": 2}
                ),
                "ab",
                "a",
            ),
            (
                ArgumentContract(
                    "value",
                    "string",
                    "Value.",
                    constraints={
                        "pattern_if_prefixed": {
                            "prefix": "#",
                            "pattern": "#[0-9A-F]{6}",
                            "message": "must be a color",
                        }
                    },
                ),
                "named",
                "#bad",
            ),
            (
                ArgumentContract(
                    "value",
                    "string",
                    "Value.",
                    constraints={"forbidden_substrings": ("start_operation",)},
                ),
                "1 + 1",
                "model.start_operation",
            ),
            (
                ArgumentContract(
                    "value",
                    "number",
                    "Value.",
                    constraints={"exclusive_minimum": 0},
                ),
                1,
                0,
            ),
            (
                ArgumentContract(
                    "value",
                    "number",
                    "Value.",
                    constraints={"exclusive_maximum": 2},
                ),
                1,
                2,
            ),
        )
        for argument, accepted, rejected in cases:
            with self.subTest(constraints=argument.constraints, accepted=True):
                validate_one(argument, accepted)
            with self.subTest(constraints=argument.constraints, accepted=False):
                with self.assertRaises(InvalidCommandArguments):
                    validate_one(argument, rejected)

        pattern = cases[3][0]
        validate_one(pattern, "#A0B1C2")

    def test_manifest_derives_every_schema_shape_and_json_safe_default(self):
        required = (
            ArgumentContract("entity", "entity_id", "Entity."),
            ArgumentContract("vector", "number[3]", "Vector.", constraints={"positive": True}),
            ArgumentContract("integers", "integer[]", "Integers."),
            ArgumentContract("count", "integer", "Count.", constraints={"positive": True}),
        )
        optional = (
            ArgumentContract(
                "choice",
                "string",
                "Choice.",
                default="a",
                constraints={"enum": ("a", "b"), "min_length": 1},
            ),
            ArgumentContract(
                "ratio",
                "number",
                "Ratio.",
                default=1,
                constraints={"exclusive_minimum": 0, "exclusive_maximum": 2},
            ),
            ArgumentContract("enabled", "boolean", "Enabled.", default=True),
        )
        command = CommandContract(
            "example", "Example.", "validating", required, optional, {}, ()
        )
        catalog = CommandCatalog(1, (command,), {}, {}, {}, {})

        schema = manifest_tools(catalog)[0]["parameters"]

        self.assertEqual([item.name for item in required], schema["required"])
        self.assertEqual(1, schema["properties"]["entity"]["anyOf"][0]["minimum"])
        self.assertEqual(0, schema["properties"]["vector"]["items"]["exclusiveMinimum"])
        self.assertEqual("integer", schema["properties"]["integers"]["items"]["type"])
        self.assertEqual(0, schema["properties"]["count"]["exclusiveMinimum"])
        self.assertEqual(["a", "b"], schema["properties"]["choice"]["enum"])
        self.assertEqual(1, schema["properties"]["choice"]["minLength"])
        self.assertEqual(0, schema["properties"]["ratio"]["exclusiveMinimum"])
        self.assertEqual(2, schema["properties"]["ratio"]["exclusiveMaximum"])
        self.assertIs(True, schema["properties"]["enabled"]["default"])

        nested_default = ArgumentContract(
            "nested",
            "string",
            "Nested.",
            default={"items": ("a", {"more": ("b",)})},
        )
        nested_command = CommandContract(
            "nested", "Nested.", "validating", (), (nested_default,), {}, ()
        )
        nested_schema = manifest_tools(CommandCatalog(1, (nested_command,), {}, {}, {}, {}))
        self.assertEqual(
            {"items": ["a", {"more": ["b"]}]},
            nested_schema[0]["parameters"]["properties"]["nested"]["default"],
        )
