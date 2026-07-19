import importlib.util
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_SCRIPT = REPO_ROOT / "scripts" / "sketchup_runtime_runner.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("sketchup_runtime_runner", RUNNER_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("SketchUp runtime runner helper could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CandidateInstallWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.plugins = Path(self.temporary.name) / "Plugins"
        self.plugins.mkdir()
        (self.plugins / ".sketchup-mcp-runtime-runner.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "sketchup_mcp.protected_runtime_plugins",
                    "repository": "coral-garden/sketchup-mcp",
                }
            ),
            encoding="utf-8",
        )

    def test_cleanup_removes_only_the_extension_targets_from_sentinel_plugins_dir(self):
        runner = load_runner()
        (self.plugins / "su_mcp.rb").write_text("stale", encoding="utf-8")
        support = self.plugins / "su_mcp"
        support.mkdir()
        (support / "stale.rb").write_text("stale", encoding="utf-8")
        neighbor = self.plugins / "keep_this_extension.rb"
        neighbor.write_text("keep", encoding="utf-8")

        removed = runner.clean_extension_install(self.plugins)

        self.assertEqual(("su_mcp.rb", "su_mcp/"), removed)
        self.assertFalse((self.plugins / "su_mcp.rb").exists())
        self.assertFalse(support.exists())
        self.assertEqual("keep", neighbor.read_text(encoding="utf-8"))
        self.assertTrue(
            (self.plugins / ".sketchup-mcp-runtime-runner.json").is_file()
        )

    def test_cleanup_refuses_an_unmarked_or_non_plugins_directory_without_deleting(self):
        runner = load_runner()
        target = self.plugins / "su_mcp.rb"
        target.write_text("keep", encoding="utf-8")
        sentinel = self.plugins / ".sketchup-mcp-runtime-runner.json"
        cases = (
            ("missing sentinel", self.plugins, None),
            ("wrong sentinel", self.plugins, {"schema_version": 1}),
            ("wrong directory name", self.plugins.parent, runner.SENTINEL_DOCUMENT),
        )
        for label, directory, sentinel_document in cases:
            with self.subTest(case=label):
                sentinel.unlink(missing_ok=True)
                if sentinel_document is not None:
                    sentinel.write_text(json.dumps(sentinel_document), encoding="utf-8")
                with self.assertRaises(runner.RunnerError):
                    runner.clean_extension_install(directory)
                self.assertEqual("keep", target.read_text(encoding="utf-8"))

    def test_cleanup_refuses_every_wrong_target_kind_without_deleting_any_target(self):
        runner = load_runner()

        for target_kind in (
            "loader directory",
            "support file",
            "broken loader symlink",
            "support symlink",
            "support reparse point",
            "support mount",
        ):
            with self.subTest(target_kind=target_kind):
                case_root = Path(self.temporary.name) / target_kind.replace(" ", "-")
                plugins = case_root / "Plugins"
                plugins.mkdir(parents=True)
                (plugins / runner.PLUGINS_SENTINEL).write_text(
                    json.dumps(runner.SENTINEL_DOCUMENT), encoding="utf-8"
                )
                loader = plugins / "su_mcp.rb"
                support = plugins / "su_mcp"
                external = case_root / "external"
                external.mkdir()
                (external / "keep.txt").write_text("external", encoding="utf-8")

                if target_kind == "loader directory":
                    loader.mkdir()
                    (loader / "keep.txt").write_text("loader", encoding="utf-8")
                    support.mkdir()
                    (support / "keep.txt").write_text("support", encoding="utf-8")
                elif target_kind == "support file":
                    loader.write_text("loader", encoding="utf-8")
                    support.write_text("support", encoding="utf-8")
                elif target_kind == "broken loader symlink":
                    loader.symlink_to(case_root / "missing-loader")
                    support.mkdir()
                    (support / "keep.txt").write_text("support", encoding="utf-8")
                elif target_kind == "support symlink":
                    loader.write_text("loader", encoding="utf-8")
                    support.symlink_to(external, target_is_directory=True)
                else:
                    loader.write_text("loader", encoding="utf-8")
                    support.mkdir()
                    (support / "keep.txt").write_text("support", encoding="utf-8")

                patches = []
                if target_kind == "support reparse point":
                    real_lstat = os.lstat

                    def reparse_lstat(path, *args, **kwargs):
                        metadata = real_lstat(path, *args, **kwargs)
                        if Path(path) != support:
                            return metadata

                        class ReparseStat:
                            st_mode = metadata.st_mode
                            st_file_attributes = 0x400

                        return ReparseStat()

                    patches.append(mock.patch.object(runner.os, "lstat", reparse_lstat))
                if target_kind == "support mount":
                    real_ismount = os.path.ismount
                    patches.append(
                        mock.patch.object(
                            runner.os.path,
                            "ismount",
                            side_effect=lambda path: Path(path) == support
                            or real_ismount(path),
                        )
                    )

                for patcher in patches:
                    patcher.start()
                try:
                    with self.assertRaises(runner.RunnerError):
                        runner.clean_extension_install(plugins)
                finally:
                    for patcher in reversed(patches):
                        patcher.stop()

                self.assertTrue(os.path.lexists(loader))
                self.assertTrue(os.path.lexists(support))
                self.assertEqual("external", (external / "keep.txt").read_text())
                if loader.is_dir() and not loader.is_symlink():
                    self.assertEqual("loader", (loader / "keep.txt").read_text())
                elif loader.is_file():
                    self.assertEqual("loader", loader.read_text())
                if support.is_dir() and not support.is_symlink():
                    self.assertEqual("support", (support / "keep.txt").read_text())
                elif support.is_file():
                    self.assertEqual("support", support.read_text())

    @unittest.skipIf(
        os.environ.get("SKETCHUP_MCP_DETERMINISTIC_TESTS") == "1",
        "Ruby subprocess is exercised by the integration suite",
    )
    def test_generated_bootstrap_installs_verifies_and_loads_the_exact_candidate(self):
        runner = load_runner()
        workspace = Path(self.temporary.name) / f"run-{'b' * 64}"
        workspace.mkdir()
        rbz = workspace / "sketchup-mcp-1.2.3.rbz"
        rbz.write_bytes(b"candidate-rbz")
        receipt = workspace / "candidate-preclean.json"
        receipt.write_text('{"precleaned":true}\n', encoding="utf-8")
        (self.plugins / "su_mcp.rb").write_text(
            "module SU_MCP; VERSION = '1.2.3'; end\n"
            "load File.join(__dir__, 'su_mcp', 'sketchup_adapter.rb')\n",
            encoding="utf-8",
        )
        support = self.plugins / "su_mcp"
        support.mkdir()
        adapter = support / "sketchup_adapter.rb"
        adapter.write_text(
            "module SU_MCP; class SketchupAdapter; def initialize; end; end; end\n",
            encoding="utf-8",
        )
        manifest = {
            "su_mcp.rb": runner.sha256_file(self.plugins / "su_mcp.rb"),
            "su_mcp/sketchup_adapter.rb": runner.sha256_file(adapter),
        }
        identity = runner.CandidateInstallIdentity.create(
            commit="a" * 40,
            run_id="b" * 64,
            version="1.2.3",
            operator="Ada Example",
            dispatcher="ada-login",
            installed_files=manifest,
        )
        bootstrap = runner.write_install_bootstrap(
            workspace=workspace,
            rbz_path=rbz,
            plugins_dir=self.plugins,
            identity=identity,
            preclean_receipt=receipt,
        )
        wrapper = Path(self.temporary.name) / "fake_sketchup.rb"
        wrapper.write_text(
            "module Sketchup\n"
            "  def self.find_support_file(name)\n"
            "    raise 'wrong support role' unless name == 'Plugins'\n"
            "    ENV.fetch('ACTUAL_PLUGINS')\n"
            "  end\n"
            "  def self.install_from_archive(path, load_on_success)\n"
            "    raise 'wrong archive' unless path == ENV.fetch('EXPECTED_RBZ')\n"
            "    raise 'must not auto-load' unless load_on_success == false\n"
            "    true\n"
            "  end\n"
            "end\n"
            "load ARGV.fetch(0)\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            ["ruby", str(wrapper), str(bootstrap)],
            check=False,
            capture_output=True,
            text=True,
            env={
                "ACTUAL_PLUGINS": str(self.plugins.resolve()),
                "EXPECTED_RBZ": str(rbz.resolve()),
            },
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        marker = json.loads(
            (workspace / "candidate-install.json").read_text(encoding="utf-8")
        )
        self.assertEqual("success", marker["status"])
        self.assertEqual("a" * 40, marker["commit"])
        self.assertEqual("b" * 64, marker["run_id"])
        self.assertEqual("Ada Example", marker["operator"])
        self.assertEqual("ada-login", marker["dispatcher"])
        self.assertTrue(marker["candidate_install_confirmed"])
        self.assertEqual(manifest, marker["installed_files"])
        self.assertEqual(runner.sha256_file(rbz), marker["rbz"]["sha256"])
        self.assertEqual(
            runner.sha256_file(bootstrap), marker["bootstrap_sha256"]
        )
        self.assertEqual(
            runner.sha256_file(receipt), marker["preclean_sha256"]
        )
        install_log = workspace / "candidate-install.log"
        self.assertIn("candidate_install:success", install_log.read_text())
        self.assertEqual(
            runner.sha256_file(install_log), marker["install_log_sha256"]
        )

    @unittest.skipIf(
        os.environ.get("SKETCHUP_MCP_DETERMINISTIC_TESTS") == "1",
        "Ruby subprocess is exercised by the integration suite",
    )
    def test_bootstrap_treats_hostile_paths_and_identity_strings_as_json_data(self):
        runner = load_runner()
        payload = "#{raise 'owned'} \"quoted\" \\ path"
        case_root = Path(self.temporary.name) / f"hostile-{payload}"
        workspace = case_root / f"run-{'b' * 64}"
        plugins = case_root / "Plugins"
        workspace.mkdir(parents=True)
        plugins.mkdir()
        (plugins / runner.PLUGINS_SENTINEL).write_text(
            json.dumps(runner.SENTINEL_DOCUMENT), encoding="utf-8"
        )
        rbz = workspace / f"sketchup-mcp-{payload}.rbz"
        rbz.write_bytes(b"candidate-rbz")
        receipt = workspace / "candidate-preclean.json"
        receipt.write_text('{"precleaned":true}\n', encoding="utf-8")
        loader = plugins / "su_mcp.rb"
        loader.write_text(
            "module SU_MCP; VERSION = '1.2.3'; end\n"
            "load File.join(__dir__, 'su_mcp', 'sketchup_adapter.rb')\n",
            encoding="utf-8",
        )
        support = plugins / "su_mcp"
        support.mkdir()
        adapter = support / "sketchup_adapter.rb"
        adapter.write_text(
            "module SU_MCP; class SketchupAdapter; def initialize; end; end; end\n",
            encoding="utf-8",
        )
        manifest = {
            "su_mcp.rb": runner.sha256_file(loader),
            "su_mcp/sketchup_adapter.rb": runner.sha256_file(adapter),
        }
        operator = f"Ada {payload}"
        identity = runner.CandidateInstallIdentity.create(
            commit="a" * 40,
            run_id="b" * 64,
            version="1.2.3",
            operator=operator,
            dispatcher="ada-login",
            installed_files=manifest,
        )

        bootstrap = runner.write_install_bootstrap(
            workspace=workspace,
            rbz_path=rbz,
            plugins_dir=plugins,
            identity=identity,
            preclean_receipt=receipt,
        )

        source = bootstrap.read_text(encoding="utf-8")
        bootstrap_input = workspace / "candidate-install-input.json"
        input_document = json.loads(bootstrap_input.read_text(encoding="utf-8"))
        for configured_value in (
            str(rbz.resolve()),
            str(plugins.resolve()),
            operator,
            "ada-login",
            "a" * 40,
            "b" * 64,
            "1.2.3",
            manifest["su_mcp.rb"],
            manifest["su_mcp/sketchup_adapter.rb"],
        ):
            self.assertNotIn(configured_value, source)
        self.assertIn(
            "File.expand_path('candidate-install-input.json', __dir__)", source
        )
        self.assertEqual(operator, input_document["identity"]["operator"])
        self.assertEqual(str(rbz.resolve()), input_document["rbz_path"])
        self.assertEqual(str(plugins.resolve()), input_document["plugins_dir"])

        wrapper = case_root / "fake_sketchup.rb"
        wrapper.write_text(
            "module Sketchup\n"
            "  def self.find_support_file(name)\n"
            "    raise 'wrong support role' unless name == 'Plugins'\n"
            "    ENV.fetch('ACTUAL_PLUGINS')\n"
            "  end\n"
            "  def self.install_from_archive(path, load_on_success)\n"
            "    raise 'wrong archive' unless path == ENV.fetch('EXPECTED_RBZ')\n"
            "    raise 'must not auto-load' unless load_on_success == false\n"
            "    true\n"
            "  end\n"
            "end\n"
            "load ARGV.fetch(0)\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            ["ruby", str(wrapper), str(bootstrap)],
            check=False,
            capture_output=True,
            text=True,
            env={
                "ACTUAL_PLUGINS": str(plugins.resolve()),
                "EXPECTED_RBZ": str(rbz.resolve()),
            },
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        marker = json.loads(
            (workspace / "candidate-install.json").read_text(encoding="utf-8")
        )
        self.assertEqual(operator, marker["operator"])
        self.assertEqual(
            {
                "filename": "candidate-install-input.json",
                "sha256": runner.sha256_file(bootstrap_input),
                "size": len(bootstrap_input.read_bytes()),
            },
            marker["bootstrap_input"],
        )

    @unittest.skipIf(
        os.environ.get("SKETCHUP_MCP_DETERMINISTIC_TESTS") == "1",
        "Ruby subprocess is exercised by the integration suite",
    )
    def test_bootstrap_requires_the_active_runtime_plugins_directory_identity(self):
        runner = load_runner()
        cases = (
            ("canonical alias", "alias", True, None),
            ("legacy canonical alias", "legacy_alias", True, None),
            ("different directory", "different", False, "runtime_plugins_identity_differs"),
            ("missing directory", "nil", False, "runtime_plugins_unavailable"),
            ("nonexistent directory", "nonexistent", False, "runtime_plugins_unavailable"),
            ("regular file", "file", False, "runtime_plugins_unavailable"),
        )

        for label, actual_kind, succeeds, expected_error in cases:
            with self.subTest(case=label):
                case_root = Path(self.temporary.name) / label.replace(" ", "-")
                workspace = case_root / f"run-{'7' * 64}"
                plugins = case_root / "Plugins"
                workspace.mkdir(parents=True)
                plugins.mkdir()
                (plugins / runner.PLUGINS_SENTINEL).write_text(
                    json.dumps(runner.SENTINEL_DOCUMENT), encoding="utf-8"
                )
                loader = plugins / "su_mcp.rb"
                loader.write_text(
                    "File.write(ENV.fetch('ENTRYPOINT_FILE'), 'loaded')\n"
                    "module SU_MCP; VERSION = '1.2.3'; end\n"
                    "load File.join(__dir__, 'su_mcp', 'sketchup_adapter.rb')\n",
                    encoding="utf-8",
                )
                support = plugins / "su_mcp"
                support.mkdir()
                adapter = support / "sketchup_adapter.rb"
                adapter.write_text(
                    "module SU_MCP; class SketchupAdapter; def initialize; end; end; end\n",
                    encoding="utf-8",
                )
                manifest = {
                    "su_mcp.rb": runner.sha256_file(loader),
                    "su_mcp/sketchup_adapter.rb": runner.sha256_file(adapter),
                }
                rbz = workspace / "sketchup-mcp-1.2.3.rbz"
                rbz.write_bytes(b"candidate")
                receipt = workspace / "candidate-preclean.json"
                receipt.write_text("{}\n", encoding="utf-8")
                identity = runner.CandidateInstallIdentity.create(
                    commit="6" * 40,
                    run_id="7" * 64,
                    version="1.2.3",
                    operator="Ada Example",
                    dispatcher="ada-login",
                    installed_files=manifest,
                )
                bootstrap = runner.write_install_bootstrap(
                    workspace=workspace,
                    rbz_path=rbz,
                    plugins_dir=plugins,
                    identity=identity,
                    preclean_receipt=receipt,
                )
                if actual_kind in {"alias", "legacy_alias"}:
                    actual_plugins = case_root / "runtime-plugins-alias"
                    actual_plugins.symlink_to(plugins, target_is_directory=True)
                    actual_value = str(actual_plugins)
                elif actual_kind == "different":
                    actual_plugins = case_root / "other-Plugins"
                    actual_plugins.mkdir()
                    actual_value = str(actual_plugins)
                elif actual_kind == "nil":
                    actual_value = "__nil__"
                elif actual_kind == "nonexistent":
                    actual_value = str(case_root / "missing-Plugins")
                else:
                    actual_plugins = case_root / "plugins-file"
                    actual_plugins.write_text("not a directory", encoding="utf-8")
                    actual_value = str(actual_plugins)
                calls = workspace / "install-calls.txt"
                entrypoint = workspace / "entrypoint-loaded.txt"
                wrapper = case_root / "fake_sketchup.rb"
                wrapper.write_text(
                    "if ENV['DISABLE_IDENTICAL'] == '1'\n"
                    "  class << File; undef_method :identical?; end\n"
                    "end\n"
                    "module Sketchup\n"
                    "  @install_calls = 0\n"
                    "  def self.find_support_file(name)\n"
                    "    raise 'wrong support role' unless name == 'Plugins'\n"
                    "    value = ENV.fetch('ACTUAL_PLUGINS')\n"
                    "    value == '__nil__' ? nil : value\n"
                    "  end\n"
                    "  def self.install_from_archive(_path, load_on_success)\n"
                    "    raise 'must not auto-load' unless load_on_success == false\n"
                    "    @install_calls += 1\n"
                    "    true\n"
                    "  end\n"
                    "  at_exit { File.write(ENV.fetch('CALLS_FILE'), @install_calls.to_s) }\n"
                    "end\n"
                    "load ARGV.fetch(0)\n",
                    encoding="utf-8",
                )

                completed = subprocess.run(
                    ["ruby", str(wrapper), str(bootstrap)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env={
                        "ACTUAL_PLUGINS": actual_value,
                        "CALLS_FILE": str(calls),
                        "ENTRYPOINT_FILE": str(entrypoint),
                        "DISABLE_IDENTICAL": (
                            "1" if actual_kind == "legacy_alias" else "0"
                        ),
                    },
                )

                marker = json.loads(
                    (workspace / "candidate-install.json").read_text(encoding="utf-8")
                )
                if succeeds:
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    self.assertEqual("1", calls.read_text(encoding="utf-8"))
                    self.assertEqual("loaded", entrypoint.read_text(encoding="utf-8"))
                    self.assertEqual("success", marker["status"])
                else:
                    self.assertNotEqual(0, completed.returncode)
                    self.assertEqual("0", calls.read_text(encoding="utf-8"))
                    self.assertFalse(entrypoint.exists())
                    self.assertEqual("failure", marker["status"])
                    self.assertEqual(expected_error, marker["error"])
                    self.assertIn(
                        f"SketchUp MCP candidate install failed: {expected_error}",
                        completed.stderr,
                    )

    def test_prepare_requires_named_operator_and_all_explicit_manual_attestations(self):
        runner = load_runner()
        valid = {
            "repo_root": REPO_ROOT,
            "artifact_dir": Path(self.temporary.name) / "artifacts",
            "rbz_path": Path(self.temporary.name) / "candidate.rbz",
            "plugins_dir": self.plugins,
            "commit": "a" * 40,
            "operator": "Ada Example",
            "dispatcher": "ada-login",
            "licensed_runner_confirmed": True,
            "single_testup_process_confirmed": True,
            "candidate_install_confirmed": True,
        }
        cases = {
            "automatic operator": {"operator": "automatic"},
            "default operator": {"operator": "default"},
            "missing operator": {"operator": ""},
            "unlicensed": {"licensed_runner_confirmed": False},
            "multiple TestUp processes": {
                "single_testup_process_confirmed": False
            },
            "candidate install unconfirmed": {"candidate_install_confirmed": False},
        }

        for label, changes in cases.items():
            with self.subTest(case=label):
                arguments = {**valid, **changes}
                with self.assertRaises(runner.RunnerError):
                    runner.prepare_runtime(**arguments)

    def test_candidate_install_identity_is_validated_and_immutable(self):
        runner = load_runner()
        identity = runner.CandidateInstallIdentity.create(
            commit="a" * 40,
            run_id="b" * 64,
            version="1.2.3",
            operator="Ada Example",
            dispatcher="ada-login",
            installed_files={
                "su_mcp.rb": "c" * 64,
                "su_mcp/sketchup_adapter.rb": "d" * 64,
            },
        )

        with self.assertRaises(FrozenInstanceError):
            identity.operator = "Mallory"
        self.assertIsInstance(identity.installed_files, tuple)
        self.assertEqual("Ada Example", identity.document()["operator"])

    def test_github_output_requires_exactly_one_candidate_archive(self):
        runner = load_runner()
        workspace = Path(self.temporary.name) / f"run-{'e' * 64}"
        workspace.mkdir()
        context = workspace / "run-context.json"
        context.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "run_id": "e" * 64,
                    "created_at": "2026-07-20T01:00:00+00:00",
                    "commit": "a" * 40,
                    "attestation": {
                        "type": "manual",
                        "operator": "Ada Example",
                        "licensed_sketchup_confirmed": True,
                        "single_testup_process_confirmed": True,
                    },
                    "rbz": {"filename": "candidate.rbz", "sha256": "f" * 64},
                    "config_sha256": "d" * 64,
                    "seed": 1,
                    "artifacts": {
                        "testup_config": "testup-ci.generated.yml",
                        "testup_results": "testup-results.json",
                        "error_log": "testup-error.log",
                        "runtime_report": "runtime-report.json",
                        "suite_marker": "suite-marker.json",
                        "log_directory": "logs",
                    },
                }
            ),
            encoding="utf-8",
        )
        output = Path(self.temporary.name) / "github-output.txt"

        with self.assertRaisesRegex(runner.RunnerError, "exactly one candidate RBZ"):
            runner._write_github_output(output, context)

        (workspace / "sketchup-mcp-1.2.3.rbz").write_bytes(b"one")
        (workspace / "sketchup-mcp-1.2.4.rbz").write_bytes(b"two")
        with self.assertRaisesRegex(runner.RunnerError, "exactly one candidate RBZ"):
            runner._write_github_output(output, context)

    @unittest.skipIf(
        os.environ.get("SKETCHUP_MCP_DETERMINISTIC_TESTS") == "1",
        "Ruby subprocess is exercised by the integration suite",
    )
    def test_bootstrap_fails_visibly_for_false_install_or_stale_installed_bytes(self):
        runner = load_runner()
        for label, install_result, tamper, expected_error in (
            ("false install", "false", False, "install_from_archive_returned_false"),
            ("stale installed copy", "true", True, "installed_manifest_differs"),
        ):
            with self.subTest(case=label):
                case_root = Path(self.temporary.name) / label.replace(" ", "-")
                workspace = case_root / f"run-{'d' * 64}"
                workspace.mkdir(parents=True)
                plugins = case_root / "Plugins"
                plugins.mkdir()
                (plugins / ".sketchup-mcp-runtime-runner.json").write_text(
                    json.dumps(runner.SENTINEL_DOCUMENT), encoding="utf-8"
                )
                loader = plugins / "su_mcp.rb"
                loader.write_text(
                    "module SU_MCP; VERSION = '1.2.3'; end\n"
                    "load File.join(__dir__, 'su_mcp', 'sketchup_adapter.rb')\n",
                    encoding="utf-8",
                )
                support = plugins / "su_mcp"
                support.mkdir()
                adapter = support / "sketchup_adapter.rb"
                adapter.write_text(
                    "module SU_MCP; class SketchupAdapter; def initialize; end; end; end\n",
                    encoding="utf-8",
                )
                manifest = {
                    "su_mcp.rb": runner.sha256_file(loader),
                    "su_mcp/sketchup_adapter.rb": runner.sha256_file(adapter),
                }
                if tamper:
                    adapter.write_text("stale", encoding="utf-8")
                rbz = workspace / "sketchup-mcp-1.2.3.rbz"
                rbz.write_bytes(b"candidate")
                receipt = workspace / "candidate-preclean.json"
                receipt.write_text("{}\n", encoding="utf-8")
                identity = runner.CandidateInstallIdentity.create(
                    commit="c" * 40,
                    run_id="d" * 64,
                    version="1.2.3",
                    operator="Ada Example",
                    dispatcher="ada-login",
                    installed_files=manifest,
                )
                bootstrap = runner.write_install_bootstrap(
                    workspace=workspace,
                    rbz_path=rbz,
                    plugins_dir=plugins,
                    identity=identity,
                    preclean_receipt=receipt,
                )
                wrapper = case_root / "fake_sketchup.rb"
                wrapper.write_text(
                    "module Sketchup\n"
                    "  def self.find_support_file(name)\n"
                    "    raise 'wrong support role' unless name == 'Plugins'\n"
                    "    ENV.fetch('ACTUAL_PLUGINS')\n"
                    "  end\n"
                    "  def self.install_from_archive(_path, load_on_success)\n"
                    "    raise 'must not auto-load' unless load_on_success == false\n"
                    f"    {install_result}\n"
                    "  end\n"
                    "end\n"
                    "load ARGV.fetch(0)\n",
                    encoding="utf-8",
                )

                completed = subprocess.run(
                    ["ruby", str(wrapper), str(bootstrap)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env={"ACTUAL_PLUGINS": str(plugins.resolve())},
                )

                self.assertNotEqual(0, completed.returncode)
                self.assertIn(
                    f"SketchUp MCP candidate install failed: {expected_error}",
                    completed.stderr,
                )
                marker = json.loads(
                    (workspace / "candidate-install.json").read_text(encoding="utf-8")
                )
                self.assertEqual("failure", marker["status"])
                self.assertEqual(expected_error, marker["error"])


if __name__ == "__main__":
    unittest.main()
