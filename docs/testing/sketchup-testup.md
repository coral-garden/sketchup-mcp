# Manual SketchUp acceptance

Use this checklist for an exact `main-build-<full-commit-sha>` artifact after the
hosted `Verification` workflow succeeds. This manual acceptance run does not
need a self-hosted GitHub runner.

## Prepare the exact build

1. Download the build artifact from the successful `Verification` run.
2. Extract it without renaming any files.
3. Verify all three package hashes against `SHA256SUMS`.
4. Check out the same full commit SHA locally. The checkout supplies the TestUp
   suite; the installed RBZ and wheel must come from the downloaded artifact.

The build contains one RBZ for the SketchUp extension and one wheel plus one
source distribution for the Python MCP server.

## Install the extension and TestUp

1. Use a supported SketchUp Desktop release on Windows or macOS.
2. Install [TestUp 2.5.4](https://github.com/SketchUp/testup-2/releases/tag/2.5.4).
3. In **Extensions > Extension Manager**, install the downloaded RBZ.
4. Confirm **SketchUp MCP** is enabled and displays the project version from
   `VERSION`, then completely restart SketchUp.

## Run the production adapter suite

1. Open **Extensions > TestUp**.
2. Open TestUp preferences with the gear button.
3. Add the exact checkout directory `testup/production_adapter` as a test path.
4. Select `TC_ProductionAdapter` and run the whole class once.

The expected result is **21 tests**, all passing, with zero failures, errors, or
skips. The suite starts each scenario from an empty model, exercises every
public command through the production SketchUp adapter, and cleans its temporary
export directory. Close any additional SketchUp or TestUp process before the
run so a human can tell which window produced the result.

## Prove the complete MCP path

1. Create a fresh Python environment and install the downloaded wheel.
2. Start **Extensions > SketchUp MCP > Start Bridge**.
3. Configure the MCP host to launch the wheel-installed `sketchup-mcp` entry
   point, then restart the host.
4. Clear the active SketchUp selection.
5. Invoke `get_selection` once through the MCP host.

The call must succeed and its command result must be exactly
`{"entities":[]}`. This proves the MCP host reached the installed Python server,
the bridge reached the installed extension, and the production adapter read the
live SketchUp model.

## Record the result

Record the full commit SHA, project version, three SHA-256 values, operating
system, SketchUp version, TestUp version, 21-test result, and `get_selection`
result. A failure blocks the human release decision but does not retroactively
change the hosted headless verification result.
