require 'json'

require_relative '../su_mcp/su_mcp/command_dispatcher'
require_relative '../su_mcp/su_mcp/command_executor'
require_relative '../su_mcp/su_mcp/sketchup_adapter'
require_relative 'headless'


class ControlledSketchupAdapter
  attr_reader :created_components, :executed_commands

  def initialize(created_id: 731, failure: nil)
    @created_id = created_id
    @failure = failure
    @created_components = []
    @executed_commands = []
  end

  def create_component(type:, position:, dimensions:)
    @created_components << {
      type: type,
      position: position,
      dimensions: dimensions
    }
    raise @failure if @failure

    { id: @created_id }
  end

  def execute(name, arguments)
    @executed_commands << [name, arguments]
    {}
  end
end


class ControlledSketchupCommands
  def command?(name)
    name == 'get_selection'
  end

  def call(_name, _arguments)
    { success: true, entities: [{ id: 61, type: 'group' }] }
  end

  def list_resources
    []
  end
end


class CommandDispatcherTest
  include HeadlessTest::Assertions

  def test_create_component_succeeds_through_the_controlled_sketchup_seam
    sketchup = ControlledSketchupAdapter.new(created_id: 731)
    executor = SU_MCP::CommandExecutor.new(sketchup: sketchup)
    dispatcher = SU_MCP::CommandDispatcher.new(executor: executor)

    response = dispatcher.call(
      'jsonrpc' => '2.0',
      'method' => 'tools/call',
      'params' => {
        'name' => 'create_component',
        'arguments' => {
          'type' => 'cube',
          'position' => [1, 2, 3],
          'dimensions' => [4, 5, 6]
        }
      },
      'id' => 'mcp-create-17'
    )

    assert_equal(
      {
        jsonrpc: '2.0',
        result: {
          content: [{ type: 'text', text: '{"id":731}' }],
          isError: false,
          success: true,
          resourceId: 731
        },
        id: 'mcp-create-17'
      },
      response
    )
    assert_equal(
      [{ type: 'cube', position: [1, 2, 3], dimensions: [4, 5, 6] }],
      sketchup.created_components
    )
  end

  def test_create_component_uses_the_catalog_defaults
    sketchup = ControlledSketchupAdapter.new(created_id: 321)
    dispatcher = SU_MCP::CommandDispatcher.new(
      executor: SU_MCP::CommandExecutor.new(sketchup: sketchup)
    )

    dispatcher.call(
      'jsonrpc' => '2.0',
      'method' => 'tools/call',
      'params' => { 'name' => 'create_component', 'arguments' => {} },
      'id' => 41
    )

    assert_equal(
      [{ type: 'cube', position: [0, 0, 0], dimensions: [1, 1, 1] }],
      sketchup.created_components
    )
  end

  def test_numeric_request_id_is_preserved
    assert_request_id_preserved(42)
  end

  def test_string_request_id_is_preserved
    assert_request_id_preserved('request-42')
  end

  def test_null_request_id_is_preserved
    assert_request_id_preserved(nil)
  end

  def test_invalid_create_component_arguments_return_invalid_params_without_mutating_sketchup
    sketchup = ControlledSketchupAdapter.new
    executor = SU_MCP::CommandExecutor.new(sketchup: sketchup)
    dispatcher = SU_MCP::CommandDispatcher.new(executor: executor)

    response = dispatcher.call(
      'jsonrpc' => '2.0',
      'method' => 'tools/call',
      'params' => {
        'name' => 'create_component',
        'arguments' => { 'type' => 'pyramid' }
      },
      'id' => 52
    )

    assert_equal '2.0', response[:jsonrpc]
    assert_equal(-32_602, response.dig(:error, :code))
    assert_includes response.dig(:error, :message), 'type'
    assert_equal false, response.dig(:error, :data, :success)
    assert_equal 52, response[:id]
    assert_equal [], sketchup.created_components
  end

  def test_sketchup_failure_returns_execution_error_with_the_original_request_id
    sketchup = ControlledSketchupAdapter.new(
      failure: RuntimeError.new('active model is unavailable')
    )
    executor = SU_MCP::CommandExecutor.new(sketchup: sketchup)
    dispatcher = SU_MCP::CommandDispatcher.new(executor: executor)

    response = dispatcher.call(
      'jsonrpc' => '2.0',
      'method' => 'tools/call',
      'params' => { 'name' => 'create_component', 'arguments' => {} },
      'id' => 'failed-create-53'
    )

    assert_equal '2.0', response[:jsonrpc]
    assert_equal(-32_603, response.dig(:error, :code))
    assert_equal 'active model is unavailable', response.dig(:error, :message)
    assert_equal false, response.dig(:error, :data, :success)
    assert_equal 'failed-create-53', response[:id]
  end

  def test_malformed_vector_returns_invalid_params_without_mutating_sketchup
    sketchup = ControlledSketchupAdapter.new
    executor = SU_MCP::CommandExecutor.new(sketchup: sketchup)
    dispatcher = SU_MCP::CommandDispatcher.new(executor: executor)

    response = dispatcher.call(
      'jsonrpc' => '2.0',
      'method' => 'tools/call',
      'params' => {
        'name' => 'create_component',
        'arguments' => { 'dimensions' => [1, 'wide', 3] }
      },
      'id' => 55
    )

    assert_equal(-32_602, response.dig(:error, :code))
    assert_includes response.dig(:error, :message), 'dimensions'
    assert_equal [], sketchup.created_components
  end

  def test_non_object_arguments_are_rejected_before_any_command_execution
    sketchup = ControlledSketchupAdapter.new
    dispatcher = SU_MCP::CommandDispatcher.new(
      executor: SU_MCP::CommandExecutor.new(sketchup: sketchup)
    )

    response = dispatcher.call(
      'jsonrpc' => '2.0',
      'method' => 'tools/call',
      'params' => { 'name' => 'get_selection', 'arguments' => [] },
      'id' => 57
    )

    assert_equal(-32_602, response.dig(:error, :code))
    assert_equal false, response.dig(:error, :data, :success)
    assert_equal [], sketchup.executed_commands
  end

  def test_existing_argumentless_commands_remain_callable_through_the_adapter
    sketchup = SU_MCP::SketchupAdapter.new(commands: ControlledSketchupCommands.new)
    dispatcher = SU_MCP::CommandDispatcher.new(
      executor: SU_MCP::CommandExecutor.new(sketchup: sketchup)
    )

    response = dispatcher.call(
      'jsonrpc' => '2.0',
      'method' => 'tools/call',
      'params' => { 'name' => 'get_selection', 'arguments' => {} },
      'id' => 62
    )

    assert_equal(
      '{"entities":[{"id":61,"type":"group"}]}',
      response.dig(:result, :content, 0, :text)
    )
    assert_equal 62, response[:id]
  end

  private

  def assert_request_id_preserved(request_id)
    sketchup = ControlledSketchupAdapter.new(created_id: 321)
    dispatcher = SU_MCP::CommandDispatcher.new(
      executor: SU_MCP::CommandExecutor.new(sketchup: sketchup)
    )
    response = dispatcher.call(
      'jsonrpc' => '2.0',
      'method' => 'tools/call',
      'params' => { 'name' => 'create_component', 'arguments' => {} },
      'id' => request_id
    )

    assert_equal request_id, response[:id]
  end

end


HeadlessTest.run(CommandDispatcherTest)
