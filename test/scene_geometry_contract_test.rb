require 'json'

require_relative '../su_mcp/su_mcp/command_dispatcher'
require_relative '../su_mcp/su_mcp/command_executor'
require_relative 'headless'


SCENE_GEOMETRY_CONTRACT = JSON.parse(
  File.read(File.join(__dir__, 'fixtures', 'scene_geometry_contract.json'))
)


class InMemorySceneGeometryAdapter
  attr_reader :calls

  def initialize(results:, failure: nil)
    @results = results
    @failure = failure
    @calls = []
  end

  def create_component(type:, position:, dimensions:)
    record('create_component', type: type, position: position, dimensions: dimensions)
  end

  def delete_component(id:)
    record('delete_component', id: id)
  end

  def transform_component(id:, position:, rotation:, scale:)
    record(
      'transform_component',
      id: id,
      position: position,
      rotation: rotation,
      scale: scale
    )
  end

  def get_selection
    record('get_selection')
  end

  def set_material(id:, material:)
    record('set_material', id: id, material: material)
  end

  def export_scene(format:)
    record('export_scene', format: format)
  end

  def boolean_operation(operation:, target_id:, tool_id:, delete_originals:)
    record(
      'boolean_operation',
      operation: operation,
      target_id: target_id,
      tool_id: tool_id,
      delete_originals: delete_originals
    )
  end

  def execute(name, arguments)
    record(name, **arguments.transform_keys(&:to_sym))
  end

  private

  def record(name, **arguments)
    @calls << [name, arguments]
    raise @failure if @failure

    @results.fetch(name)
  end
end


class SceneGeometryContractTest
  include HeadlessTest::Assertions

  def test_every_scene_geometry_command_returns_the_plain_result_and_catalog_resource
    results = SCENE_GEOMETRY_CONTRACT['commands'].to_h do |command|
      [command['name'], command['command_result']]
    end
    sketchup = InMemorySceneGeometryAdapter.new(results: results)
    dispatcher = dispatcher_for(sketchup)

    SCENE_GEOMETRY_CONTRACT['commands'].each do |command|
      response = dispatcher.call(request_for(command))

      assert_equal '2.0', response[:jsonrpc]
      assert_equal command['request_id'], response[:id]
      assert_equal JSON.generate(command['command_result']), response.dig(:result, :content, 0, :text)
      assert_equal false, response.dig(:result, :isError)
      assert_equal true, response.dig(:result, :success)
      assert_equal command['resource_id'], response.dig(:result, :resourceId)
      assert_equal command.key?('resource_id'), response[:result].key?(:resourceId)
    end

    assert_equal SCENE_GEOMETRY_CONTRACT['commands'].map { |command| command['name'] },
                 sketchup.calls.map(&:first)
    assert_equal({ id: 731 }, sketchup.calls.find { |call| call.first == 'delete_component' }[1])
  end

  def test_catalog_defaults_are_applied_before_the_adapter_call
    results = {
      'create_component' => { 'id' => 1 },
      'transform_component' => { 'id' => 1 },
      'export_scene' => { 'path' => '/tmp/model.skp', 'format' => 'skp' },
      'boolean_operation' => { 'id' => 3 }
    }
    sketchup = InMemorySceneGeometryAdapter.new(results: results)
    dispatcher = dispatcher_for(sketchup)

    dispatcher.call(tool_request('create_component', {}, 1))
    dispatcher.call(tool_request('transform_component', { 'id' => 1, 'position' => [0, 0, 0] }, 2))
    dispatcher.call(tool_request('export_scene', {}, 3))
    dispatcher.call(
      tool_request(
        'boolean_operation',
        { 'operation' => 'difference', 'target_id' => 1, 'tool_id' => 2 },
        4
      )
    )

    assert_equal(
      [
        ['create_component', { type: 'cube', position: [0, 0, 0], dimensions: [1, 1, 1] }],
        ['transform_component', { id: 1, position: [0, 0, 0], rotation: nil, scale: nil }],
        ['export_scene', { format: 'skp' }],
        [
          'boolean_operation',
          { operation: 'difference', target_id: 1, tool_id: 2, delete_originals: false }
        ]
      ],
      sketchup.calls
    )
  end

  def test_invalid_arguments_never_reach_the_sketchup_adapter
    sketchup = InMemorySceneGeometryAdapter.new(results: {})
    dispatcher = dispatcher_for(sketchup)

    SCENE_GEOMETRY_CONTRACT['invalid_arguments'].each_with_index do |fixture, index|
      response = dispatcher.call(
        tool_request(fixture['name'], fixture['arguments'], "invalid-#{index}")
      )

      assert_equal(-32_602, response.dig(:error, :code))
      assert_includes response.dig(:error, :message), fixture['contains']
      assert_equal "invalid-#{index}", response[:id]
    end

    response = dispatcher.call(
      tool_request('create_component', { 'position' => [0, Float::INFINITY, 0] }, 'infinite')
    )
    assert_equal(-32_602, response.dig(:error, :code))
    assert_includes response.dig(:error, :message), 'position'
    assert_equal [], sketchup.calls
  end

  def test_removed_and_unknown_commands_return_method_not_found
    sketchup = InMemorySceneGeometryAdapter.new(results: {})
    dispatcher = dispatcher_for(sketchup)

    %w[chamfer_edges fillet_edges get_selected_components missing_command].each do |name|
      response = dispatcher.call(tool_request(name, {}, "unknown-#{name}"))

      assert_equal(-32_601, response.dig(:error, :code))
      assert_equal "unknown-#{name}", response[:id]
    end
    assert_equal [], sketchup.calls
  end


  def test_legacy_direct_requests_accept_only_the_executable_export_alias
    results = {
      'export_scene' => { 'path' => '/tmp/model.skp', 'format' => 'skp' }
    }
    sketchup = InMemorySceneGeometryAdapter.new(results: results)
    dispatcher = dispatcher_for(sketchup)

    response = dispatcher.call(
      'command' => 'export',
      'parameters' => { 'format' => 'skp' },
      'id' => 'legacy-export'
    )
    migration_only = dispatcher.call(
      'command' => 'get_selected_components',
      'parameters' => {},
      'id' => 'legacy-selection'
    )

    assert_equal(
      '{"path":"/tmp/model.skp","format":"skp"}',
      response.dig(:result, :content, 0, :text)
    )
    assert_equal 'export_scene', sketchup.calls.first.first
    assert_equal(-32_601, migration_only.dig(:error, :code))
    assert_equal 'legacy-selection', migration_only[:id]
  end

  def test_adapter_failures_return_internal_error_with_the_original_request_id
    sketchup = InMemorySceneGeometryAdapter.new(
      results: { 'delete_component' => {} },
      failure: RuntimeError.new('entity disappeared')
    )
    dispatcher = dispatcher_for(sketchup)

    response = dispatcher.call(tool_request('delete_component', { 'id' => 731 }, nil))

    assert_equal(-32_603, response.dig(:error, :code))
    assert_equal 'entity disappeared', response.dig(:error, :message)
    assert_equal false, response.dig(:error, :data, :success)
    assert_equal nil, response[:id]
  end

  private

  def dispatcher_for(sketchup)
    SU_MCP::CommandDispatcher.new(
      executor: SU_MCP::CommandExecutor.new(adapter: sketchup)
    )
  end

  def request_for(command)
    tool_request(command['name'], command['arguments'], command['request_id'])
  end

  def tool_request(name, arguments, id)
    {
      'jsonrpc' => '2.0',
      'method' => 'tools/call',
      'params' => { 'name' => name, 'arguments' => arguments },
      'id' => id
    }
  end
end


HeadlessTest.run(SceneGeometryContractTest)
