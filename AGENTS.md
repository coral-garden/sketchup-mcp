## Landing work

`main` is protected. Direct pushes are rejected — `Changes must be made through a pull request`. This applies to admin tokens too, so it applies to you.

1. Branch, commit, push the branch.
2. `gh pr create --repo coral-garden/sketchup-mcp --base main --head <branch>`
3. The `Local Python and Ruby gates` check must pass before merge. No review approval is required.

This repo is a **fork** of `mhyrr/sketchup-mcp`. Always pass `--repo coral-garden/sketchup-mcp` when creating a PR: GitHub defaults a fork's PR base to the upstream repo, and the web UI will silently aim at `mhyrr`. `gh repo set-default` is already set to this fork, so other `gh` commands resolve correctly.

Note that anything pushed here is retrievable from the upstream repo by SHA — that is how GitHub fork networks work, and deleting a branch does not undo it.

## Verifying

One entry point, from the repository root:

```sh
python scripts/verify.py local     # Python + Ruby gates, both at 100% line/branch
```

Individual suites while iterating:

```sh
PYTHONPATH=src python -m unittest discover -s tests   # Python
ruby test/headless.rb                                 # Ruby, all test files
PYTHONPATH=src python -m sketchup_mcp.command_parity . # command contract parity
```

`ruby test/headless.rb` runs the whole Ruby suite. Do not run a bare `require` of that file and assume it ran anything — it is also the assertion harness, and it used to exit 0 silently when invoked with nothing to do.

## Coverage is not verification

Both runtimes sit at 100% line and branch coverage. That number measures which lines *executed*, not which behaviours are *checked*, and this repo has repeatedly shipped tests that pass no matter what the code does:

- A regression test whose assertion raised the exception it was asserting.
- A loopback test that asserted a constructor rejects an unknown keyword, not what address the client connects to.
- Geometry tests that counted faces while every dimension — radius, segment count, extrusion depth, joint taper — went unasserted. Ten of ten mutations survived.

So:

**Do not add tests to move a coverage number.** It is already 100%; a new test that only raises it is measuring nothing.

**Prove a test binds before claiming it works.** Break the code it covers, watch the test fail, restore, watch it pass. A test that has never been seen red is not evidence.

**Assert values, not shapes.** Counting calls, keys, or events passes through almost any wrong answer. Where a number is the product — coordinates, dimensions, error codes, retry counts — assert the number.

**Fakes must record what the test needs to assert.** The geometry fakes discarded face points and pushpull distances, which made dimensional assertions impossible rather than merely absent.

## Agent skills

### Issue tracker

Issues and PRDs are tracked in this repository's GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the canonical `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix` labels. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository using root-level `CONTEXT.md` and `docs/adr/`. See `docs/agents/domain.md`.
