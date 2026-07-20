# Verification and release gate

The repository has one verification entry point with two modes:

```text
uv run --locked --group test --group build python scripts/verify.py local
uv run --locked --group test --group build python scripts/verify.py release --runtime-root PATH --runtime-run-id RUN_ID
```

`local` is the contributor and hosted-CI gate. `release` repeats that gate and
then binds it to fresh evidence from licensed desktop SketchUp. Neither command
publishes a package.

## Local verification

Follow the exact fresh-clone, locked dependency, and local verifier commands in
the canonical [contributor workflow](../../CONTRIBUTING.md).

This runs the Python coverage suite, the complete Python integration suite, the
headless Ruby coverage suite, and the complete headless Ruby integration suite.
Python and headless Ruby each require exactly 100% line and branch coverage;
one scope cannot compensate for a regression in the other. The console names
each scope separately and reports the desktop SketchUp scope as external.

The machine-readable result is
`artifacts/verification/local.json`. A missing executable, suite failure,
missing or malformed report, empty metric set, or any uncovered line or branch
makes the command fail. The `Verification` GitHub workflow runs this same
command for pull requests and pushes to `main` on an unprivileged hosted Linux
runner. It never sends pull-request code to a desktop SketchUp runner.

## Produce trusted SketchUp evidence

The `SketchUp Runtime Evidence` GitHub workflow is an operator-invoked
`workflow_dispatch` workflow. Supply:

- `commit`: a full SHA already reachable from `origin/main`.
- `platform`: `windows` or `macos`.

The selected job requires the protected `sketchup-runtime` environment and a
runner carrying all four fixed labels: `self-hosted`, the selected OS,
`sketchup-runtime`, and `interactive`. Configure that environment with
`SKETCHUP_WINDOWS_EXECUTABLE` or `SKETCHUP_MACOS_EXECUTABLE`, pointing to the
licensed SketchUp executable, and `SKETCHUP_WINDOWS_PLUGINS_DIR` or
`SKETCHUP_MACOS_PLUGINS_DIR`, pointing to that SketchUp installation's absolute
`Plugins` directory. The runner must also have TestUp 2.5.4 installed.

The designated `Plugins` directory must contain this exact sentinel file at
`.sketchup-mcp-runtime-runner.json`:

```json
{
  "schema_version": 1,
  "kind": "sketchup_mcp.protected_runtime_plugins",
  "repository": "coral-garden/sketchup-mcp"
}
```

The sentinel authorizes the helper to remove only `su_mcp.rb` and `su_mcp/`.
It rejects a relative path, a directory not named `Plugins`, a symlinked root,
or a missing/different sentinel. Cleanup first validates both targets without
deleting either: the loader must be a non-symlink regular file, and the support
target must be a real non-symlink, non-junction/reparse, non-mount directory.
A wrong-kind or unreadable target fails with all targets and their contents
preserved. TestUp and every neighboring extension remain untouched. An
`always()` cleanup repeats that same exact-target operation after the run.

The dispatch form also requires a named physical `operator` and three explicit
true confirmations: designated licensed runner, exactly one SketchUp/TestUp
process, and permission to install the exact candidate. Missing, false,
automatic, or default attestations fail before preparation. The named operator
is recorded in #15 evidence. `github.actor` is recorded separately as the
workflow dispatcher; it is not treated as the operator or environment
approver. Protected-environment approval is access control, not an attestation.

Before executing candidate code, the workflow checks out trusted `main`,
requires the supplied full SHA to be an ancestor of `origin/main`, and only
then switches to that commit. It builds the RBZ, prepares a unique TestUp
workspace, and pre-cleans only the two sentinel-authorized SketchUp MCP targets
before SketchUp can load a stale copy. The `-RubyStartup` bootstrap is static
code: it contains no configured paths, identity strings, or package-manifest
values. It reads those values as data from the adjacent fixed-name
`candidate-install-input.json`, then uses the official
`Sketchup.install_from_archive(rbz, false)` API to install the exact RBZ,
verifies every installed file hash and project version, and loads that exact
entrypoint before TestUp. Before any installation call, the bootstrap obtains
the active runtime directory from `Sketchup.find_support_file('Plugins')` and
requires filesystem identity with the configured sentinel directory using
`File.identical?`. For a legacy Ruby without that API, the documented fallback
requires both canonical-path equality and matching device/inode identity.
Missing, invalid, mismatched, or unreadable runtime directories fail with a
bounded error code before installation. A failed API result, exception,
unexpected installed file, stale byte, wrong loaded adapter, or wrong version
also fails visibly. The
retained install marker binds the bootstrap input by filename, SHA-256, and byte
size, as well as binding the RBZ, pre-clean receipt, static bootstrap, install
log, dispatcher, operator, version, and loaded-file manifest. Release
verification revalidates the input binding and its identity, RBZ, and pre-clean
contents before accepting the runtime bundle.

After TestUp succeeds, the workflow starts SketchUp a second time with the
already-installed exact candidate. A checked-in static `-RubyStartup` harness
reads adjacent JSON data, clears the model selection, obtains the extension
runtime through `SU_MCP.extension_runtime`, and starts the production listener
on the dedicated loopback port `19877`. The exact candidate wheel, installed
into the locked `.venv`, runs the official Python MCP SDK stdio client. It must
complete `initialize`, return exactly the command-catalog tool names from
`list_tools`, and return the raw successful `CallToolResult` whose nested
`get_selection` payload is exactly `{"entities":[]}`. The collector then writes
the fixed stop marker; the harness stops the bridge and calls `Sketchup.quit`.
Both ready and exit markers are bounded and retained.

`scripts/install_acceptance.py` records a redacted MCP host configuration,
exact wheel/sdist/RBZ hashes and sizes, installed-distribution identity,
catalog and tool names, raw `CallToolResult`, SketchUp and OS versions,
operator, dispatcher, GitHub run ID, random run ID, and timestamps. Its final
manifest binds every raw file by SHA-256 and byte size and rejects symlinks,
unsafe workspaces, stale or changed files, false attestations, a non-loopback or
ordinary bridge port, catalog drift, or a nonempty selection. It exposes no
command, host, repository, URL, or stop-action input.
The recorded host configuration uses the same supported console-script shape as
the README (`.venv/.../sketchup-mcp`, no arguments, and the shared port
environment). The collector resolves that redacted command inside the exact
wheel-installed environment and constructs `StdioServerParameters` directly
from the recorded arguments and environment; there is no separate hard-coded
launch path that can drift from the evidence.

After SketchUp exits, the shared runner helper discovers #15's public raw
artifact bundle, collects the evidence, and calls its public validator. The
resulting artifact is named `sketchup-runtime-evidence-<workflow-run-id>` and
retains the tested RBZ, wheel, source distribution, installation proof, TestUp
evidence, install-acceptance evidence, and every raw input. A missing interactive
runner leaves the workflow unable to produce an artifact; a missing suite,
report, installation marker, acceptance marker, or raw file fails the job.

For a manual run outside GitHub, follow the exact platform commands in
[the production-adapter guide](sketchup-testup.md). Those artifacts are useful
for diagnosis, but only the protected workflow supplies the GitHub provenance
required by the release gate.

## Verify a release candidate

Run the hosted `Release Verification` workflow with:

- `candidate_sha`: the exact full SHA to verify, already reachable from
  `origin/main`.
- `runtime_run_id`: the positive numeric run ID of a successful `SketchUp
  Runtime Evidence` workflow for that same SHA.

The workflow has read-only `contents` and `actions` permissions and runs on
hosted Linux. It derives the GitHub API endpoint, artifact name, and download
path itself; neither input can supply a repository, URL, artifact name, or file
path. It downloads the named artifact from the same repository and records the
trusted GitHub run and artifact metadata beside it. It then runs the release
mode above.

Release mode first reruns all local checks and deterministically rebuilds the
RBZ from its exact `HEAD`. It rejects GitHub metadata unless the workflow name
and path, repository, `workflow_dispatch` event, successful conclusion, run ID,
artifact identity, and full SHA all match. The GitHub run, artifact, and
in-SketchUp evidence must be no more than 24 hours old. Finally it invokes the
public runtime evidence validator, which requires the exact version, RBZ bytes,
suite and command-catalog hashes, supported runtime, passing TestUp inventory,
and 100% production-adapter line and branch coverage. It then invokes the
public install-acceptance validator against the retained RBZ, wheel, source
distribution, trusted dispatcher, and GitHub workflow run ID. Either validator
failing makes release verification fail closed.

After release mode passes, the workflow prepares and validates the versioned
RBZ, wheel, and source distribution and preserves them as a workflow artifact.
It does not create a tag, publish a GitHub release, or upload to a package
registry.

The aggregate result is `artifacts/verification/release.json`. It retains
separate `python`, `headless_ruby`, `sketchup_runtime`, and
`install_acceptance` scopes. Python and headless Ruby each carry their measured
covered/total line and branch counts plus fixed 100% thresholds; the SketchUp
scope carries the production-adapter counts. The workflow preserves both
`local.json` and `release.json` for audit but does not publish or modify a
GitHub release.
Missing, malformed, expired, wrong-SHA, wrong-run, or wrong-package evidence
fails closed and must be regenerated on the protected runner.

The repository contains validator fixtures and controlled adversarial tests,
not a licensed SketchUp acceptance result. Only the protected
`SketchUp Runtime Evidence` workflow is an accepted producer.
