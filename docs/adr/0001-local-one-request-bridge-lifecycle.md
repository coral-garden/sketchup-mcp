# ADR 0001: Local one-request bridge lifecycle

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

The Python MCP server calls the Ruby SketchUp extension over a private TCP bridge.
The Python implementation treated that connection as persistent and sent an
unanswered liveness ping before later calls. The Ruby implementation answered one
newline-delimited request and then closed the connection. A second tool call could
therefore write to a socket whose peer had already closed it.

The extension also exposes `eval_ruby`, which deliberately evaluates arbitrary Ruby
inside SketchUp. The listener has no authentication, so its network exposure is a
security decision as well as a transport detail.

## Decision

The bridge uses **one request per connection**:

1. The Python bridge client opens a new IPv4 loopback TCP connection.
2. It writes exactly one UTF-8 JSON-RPC 2.0 request followed by `\n`.
3. The Ruby bridge listener reads exactly one newline-framed request, writes exactly
   one newline-framed response, and closes the accepted connection.
4. The Python bridge client reads through the response newline, validates the JSON
   object and request ID, returns a result or maps the error, then closes its side.

Opening a connection and completing the exchange is the liveness check. There is no
separate ping and no socket survives between tool calls.

The client makes three attempts by default. Connection failures, premature EOF, and
timeouts are transport failures and may be retried with the same request ID. A
well-formed remote JSON-RPC error or malformed response is final and is not retried.
Remote errors retain their code, message, data, and request ID. Response IDs must
equal request IDs.

The Ruby extension owns and binds the listening port. Both runtimes read that port
from `SKETCHUP_MCP_BRIDGE_PORT`, defaulting to `9876`. The host is not configurable:
the Ruby listener binds only `127.0.0.1`, and the Python client connects only to
`127.0.0.1`. If the port is occupied, the Ruby listener fails startup with an
explicit port-in-use error. A Python call to an unavailable listener fails after
its bounded retries, and the calling MCP tool reports `SketchUp bridge unavailable
at 127.0.0.1:<port> after <attempts> attempts`. If another local process owns the
port, its observable behavior maps to the applicable timeout, protocol, or remote
error.

The accepted trust boundary is the local operating-system user. The bridge has no
application authentication, and any local process running as that user can invoke
commands, including `eval_ruby`. The listener must never bind a non-loopback address.
Widening access beyond loopback requires a new security decision and authentication
design.

## Consequences

- Ruby's UI timer never owns a long-lived client connection.
- Every tool call pays one local TCP handshake in exchange for deterministic
  lifecycle and reconnect behavior.
- Requests have at-least-once *attempt* semantics, not exactly-once execution.
  After a timeout or EOF, the Ruby side may have executed a command even though the
  Python side did not receive its response; a retry can therefore execute it again.
  Commands are not assumed idempotent. The stable request ID is retained so a future
  command contract can add deduplication without changing this lifecycle. Callers
  must not interpret a transport failure as proof that the command did not run.
- Port changes must use the same environment variable in the Python MCP server and
  the SketchUp process.
- Loopback binding is a required security invariant and is covered by an executable
  listener test.
