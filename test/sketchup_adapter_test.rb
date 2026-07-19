require_relative '../su_mcp/command_executor'
require_relative '../su_mcp/sketchup_adapter'
require_relative 'headless'


class AdapterTestEntity
  def initialize(solid: true)
    @solid = solid
  end

  def manifold?
    @solid
  end

  def copy; end
  def union(_other); end
  def subtract(_other); end
  def intersect(_other); end
end


class AdapterTestModel
  attr_reader :trace, :materials

  def initialize(trace:, entities: { 1 => AdapterTestEntity.new, 2 => AdapterTestEntity.new },
                 start_failure: nil)
    @trace = trace
    @entities = entities
    @start_failure = start_failure
    @materials = { 'Existing Wood' => Object.new }
  end

  def find_entity_by_id(id)
    @trace << [:find, id]
    @entities[id]
  end

  def start_operation(name, disable_ui)
    @trace << [:start, name, disable_ui]
    raise @start_failure if @start_failure
  end

  def commit_operation
    @trace << [:commit]
  end

  def abort_operation
    @trace << [:abort]
  end
end


class AdapterTestCommands
  def initialize(trace:, failure: nil)
    @trace = trace
    @failure = failure
  end

  def call(name, arguments, solid_method: nil)
    arguments = arguments.merge('_solid_method' => solid_method) if solid_method
    @trace << [:command, name, arguments]
    raise @failure if @failure

    case name
    when 'create_component', 'transform_component', 'set_material', 'boolean_operation'
      { success: true, id: 41 }
    when 'get_selection'
      { success: true, entities: [] }
    when 'export_scene'
      { success: true, path: '/tmp/model.skp', format: 'skp' }
    else
      { success: true }
    end
  end

  def command?(_name)
    true
  end
end


class SketchupAdapterTest
  include HeadlessTest::Assertions

  def test_each_model_mutation_uses_exactly_one_successful_operation
    mutation_calls.each do |name, invocation|
      trace = []
      adapter = adapter_for(trace: trace)

      invocation.call(adapter)

      starts = trace.select { |event| event.first == :start }
      assert_equal 1, starts.length
      assert_equal [:commit], trace.last
      assert_equal 0, trace.count { |event| event.first == :abort }
      assert_equal name, trace.find { |event| event.first == :command }[1]
      assert_operator trace.index(starts.first), :<,
                      trace.index(trace.find { |event| event.first == :command })
    end
  end

  def test_each_failed_model_mutation_aborts_its_single_operation
    mutation_calls.each_value do |invocation|
      trace = []
      adapter = adapter_for(trace: trace, failure: RuntimeError.new('mutation failed'))

      assert_raises(RuntimeError) { invocation.call(adapter) }

      assert_equal 1, trace.count { |event| event.first == :start }
      assert_equal 0, trace.count { |event| event.first == :commit }
      assert_equal [:abort], trace.last
      assert_operator trace.index(trace.find { |event| event.first == :start }), :<,
                      trace.index(trace.find { |event| event.first == :command })
    end
  end

  def test_preflight_failure_never_starts_an_operation_or_calls_commands
    trace = []
    model = AdapterTestModel.new(trace: trace, entities: {})
    adapter = adapter_for(trace: trace, model: model)

    error = assert_raises(RuntimeError) { adapter.delete_component(id: 404) }

    assert_includes error.message, 'Entity not found'
    assert_equal [[:find, 404]], trace
  end

  def test_start_operation_failure_is_not_aborted
    trace = []
    model = AdapterTestModel.new(
      trace: trace,
      start_failure: RuntimeError.new('cannot start operation')
    )
    adapter = adapter_for(trace: trace, model: model)

    assert_raises(RuntimeError) do
      adapter.create_component(type: 'cube', position: [0, 0, 0], dimensions: [1, 1, 1])
    end

    assert_equal [[:start, 'Create component', true]], trace
  end

  def test_material_and_solid_capability_failures_happen_before_operations
    trace = []
    entities = { 1 => AdapterTestEntity.new(solid: false), 2 => AdapterTestEntity.new }
    model = AdapterTestModel.new(trace: trace, entities: entities)
    adapter = adapter_for(trace: trace, model: model)

    assert_raises(RuntimeError) { adapter.set_material(id: 1, material: 'Not Installed') }
    assert_raises(RuntimeError) do
      adapter.boolean_operation(
        operation: 'union', target_id: 1, tool_id: 2, delete_originals: false
      )
    end

    assert_equal 0, trace.count { |event| event.first == :start }
    assert_equal 0, trace.count { |event| event.first == :command }
  end

  def test_selection_and_export_do_not_start_model_operations
    trace = []
    adapter = adapter_for(trace: trace)

    adapter.get_selection
    adapter.export_scene(format: 'skp')

    assert_equal 0, trace.count { |event| event.first == :start }
    assert_equal %w[get_selection export_scene],
                 trace.select { |event| event.first == :command }.map { |event| event[1] }
  end

  private

  def adapter_for(trace:, failure: nil, model: nil)
    SU_MCP::SketchupAdapter.new(
      commands: AdapterTestCommands.new(trace: trace, failure: failure),
      model: model || AdapterTestModel.new(trace: trace)
    )
  end

  def mutation_calls
    {
      'create_component' => lambda do |adapter|
        adapter.create_component(type: 'cube', position: [0, 0, 0], dimensions: [1, 1, 1])
      end,
      'delete_component' => ->(adapter) { adapter.delete_component(id: 1) },
      'transform_component' => lambda do |adapter|
        adapter.transform_component(id: 1, position: [1, 2, 3], rotation: nil, scale: nil)
      end,
      'set_material' => ->(adapter) { adapter.set_material(id: 1, material: '#ff8800') },
      'boolean_operation' => lambda do |adapter|
        adapter.boolean_operation(
          operation: 'union',
          target_id: 1,
          tool_id: 2,
          delete_originals: false
        )
      end
    }
  end
end


HeadlessTest.run(SketchupAdapterTest)
