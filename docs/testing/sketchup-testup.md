# Production adapter verification in SketchUp

This workflow exercises the production `SketchupAdapter` and
`SketchupCommands` against an isolated real model in desktop SketchUp. The
suite has 22 fixed contract scenarios plus one dynamic run-marker test. It
covers all 11 commands, exact catalog parity, geometry and joinery effects,
undo/abort behavior, export cleanup, evaluation safety, adapter failure
branches, and final evidence reporting.

The designated runner is an interactive, licensed SketchUp Pro installation on
Windows 11 x64 or a currently supported macOS release. Use SketchUp 2024 or
newer, whose [release notes document the Ruby 3.2.2
upgrade](https://ruby.sketchup.com/file.ReleaseNotes.html), and install
[TestUp 2.5.4](https://github.com/SketchUp/testup-2/releases/tag/2.5.4). TestUp
provides the in-SketchUp Minitest lifecycle; Ruby's built-in
[`Coverage` branch mode](https://ruby-doc.org/3.2/exts/coverage/Coverage.html)
provides line and branch measurements.

For release evidence, dispatch the protected `SketchUp Runtime Evidence`
workflow described in [the verification guide](verification.md). The platform
commands below remain the authoritative manual procedure and are useful for
runner setup and diagnosis. The protected workflow does not rely on a manual
Extension Manager installation: its static `-RubyStartup` bootstrap reads a
hash-and-size-bound adjacent JSON input, uses the official
`Sketchup.install_from_archive(path, false)` API, verifies the installed
package, and loads the exact candidate before TestUp. Before installing, it
requires the active `Sketchup.find_support_file('Plugins')` directory to be the
same filesystem directory configured by the protected runner. A release also
requires that automated installation proof and the trusted GitHub run metadata.

## What the evidence proves

The operator makes a manual attestation that the machine is the designated
licensed runner and that exactly one SketchUp/TestUp process writes the prepared
workspace. This does not cryptographically prove the SketchUp license, operator
identity, or process provenance.

The workflow creates a unique `run-<256-bit-run-id>/` workspace and injects a
dynamic `test_run_id_<run-id>` test into TestUp. The generated `Tests:` list
requests all 23 exact `TC_ProductionAdapter#test_*` filters, including that
marker; it never uses the broad `TC_ProductionAdapter#` class filter. TestUp's
[2.5.4 FileReporter source](https://github.com/SketchUp/testup-2/blob/2.5.4/src/testup/file_reporter.rb)
shows that `.run` stores requested Minitest filters, not executed results. The
collector therefore checks `.run` as the exact requested inventory and checks
the verbose TestUp JSON plus human FileReporter `.log` as the exact executed
pass inventory. It also requires the marker in the suite marker and runtime
report, and checks configured paths, timestamps, deterministic package bytes,
and installed file hashes. The final SHA-256 hashes and byte sizes show whether
the retained raw files changed after collection. Together these controls
correlate the artifacts under the trusted operator's single-process
attestation; hashes alone do not prove which process created a file.

The extension bridge is loopback-only but unauthenticated. Loopback restricts
access to the local host, not to the current OS user; any local process or user
able to connect to `127.0.0.1` can invoke it. Run this workflow on a trusted,
isolated desktop account.

After this TestUp run, the protected workflow performs a distinct clean-install
acceptance run. It launches SketchUp again without reinstalling the candidate,
uses the static `testup/install_acceptance/startup.rb` harness and its adjacent
JSON input, clears the active selection, and starts the production bridge. The
official Python MCP SDK then initializes the exact installed server, discovers
all catalog tools, and calls the read-only `get_selection` tool. Acceptance
requires the raw SDK result to contain the exact empty-selection success
envelope. A fixed marker stops the bridge and quits SketchUp; no process-wide
kill command is part of the workflow.

## Prepare the exact run

Use a clean checkout of the commit being verified and build the RBZ once:

```text
python scripts/build.py --output-dir dist
```

Then follow exactly one platform procedure below. Each procedure invokes
`prepare` once. It independently rebuilds the RBZ and requires byte-for-byte
equality, creates the unique run workspace, records both manual attestations,
and writes a concrete `testup-ci.generated.yml` with absolute paths, a
run-derived seed, `Verbose: true`, and the exact 23-test requested filter
inventory. The command prints only the new `run-context.json` path so the
examples can capture it safely.

Install the built RBZ through SketchUp's Extension Manager and leave `SketchUp
MCP` enabled. Install TestUp 2.5.4 from its official release. Quit every
SketchUp process so the next launch inherits the coverage environment before
the extension loads.

Do not edit tracked files, rebuild, replace the RBZ, switch commits, or allow a
second process to write the run workspace between `prepare`, the SketchUp
launch, `collect`, and `validate`.

## Run on Windows 11

From PowerShell at the repository root, adjust the SketchUp executable for the
installed supported version:

```powershell
$repo = (Resolve-Path .).Path
$rbz = (Resolve-Path "dist/sketchup-mcp-$((Get-Content VERSION).Trim()).rbz").Path
$contextPath = python scripts/sketchup_runtime_evidence.py prepare `
  --artifact-dir artifacts/sketchup-testup --rbz $rbz `
  --operator "YOUR NAME" `
  --attest-licensed-runner --attest-single-testup-process
$contextPath = (Resolve-Path $contextPath).Path
$artifactDir = Split-Path $contextPath
$context = Get-Content $contextPath -Raw | ConvertFrom-Json
$commit = (git rev-parse HEAD).Trim()

$env:SKETCHUP_MCP_TESTUP_COVERAGE = "1"
$env:SKETCHUP_MCP_TESTUP_COMMIT = $commit
$env:SKETCHUP_MCP_TESTUP_RUN_ID = $context.run_id
$env:SKETCHUP_MCP_TESTUP_OS_VERSION = (Get-CimInstance Win32_OperatingSystem).Version
$env:SKETCHUP_MCP_TESTUP_RUNTIME_REPORT = Join-Path $artifactDir "runtime-report.json"
$env:SKETCHUP_MCP_TESTUP_SUITE_MARKER = Join-Path $artifactDir "suite-marker.json"

$arguments = @(
  "-RubyStartupArg",
  "`"TestUp:CI:Config: $artifactDir\testup-ci.generated.yml`""
)
$process = Start-Process `
  -FilePath "C:\Program Files\SketchUp\SketchUp 2024\SketchUp.exe" `
  -ArgumentList $arguments -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "SketchUp/TestUp exited unsuccessfully" }

$entries = @(Get-ChildItem (Join-Path $artifactDir "logs"))
$regularFiles = @($entries | Where-Object {
  -not $_.PSIsContainer -and
  -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
})
$logs = @($regularFiles | Where-Object { $_.Extension -eq ".log" })
$replays = @($regularFiles | Where-Object { $_.Extension -eq ".run" })
$unexpected = @($entries | Where-Object {
  $_.PSIsContainer -or
  ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
  $_.Extension -notin @(".log", ".run")
})
if ($logs.Count -ne 1) { throw "Expected exactly one regular TestUp .log" }
if ($replays.Count -ne 1) { throw "Expected exactly one regular TestUp .run" }
if ($unexpected.Count -ne 0) { throw "Unexpected TestUp FileReporter artifact role" }

$raw = @(
  "--run-context", $contextPath,
  "--testup-config", (Join-Path $artifactDir "testup-ci.generated.yml"),
  "--testup-results", (Join-Path $artifactDir "testup-results.json"),
  "--testup-log", $logs[0].FullName,
  "--testup-replay", $replays[0].FullName,
  "--error-log", (Join-Path $artifactDir "testup-error.log"),
  "--runtime-report", (Join-Path $artifactDir "runtime-report.json"),
  "--suite-marker", (Join-Path $artifactDir "suite-marker.json")
)
python scripts/sketchup_runtime_evidence.py collect @raw `
  --rbz $rbz --commit $commit --output (Join-Path $artifactDir "evidence.json")
python scripts/sketchup_runtime_evidence.py validate @raw `
  --evidence (Join-Path $artifactDir "evidence.json") --rbz $rbz --commit $commit
```

`Start-Process -Wait` is required: artifact inspection starts only after the
SketchUp/TestUp GUI process has exited.

## Run on macOS

Run SketchUp from the same terminal so it inherits the instrumentation
environment. Adjust the application path for the installed supported version:

```bash
repo="$(pwd -P)"
rbz="$repo/dist/sketchup-mcp-$(tr -d '\r\n' < VERSION).rbz"
context="$(python scripts/sketchup_runtime_evidence.py prepare \
  --artifact-dir artifacts/sketchup-testup --rbz "$rbz" \
  --operator "YOUR NAME" \
  --attest-licensed-runner --attest-single-testup-process)"
artifact_dir="$(dirname "$context")"
commit="$(git rev-parse HEAD)"
run_id="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["run_id"])' "$context")"

SKETCHUP_MCP_TESTUP_COVERAGE=1 \
SKETCHUP_MCP_TESTUP_COMMIT="$commit" \
SKETCHUP_MCP_TESTUP_RUN_ID="$run_id" \
SKETCHUP_MCP_TESTUP_OS_VERSION="$(sw_vers -productVersion)" \
SKETCHUP_MCP_TESTUP_RUNTIME_REPORT="$artifact_dir/runtime-report.json" \
SKETCHUP_MCP_TESTUP_SUITE_MARKER="$artifact_dir/suite-marker.json" \
  '/Applications/SketchUp 2024/SketchUp.app/Contents/MacOS/sketchup' \
  -RubyStartupArg "TestUp:CI:Config: $artifact_dir/testup-ci.generated.yml"

# The foreground SketchUp executable has exited before artifact inspection.
entry_count="$(find "$artifact_dir/logs" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')"
log_count="$(find "$artifact_dir/logs" -mindepth 1 -maxdepth 1 -type f -name '*.log' | wc -l | tr -d ' ')"
replay_count="$(find "$artifact_dir/logs" -mindepth 1 -maxdepth 1 -type f -name '*.run' | wc -l | tr -d ' ')"
[[ "$log_count" = 1 ]] || { echo "Expected exactly one regular TestUp .log" >&2; exit 1; }
[[ "$replay_count" = 1 ]] || { echo "Expected exactly one regular TestUp .run" >&2; exit 1; }
[[ "$entry_count" = 2 ]] || { echo "Unexpected TestUp FileReporter artifact role" >&2; exit 1; }
testup_log="$(find "$artifact_dir/logs" -mindepth 1 -maxdepth 1 -type f -name '*.log' -print -quit)"
testup_replay="$(find "$artifact_dir/logs" -mindepth 1 -maxdepth 1 -type f -name '*.run' -print -quit)"

raw=(
  --run-context "$context"
  --testup-config "$artifact_dir/testup-ci.generated.yml"
  --testup-results "$artifact_dir/testup-results.json"
  --testup-log "$testup_log"
  --testup-replay "$testup_replay"
  --error-log "$artifact_dir/testup-error.log"
  --runtime-report "$artifact_dir/runtime-report.json"
  --suite-marker "$artifact_dir/suite-marker.json"
)
python scripts/sketchup_runtime_evidence.py collect "${raw[@]}" \
  --rbz "$rbz" --commit "$commit" --output "$artifact_dir/evidence.json"
python scripts/sketchup_runtime_evidence.py validate "${raw[@]}" \
  --evidence "$artifact_dir/evidence.json" --rbz "$rbz" --commit "$commit"
```

If a FileReporter filename contains a newline, set `testup_log` or
`testup_replay` manually to the corresponding single regular file; do not make
a transformed copy.

## Acceptance and retained artifacts

Collection fails unless all 23 exact verbose test names pass—the 22 fixed
contract scenarios, including the alphabetically final
`test_zz_write_runtime_report`, plus the dynamic run marker—with no failures,
errors, or skips. The final fixed test refuses to write until every other fixed
scenario has completed. The separately
scoped `su_mcp/sketchup_adapter.rb` report must have exactly 100% line and branch
coverage.

Validation rejects a mismatched dynamic marker or seed, lifecycle ordering,
stale file timestamp, nonempty TestUp error log, missing/duplicate/unexpected
FileReporter role, broad/missing/extra requested replay filter, missing or extra
installed extension file, changed raw artifact, unsupported runtime, catalog or
suite drift, non-reproducible RBZ, different commit or version, and any package
whose bytes differ from a deterministic rebuild of the clean checkout. Final
evidence contains filenames, byte sizes, and SHA-256 hashes, never absolute
runner paths or raw exception text.

Preserve `run-context.json`, the generated config, verbose TestUp JSON, the raw
FileReporter `.log` and replay `.run`, empty TestUp error log, suite marker,
runtime report, final evidence, tested RBZ, wheel, source distribution, and the
complete `install-acceptance/` directory together for a release audit. The
replay and raw install-acceptance files are evidence and must not be discarded.

Linux/headless checks validate suite structure, safe cleanup, catalog parity,
evidence schema, package binding, exact mismatch rejection, and fail-closed
behavior. A Linux/headless pass does not satisfy issue #15: acceptance requires
`validate` to pass with artifacts produced by the designated licensed
Windows/macOS SketchUp runner. The same is true of clean-install acceptance.
This repository does not contain either licensed runtime artifact; the
protected workflow produces both on the trusted desktop runner.
