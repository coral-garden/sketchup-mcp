require 'json'
require_relative '../../su_mcp/bridge_listener'


request_count = 0
listener = SU_MCP::BridgeListener.new(port: 0, handler: ->(request) {
  request_count += 1
  {
    jsonrpc: '2.0',
    result: { request: request_count, request_id: request['id'] },
    id: request['id']
  }
})

begin
  listener.start
  STDOUT.write(JSON.generate(port: listener.port) + "\n")
  STDOUT.flush
  deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 2
  until request_count == 2
    if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
      raise 'Bridge fixture did not receive both requests before the deadline'
    end

    listener.poll(timeout: 0.01)
    listener.drain
  end
ensure
  listener.stop
end
