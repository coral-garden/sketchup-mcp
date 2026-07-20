"""Clean-install acceptance evidence contract tests."""

import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.test_bridge_lifecycle import ScriptedBridge, send_result


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install_acceptance.py"


def load_module():
    spec = importlib.util.spec_from_file_location("install_acceptance", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("install acceptance helper could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallAcceptanceTest(unittest.TestCase):
    COMMIT = "a" * 40
    RUN_ID = "b" * 64
    NOW = datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.module = load_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / f"run-{self.RUN_ID}"
        self.workspace.mkdir()
        self.version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.rbz = self.workspace / f"sketchup-mcp-{self.version}.rbz"
        self.wheel = self.workspace / f"sketchup_mcp-{self.version}-py3-none-any.whl"
        self.sdist = self.workspace / f"sketchup_mcp-{self.version}.tar.gz"
        for path, contents in (
            (self.rbz, b"exact rbz candidate"),
            (self.wheel, b"exact wheel candidate"),
            (self.sdist, b"exact source candidate"),
        ):
            path.write_bytes(contents)
        context = {
            "schema_version": 2,
            "run_id": self.RUN_ID,
            "created_at": self.NOW.isoformat(),
            "commit": self.COMMIT,
            "attestation": {
                "type": "manual",
                "operator": "Ada Example",
                "licensed_sketchup_confirmed": True,
                "single_testup_process_confirmed": True,
            },
        }
        self.context = self.workspace / "run-context.json"
        self.context.write_text(json.dumps(context), encoding="utf-8")

    def _prepare(self, **overrides):
        distribution = {
            "name": "sketchup-mcp",
            "version": self.version,
            "source": "exact candidate wheel",
            "wheel_sha256": hashlib.sha256(self.wheel.read_bytes()).hexdigest(),
            "python": "CHECKOUT/.venv/bin/python",
            "console_script": "CHECKOUT/.venv/bin/sketchup-mcp",
            "installation_verified": True,
            "direct_url": "REDACTED/exact-candidate-wheel",
            "installed_files": {"sketchup_mcp/__init__.py": "c" * 64},
        }
        with mock.patch.object(
            self.module, "_installed_distribution", return_value=distribution
        ):
            arguments = {
                "repo_root": REPO_ROOT,
                "run_context": self.context,
                "rbz_path": self.rbz,
                "wheel_path": self.wheel,
                "sdist_path": self.sdist,
                "commit": self.COMMIT,
                "dispatcher": "ada-login",
                "github_run_id": 123456,
                "port": 19877,
                "os_version": "TestOS 1",
                "python_executable": Path("/protected/checkout/.venv/bin/python"),
                "enforce_installed_distribution": True,
                "now": self.NOW,
            }
            arguments.update(overrides)
            return self.module.prepare(**arguments)

    def _write_raw(self, acceptance):
        prepared = json.loads((acceptance / "prepared.json").read_text())
        timestamp = self.NOW.isoformat()
        raw = {
            "bridge-ready.json": {
                "schema_version": 1,
                "kind": "sketchup_mcp.install_acceptance.ready",
                "run_id": self.RUN_ID,
                "commit": self.COMMIT,
                "version": self.version,
                "catalog_sha256": prepared["catalog_sha256"],
                "port": 19877,
                "sketchup_version": "2025.0.0",
                "os_version": "TestOS 1",
                "created_at": timestamp,
            },
            "mcp-session.json": {
                "schema_version": 1,
                "kind": "sketchup_mcp.install_acceptance.mcp_session",
                "run_id": self.RUN_ID,
                "started_at": timestamp,
                "completed_at": timestamp,
                "initialized": True,
                "tools": prepared["expected_tools"],
                "call": {
                    "name": "get_selection",
                    "arguments": {},
                    "raw_call_tool_result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": '{"entities":[]}',
                                            }
                                        ],
                                        "isError": False,
                                        "success": True,
                                    }
                                ),
                            }
                        ],
                        "isError": False,
                    },
                },
            },
            "bridge-exit.json": {
                "schema_version": 1,
                "kind": "sketchup_mcp.install_acceptance.exit",
                "run_id": self.RUN_ID,
                "status": "stopped",
                "created_at": timestamp,
            },
        }
        for name, document in raw.items():
            (acceptance / name).write_text(
                json.dumps(document, sort_keys=True) + "\n", encoding="utf-8"
            )

    def test_prepare_copies_static_harness_and_binds_exact_candidates(self):
        acceptance = self._prepare()

        self.assertEqual(self.workspace / "install-acceptance", acceptance)
        harness = acceptance / "startup.rb"
        self.assertEqual(
            (REPO_ROOT / "testup" / "install_acceptance" / "startup.rb").read_bytes(),
            harness.read_bytes(),
        )
        startup_source = harness.read_text(encoding="utf-8")
        self.assertIn("File.join(__dir__, 'runtime-input.json')", startup_source)
        self.assertIn("SU_MCP.extension_runtime", startup_source)
        self.assertIn("Sketchup.active_model.selection.clear", startup_source)
        self.assertIn("runtime.stop", startup_source)
        self.assertIn("Sketchup.quit", startup_source)
        self.assertIn("expected_stop = \"#{identity.fetch('run_id')}\\n\"", startup_source)
        self.assertIn("stop_marker_identity_differs", startup_source)
        self.assertIn("'os_version' => identity.fetch('os_version')", startup_source)
        self.assertNotIn("RUBY_PLATFORM", startup_source)
        self.assertNotIn("eval(", startup_source)
        self.assertNotIn("system(", startup_source)
        main_source = (REPO_ROOT / "su_mcp" / "main.rb").read_text(encoding="utf-8")
        self.assertIn("def self.extension_runtime", main_source)
        schema = json.loads(
            (REPO_ROOT / "testup" / "install_acceptance" / "evidence.schema.json").read_text()
        )
        self.assertEqual("sketchup_mcp.install_acceptance", schema["properties"]["kind"]["const"])
        prepared = json.loads((acceptance / "prepared.json").read_text())
        self.assertEqual("sketchup_mcp.install_acceptance.prepared", prepared["kind"])
        self.assertEqual(self.RUN_ID, prepared["run_id"])
        self.assertEqual("Ada Example", prepared["operator"])
        self.assertEqual("ada-login", prepared["dispatcher"])
        self.assertEqual(123456, prepared["github_run_id"])
        self.assertEqual("127.0.0.1", prepared["bridge_host"])
        self.assertEqual(19877, prepared["bridge_port"])
        self.assertEqual("TestOS 1", prepared["os_version"])
        self.assertEqual(
            "CHECKOUT/.venv/bin/sketchup-mcp",
            prepared["mcp_host_config"]["command"],
        )
        self.assertEqual([], prepared["mcp_host_config"]["args"])
        for name, path in (("rbz", self.rbz), ("wheel", self.wheel), ("sdist", self.sdist)):
            with self.subTest(candidate=name):
                self.assertEqual(path.name, prepared["candidates"][name]["filename"])
                self.assertEqual(path.stat().st_size, prepared["candidates"][name]["size"])
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    prepared["candidates"][name]["sha256"],
                )
        self.assertEqual(
            prepared,
            json.loads((acceptance / "runtime-input.json").read_text()),
        )

    def test_collect_and_validate_require_the_real_empty_selection_envelope(self):
        acceptance = self._prepare()
        self._write_raw(acceptance)

        evidence_path = self.module.collect(
            repo_root=REPO_ROOT,
            acceptance_dir=acceptance,
            expected_commit=self.COMMIT,
            expected_dispatcher="ada-login",
            now=self.NOW,
        )
        evidence = self.module.validate(
            repo_root=REPO_ROOT,
            acceptance_dir=acceptance,
            evidence_path=evidence_path,
            rbz_path=self.rbz,
            wheel_path=self.wheel,
            sdist_path=self.sdist,
            expected_commit=self.COMMIT,
            expected_dispatcher="ada-login",
            expected_github_run_id=123456,
            now=self.NOW,
        )

        self.assertEqual("sketchup_mcp.install_acceptance", evidence["kind"])
        self.assertEqual("pass", evidence["status"])
        self.assertEqual(
            {
                "bridge-exit.json",
                "bridge-ready.json",
                "evidence.schema.json",
                "mcp-host-config.json",
                "mcp-session.json",
                "prepared.json",
                "python-distribution.json",
                "runtime-input.json",
                "startup.rb",
            },
            set(evidence["artifacts"]),
        )

        evidence["absolute_path"] = "/protected/runner/secret"
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        with self.assertRaisesRegex(self.module.AcceptanceError, "fields differ"):
            self.module.validate(
                repo_root=REPO_ROOT,
                acceptance_dir=acceptance,
                evidence_path=evidence_path,
                rbz_path=self.rbz,
                wheel_path=self.wheel,
                sdist_path=self.sdist,
                expected_commit=self.COMMIT,
                expected_dispatcher="ada-login",
                expected_github_run_id=123456,
                now=self.NOW,
            )

        session = json.loads((acceptance / "mcp-session.json").read_text())
        session["call"]["raw_call_tool_result"]["content"][0]["text"] = json.dumps(
            {
                "content": [{"type": "text", "text": '{"entities":[{"id":1}]}' }],
                "isError": False,
                "success": True,
            }
        )
        (acceptance / "mcp-session.json").write_text(json.dumps(session), encoding="utf-8")
        with self.assertRaisesRegex(self.module.AcceptanceError, "empty selection"):
            self.module.collect(
                repo_root=REPO_ROOT,
                acceptance_dir=acceptance,
                expected_commit=self.COMMIT,
                expected_dispatcher="ada-login",
                now=self.NOW,
            )

    def test_fail_closed_for_unsafe_paths_tampering_and_false_attestations(self):
        acceptance = self._prepare()
        self._write_raw(acceptance)
        evidence_path = self.module.collect(
            repo_root=REPO_ROOT,
            acceptance_dir=acceptance,
            expected_commit=self.COMMIT,
            expected_dispatcher="ada-login",
            now=self.NOW,
        )

        ready = acceptance / "bridge-ready.json"
        ready.write_text(ready.read_text() + " ", encoding="utf-8")
        with self.assertRaisesRegex(self.module.AcceptanceError, "artifact proof"):
            self.module.validate(
                repo_root=REPO_ROOT,
                acceptance_dir=acceptance,
                evidence_path=evidence_path,
                rbz_path=self.rbz,
                wheel_path=self.wheel,
                sdist_path=self.sdist,
                expected_commit=self.COMMIT,
                expected_dispatcher="ada-login",
                now=self.NOW,
            )

        ready.unlink()
        ready.symlink_to(acceptance / "bridge-exit.json")
        with self.assertRaisesRegex(self.module.AcceptanceError, "symlink"):
            self.module.collect(
                repo_root=REPO_ROOT,
                acceptance_dir=acceptance,
                expected_commit=self.COMMIT,
                expected_dispatcher="ada-login",
                now=self.NOW,
            )

        context = json.loads(self.context.read_text())
        context["attestation"]["licensed_sketchup_confirmed"] = False
        self.context.write_text(json.dumps(context), encoding="utf-8")
        with self.assertRaisesRegex(self.module.AcceptanceError, "licensed"):
            self._prepare()

    def test_prepare_rejects_candidate_aliases_and_symlinked_context_parent(self):
        for argument, target in (
            ("rbz_path", self.rbz),
            ("wheel_path", self.wheel),
            ("sdist_path", self.sdist),
        ):
            alias = Path(self.temporary.name) / f"{argument}-alias"
            alias.symlink_to(target)
            with self.subTest(argument=argument):
                with self.assertRaisesRegex(
                    self.module.AcceptanceError, "cannot traverse a symlink"
                ):
                    self._prepare(**{argument: alias})

        workspace_alias = Path(self.temporary.name) / "workspace-alias"
        workspace_alias.symlink_to(self.workspace, target_is_directory=True)
        with self.assertRaisesRegex(
            self.module.AcceptanceError, "cannot traverse a symlink"
        ):
            self._prepare(run_context=workspace_alias / "run-context.json")

    def test_collect_rejects_empty_or_cross_bundle_operator(self):
        acceptance = self._prepare()
        self._write_raw(acceptance)
        prepared_path = acceptance / "prepared.json"
        runtime_input_path = acceptance / "runtime-input.json"

        for operator in ("", "Grace Example"):
            prepared = json.loads(prepared_path.read_text())
            prepared["operator"] = operator
            for path in (prepared_path, runtime_input_path):
                path.write_text(json.dumps(prepared), encoding="utf-8")
            with self.subTest(operator=operator):
                with self.assertRaisesRegex(
                    self.module.AcceptanceError, "operator differs"
                ):
                    self.module.collect(
                        repo_root=REPO_ROOT,
                        acceptance_dir=acceptance,
                        expected_commit=self.COMMIT,
                        expected_dispatcher="ada-login",
                        now=self.NOW,
                    )

    def test_self_consistent_host_config_mutation_is_rejected(self):
        acceptance = self._prepare()
        self._write_raw(acceptance)
        prepared_path = acceptance / "prepared.json"
        runtime_input_path = acceptance / "runtime-input.json"
        config_path = acceptance / "mcp-host-config.json"
        prepared = json.loads(prepared_path.read_text())
        prepared["mcp_host_config"]["args"] = ["--unexpected"]
        for path, document in (
            (prepared_path, prepared),
            (runtime_input_path, prepared),
            (config_path, prepared["mcp_host_config"]),
        ):
            path.write_text(json.dumps(document), encoding="utf-8")

        with self.assertRaisesRegex(self.module.AcceptanceError, "host config"):
            self.module.collect(
                repo_root=REPO_ROOT,
                acceptance_dir=acceptance,
                expected_commit=self.COMMIT,
                expected_dispatcher="ada-login",
                now=self.NOW,
            )

    def test_stop_marker_is_exclusive_identity_bound_and_rejects_symlinks(self):
        acceptance = self._prepare()
        stop = self.module.ensure_stop_marker(
            acceptance_dir=acceptance, run_id=self.RUN_ID
        )
        self.assertEqual((self.RUN_ID + "\n").encode("ascii"), stop.read_bytes())
        self.assertEqual(
            stop,
            self.module.ensure_stop_marker(
                acceptance_dir=acceptance, run_id=self.RUN_ID
            ),
        )

        stop.unlink()
        stop.write_text("wrong\n", encoding="utf-8")
        with self.assertRaisesRegex(self.module.AcceptanceError, "identity differs"):
            self.module.ensure_stop_marker(
                acceptance_dir=acceptance, run_id=self.RUN_ID
            )

        for target in (acceptance / "missing", acceptance / "prepared.json"):
            stop.unlink()
            stop.symlink_to(target)
            with self.subTest(target=target.name):
                with self.assertRaisesRegex(self.module.AcceptanceError, "symlink"):
                    self.module.ensure_stop_marker(
                        acceptance_dir=acceptance, run_id=self.RUN_ID
                    )

    def test_cli_has_only_fixed_actions(self):
        parser = self.module.parser()
        help_text = parser.format_help()
        for action in ("prepare", "collect", "signal-stop", "validate"):
            self.assertIn(action, help_text)
        for prohibited in ("--command", "--url", "--host", "--stop-command"):
            self.assertNotIn(prohibited, help_text)

    @unittest.skipIf(
        os.environ.get("SKETCHUP_MCP_DETERMINISTIC_TESTS") == "1",
        "the deterministic coverage gate forbids real loopback integration",
    )
    def test_official_stdio_client_crosses_the_controlled_tcp_ruby_boundary(self):
        ruby_envelope = {
            "content": [{"type": "text", "text": '{"entities":[]}'}],
            "isError": False,
            "success": True,
        }
        with ScriptedBridge([send_result(ruby_envelope)]) as bridge:
            session = asyncio.run(
                self.module._run_mcp(
                    {
                        "run_id": self.RUN_ID,
                        "bridge_port": 1,
                        "mcp_host_config": {
                            "schema_version": 1,
                            "kind": "sketchup_mcp.install_acceptance.mcp_host_config",
                            "transport": "stdio",
                            "command": "CHECKOUT/.venv/bin/sketchup-mcp",
                            "args": [],
                            "environment": {
                                "SKETCHUP_MCP_BRIDGE_PORT": str(bridge.port)
                            },
                        },
                    }
                )
            )

        self.assertTrue(session["initialized"])
        catalog = json.loads(
            (REPO_ROOT / "src/sketchup_mcp/command_catalog.json").read_text()
        )
        self.assertEqual(
            [command["name"] for command in catalog["commands"]], session["tools"]
        )
        self.assertEqual("get_selection", bridge.requests[0]["params"]["name"])
        raw = session["call"]["raw_call_tool_result"]
        self.assertFalse(raw["isError"])
        self.assertEqual(ruby_envelope, json.loads(raw["content"][0]["text"]))


if __name__ == "__main__":
    unittest.main()
