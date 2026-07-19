require 'json'

require_relative '../../su_mcp/su_mcp/bridge_listener'
require_relative '../../su_mcp/su_mcp/command_catalog'
require_relative '../../su_mcp/su_mcp/command_dispatcher'
require_relative '../../su_mcp/su_mcp/command_executor'


class ContractAdapter
  def initialize(results)
    @results = results
    results.each_key do |name|
      define_singleton_method(name) { |**_arguments| @results.fetch(name) }
    end
  end
end


commands = %w[scene_geometry_contract.json joinery_eval_contract.json].flat_map do |filename|
  JSON.parse(File.read(File.join(__dir__, filename))).fetch('commands')
end
results = commands.to_h do |command|
  [command.fetch('name'), command.fetch('command_result')]
end
catalog = SU_MCP::CommandCatalog.new
executor = SU_MCP::CommandExecutor.new(
  sketchup: ContractAdapter.new(results),
  catalog: catalog
)
dispatcher = SU_MCP::CommandDispatcher.new(executor: executor, catalog: catalog)
request_count = 0
listener = SU_MCP::BridgeListener.new(
  port: 0,
  handler: lambda do |request|
    request_count += 1
    dispatcher.call(request)
  end
)

begin
  listener.start
  STDOUT.write(JSON.generate(port: listener.port) + "\n")
  STDOUT.flush
  until request_count == commands.length
    listener.poll(timeout: 0.01)
    listener.drain
  end
  raise 'Python driver did not confirm every response' unless STDIN.gets == "done\n"
ensure
  listener.stop
end
