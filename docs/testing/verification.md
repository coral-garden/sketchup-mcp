# Verification, builds, and manual acceptance

The canonical setup and commands live in the
[contributor workflow](../../CONTRIBUTING.md).

## Automated boundary

The repository has one verification entry point:

```text
uv run --locked --group test --group build python scripts/verify.py local
```

It runs Python and headless Ruby coverage plus their complete integration
suites. Each headless runtime must independently reach exactly 100% line and branch coverage.
Missing tools, malformed reports, uncovered code, or any test failure make the
command fail.

GitHub's `Verification` workflow runs that command on an unprivileged Ubuntu
runner for every pull request and push to `main`. Pull-request code never runs
inside desktop SketchUp.

## Main build artifacts

Only a successful push to `main` starts the dependent build job. It builds and
validates these files from the verified commit:

- the SketchUp extension package (`.rbz`);
- the Python MCP server wheel (`.whl`);
- the Python MCP server source distribution (`.tar.gz`);
- `SHA256SUMS` covering those three versioned files.

The workflow uploads them as `main-build-<full-commit-sha>` and retains the
artifact for 14 days. The artifact name binds the download to the exact source
commit; the checksum manifest lets the operator verify the extracted bytes.

Building does not publish a release and does not imply SketchUp compatibility.

## Manual SketchUp acceptance

Desktop SketchUp remains a manual boundary because it needs an interactive,
licensed Windows or macOS application. It is not a required CI scope and does
not require a GitHub runner service.

The operator downloads one exact main build, verifies `SHA256SUMS`, installs the
RBZ and wheel, runs the 21-test production-adapter TestUp suite, and proves the
full MCP path with `get_selection`. The complete checklist is in
[the manual SketchUp guide](sketchup-testup.md).

Record the commit SHA, project version, artifact checksums, SketchUp version,
TestUp result, and MCP smoke result with the release decision. This is a human
acceptance record, not machine-produced provenance.
