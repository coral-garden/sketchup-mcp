# Contributing to SketchUp MCP

This is the canonical contributor workflow for the Python MCP server and the
SketchUp extension.

## Prerequisites

- Git.
- Python 3.10 or newer. The verification baseline is Python 3.10.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) 0.10.4.
- Ruby 3.2.2 for the supported headless baseline, matching the embedded Ruby in
  SketchUp 2024. The headless Ruby suites use only the standard library.

## Fresh clone and verification

```sh
git clone https://github.com/coral-garden/sketchup-mcp.git
cd sketchup-mcp
uv sync --locked --python 3.10 --group test --group build
```

The committed `uv.lock` is the reviewed application, runtime, test, and build
dependency graph. Run the repository gate through that locked environment:

```sh
uv run --locked --group test --group build python scripts/verify.py local
```

The verifier runs the deterministic coverage gate and complete integration
suite for each headless runtime. Python and headless Ruby must each reach
exactly 100% line and branch coverage; coverage in one runtime cannot compensate
for a gap in the other. Successful output ends with:

```text
Python: PASS
Headless Ruby: PASS
SketchUp runtime: MANUAL (run the desktop acceptance checklist)
Local verification: PASS
```

The exact counts are written to `artifacts/verification/local.json`. Desktop
SketchUp is deliberately outside this gate; follow the
[manual SketchUp checklist](docs/testing/sketchup-testup.md) when accepting an
installable build.

## Reproduce the installable artifacts locally

`VERSION` is the authoritative project version. The Python package metadata,
MCP server, extension metadata, and RBZ filename all derive from it.

```sh
version="$(python scripts/build.py --print-version)"
python scripts/build.py --output-dir dist
python scripts/build.py --check "dist/sketchup-mcp-$version.rbz"
uv build --offline --no-build-isolation --out-dir dist
uv run --locked --group test --group build python scripts/check_python_distribution.py --dist-dir dist
```

These commands produce and validate:

- `dist/sketchup-mcp-$version.rbz`
- `dist/sketchup_mcp-$version-py3-none-any.whl`
- `dist/sketchup_mcp-$version.tar.gz`

The RBZ builder is deterministic and prints its SHA-256. The distribution check
builds a wheel from the source distribution, compares the published module
inventory, installs the exact locked runtime dependencies in isolation, and
loads both MCP server entry points.

## What happens after merge

The `Verification` workflow runs the same headless gate for pull requests and
pushes to `main`. After a `main` push passes, its dependent build job creates the
three versioned files above, writes `SHA256SUMS`, and uploads one Actions artifact
named `main-build-<full-commit-sha>` for 14 days.

That artifact is the handoff to a human. Download it, install the exact RBZ and
wheel, and complete the [manual acceptance checklist](docs/testing/sketchup-testup.md).
The build does not create a tag, publish a GitHub release, upload to a package
registry, or claim that desktop SketchUp was exercised automatically.
