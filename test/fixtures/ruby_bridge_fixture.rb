require 'json'
require_relative '../../su_mcp/su_mcp/bridge_listener'


request_count = 0
listener = SU_MCP::BridgeListener.new(port: 0, handler: ->(request) {
  request_count += 1
  {
    jsonrpc: '2.0',
    result: { request: request_count },
    id: request['id']
  }
})

begin
  listener.start
  STDOUT.write(JSON.generate(port: listener.port) + "\n")
  STDOUT.flush
  until request_count == 2
    listener.poll(timeout: 0.01)
    listener.drain
  end
ensure
  listener.stop
end
