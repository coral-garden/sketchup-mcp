# Contributing to SketchUp MCP

This is the canonical contributor workflow for the Python MCP server and the
SketchUp extension.

## Prerequisites

- Git.
- Python 3.10 or newer. The verification baseline is Python 3.10.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) 0.10.4.
- Ruby 3.2.2 for the supported headless baseline, matching the embedded Ruby in
  SketchUp 2024. The headless Ruby suites use only the standard library; there
  are no gems to install.

## Fresh clone

```sh
git clone https://github.com/coral-garden/sketchup-mcp.git
cd sketchup-mcp
uv sync --locked --python 3.10 --group test --group build
```

The committed `uv.lock` is the reviewed application, runtime, test, and build
dependency graph.

Run the repository gate through that locked environment:

```sh
uv run --locked --group test --group build python scripts/verify.py local
```

## Understand the local gate

The verifier runs the deterministic coverage gate and complete integration
suite for each headless runtime. Python and headless Ruby must each reach
exactly 100% line and branch coverage; coverage in one runtime cannot compensate
for a gap in the other. Successful output ends with these distinct statuses:

```text
Python: PASS
Headless Ruby: PASS
SketchUp runtime: EXTERNAL (not available in local verification)
Local verification: PASS
```

The exact covered/total counts appear immediately above the statuses. The same
result is saved as `artifacts/verification/local.json` so automation can inspect
the three runtime scopes independently.

The SketchUp runtime is external because it cannot run on the hosted Linux
gate. Its authoritative workflow is
[`docs/testing/sketchup-testup.md`](docs/testing/sketchup-testup.md). Producing
trusted evidence requires the protected, interactive licensed SketchUp runner
on Windows or macOS; a headless pass cannot replace that run.

## Prepare a release candidate

`VERSION` is the authoritative project version. The Python package metadata,
Python runtime, extension metadata, and RBZ filename all derive from it. Prepare
the installable artifacts only after the local gate passes:

```sh
version="$(python scripts/build.py --print-version)"
python scripts/build.py
python scripts/build.py --check "dist/sketchup-mcp-$version.rbz"
uv build --offline --no-build-isolation --out-dir dist
```

The commands produce and validate these three candidate artifacts:

- `dist/sketchup-mcp-$version.rbz`
- `dist/sketchup_mcp-$version-py3-none-any.whl`
- `dist/sketchup_mcp-$version.tar.gz`

The RBZ builder prints its version, SHA-256, and complete member count. The
Python build produces both the wheel and source distribution from the same
checkout. Automated distribution checks also build a wheel from the source
distribution without network access or build isolation, compare both archives
with the authoritative source-module inventory, install only exact locked
runtime dependencies in an isolated Python 3.10 environment, and load both
published entry points.

## Verify trusted release evidence

First complete the operator workflow in
[`docs/testing/sketchup-testup.md`](docs/testing/sketchup-testup.md) for the
exact release-candidate commit. The protected workflow supplies a trusted GitHub
workflow run ID and an artifact whose fixed contents are downloaded to
`artifacts/trusted-runtime`. Replace `RUN_ID` below with that positive numeric
workflow run ID:

```sh
uv run --locked --group test --group build python scripts/verify.py release --runtime-root artifacts/trusted-runtime --runtime-run-id RUN_ID
```

Release verification repeats the complete local gate, checks the trusted GitHub
run identity and freshness, rebuilds the RBZ, and validates the raw TestUp and
installed-package evidence. It also requires the protected workflow's second
SketchUp launch to prove the exact installed wheel and RBZ through the
production stdio MCP server: initialize, discover the full tool catalog, and
return an exact empty selection from `get_selection`. Python, headless Ruby,
and the production SketchUp adapter must each have exactly 100% line and branch
coverage. A success ends with:

```text
Python: PASS
Headless Ruby: PASS
SketchUp runtime: PASS
Install acceptance: PASS
Release verification: PASS
```

The aggregate is written to `artifacts/verification/release.json`. This workflow
only prepares and verifies files; it does not change Git history, publish a
GitHub release, or upload a Python package or RBZ to a distribution service.
No licensed-runner acceptance result is checked into this repository; only a
fresh artifact produced by the protected desktop workflow satisfies the gate.
