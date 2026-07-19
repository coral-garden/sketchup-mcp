require 'json'

require_relative '../su_mcp/su_mcp/command_catalog'
require_relative '../su_mcp/su_mcp/command_dispatcher'
require_relative '../su_mcp/su_mcp/command_executor'
require_relative 'headless'


class CompleteContractAdapter
  attr_reader :calls

  def initialize(results)
    @results = results
    @calls = []
    results.each_key do |name|
      define_singleton_method(name) do |**arguments|
        @calls << [name, arguments]
        @results.fetch(name)
      end
    end
  end
end


class CompleteCommandContractTest
  include HeadlessTest::Assertions

  def test_all_eleven_catalog_commands_execute_through_one_dispatch_contract
    commands = fixture_commands
    catalog = SU_MCP::CommandCatalog.new
    results = commands.to_h { |command| [command.fetch('name'), command.fetch('command_result')] }
    adapter = CompleteContractAdapter.new(results)
    dispatcher = SU_MCP::CommandDispatcher.new(
      executor: SU_MCP::CommandExecutor.new(sketchup: adapter, catalog: catalog),
      catalog: catalog
    )

    assert_equal catalog.names, commands.map { |command| command.fetch('name') }
    commands.each do |command|
      response = dispatcher.call(
        'jsonrpc' => '2.0',
        'method' => 'tools/call',
        'params' => {
          'name' => command.fetch('name'),
          'arguments' => command.fetch('arguments')
        },
        'id' => command.fetch('request_id')
      )

      assert_equal command.fetch('request_id'), response[:id]
      assert_equal JSON.generate(command.fetch('command_result')),
                   response.dig(:result, :content, 0, :text)
    end
    assert_equal catalog.names, adapter.calls.map(&:first)
  end

  private

  def fixture_commands
    %w[scene_geometry_contract.json joinery_eval_contract.json].flat_map do |filename|
      JSON.parse(File.read(File.join(__dir__, 'fixtures', filename))).fetch('commands')
    end
  end
end


HeadlessTest.run(CompleteCommandContractTest)
