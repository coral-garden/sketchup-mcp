# Ruby test suites

Use the single repository setup and verification path in the
[contributor workflow](../CONTRIBUTING.md). This page explains how the Ruby
tests are divided; it does not define a second command path.

The gate starts Ruby's standard-library coverage collector before loading the
production source and runs all selected tests in one process. It exits non-zero
unless both line and branch coverage are exactly 100%.

## Headless production source

The executable classification in `scripts/ruby_coverage.rb` includes:

- `bridge_listener.rb` and `bridge_protocol.rb`: controlled Bridge framing,
  parsing, errors, and one-request connection lifecycle behavior;
- `bridge_runtime.rb`: Bridge lifecycle coordination through controlled Bridge
  listener and scheduler adapters;
- `command_catalog.rb`, `command_dispatcher.rb`, `command_execution_error.rb`,
  `command_executor.rb`, `command_response_builder.rb`, and `eval_result.rb`:
  the Command catalog, Command dispatcher, Public command execution, and
  Command result behavior;
- `sketchup_adapter.rb`: every Public command path through controlled model and
  command adapters;
- `version.rb`: Project version selection.

The coverage process does not load SketchUp or its UI and does not bind a TCP
port. The real-TCP methods in `bridge_listener_test.rb` are explicitly skipped
by this gate; controlled-transport methods from the same test remain included.

## Runtime-bound production source

`socket_transport.rb` owns real TCP. `extension_menu.rb`,
`extension_runtime.rb`, `main.rb`, and `sketchup_commands.rb` are SketchUp-owned
adapters or composition roots whose final verification belongs to the SketchUp
runtime suite. Every Ruby runtime source file must appear in exactly one of the
two executable lists; an unclassified file makes the coverage command fail.

The local verifier also runs the broader multi-process Ruby suite, including
real loopback integration, after the deterministic coverage gate.
