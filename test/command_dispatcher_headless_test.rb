require_relative '../su_mcp/command_dispatcher'
require_relative 'headless'


class UnreachedCommandExecutor
  def call(_name, _arguments)
    raise 'Public command execution should not be reached'
  end
end


class CommandDispatcherHeadlessTest
  include HeadlessTest::Assertions

  def test_bridge_rejects_invalid_requests_before_public_command_execution
    dispatcher = SU_MCP::CommandDispatcher.new(executor: UnreachedCommandExecutor.new)

    non_object = dispatcher.call([])
    wrong_version = dispatcher.call('jsonrpc' => '1.0', 'id' => 12)
    missing_params = dispatcher.call(
      'jsonrpc' => '2.0', 'method' => 'tools/call', 'id' => 13
    )
    invalid_name = dispatcher.call(
      'jsonrpc' => '2.0', 'method' => 'tools/call',
      'params' => { 'name' => 14 }, 'id' => 14
    )

    assert_equal(-32_600, non_object.dig(:error, :code))
    assert_equal nil, non_object[:id]
    assert_equal(-32_600, wrong_version.dig(:error, :code))
    assert_equal 12, wrong_version[:id]
    assert_equal 'params must be an object', missing_params.dig(:error, :message)
    assert_equal 'name must be a string', invalid_name.dig(:error, :message)
  end

  def test_bridge_rejects_methods_outside_public_command_dispatch
    dispatcher = SU_MCP::CommandDispatcher.new(executor: UnreachedCommandExecutor.new)

    resources = dispatcher.call(
      'jsonrpc' => '2.0', 'method' => 'resources/list', 'id' => 'resources'
    )
    prompts = dispatcher.call(
      'jsonrpc' => '2.0', 'method' => 'prompts/list', 'id' => 'prompts'
    )
    unknown = dispatcher.call(
      'jsonrpc' => '2.0', 'method' => 'unknown/list', 'id' => 'unknown'
    )

    assert_equal(-32_601, resources.dig(:error, :code))
    assert_equal 'resources', resources[:id]
    assert_equal(-32_601, prompts.dig(:error, :code))
    assert_equal 'prompts', prompts[:id]
    assert_equal(-32_601, unknown.dig(:error, :code))
    assert_equal 'unknown', unknown[:id]
  end
end


HeadlessTest.run(CommandDispatcherHeadlessTest)
