require_relative '../su_mcp/sketchup_adapter'
require_relative 'headless'


class ControlledAdapterEntity
  attr_reader :parent

  def initialize(parent: nil)
    @parent = parent
  end

  def manifold? = true
  def subtract(_other); end
  def union(_other); end
end


class ControlledAdapterModel
  attr_reader :materials, :trace

  def initialize(entities: {}, materials: {})
    @entities = entities
    @materials = materials
    @trace = []
  end

  def find_entity_by_id(id)
    @entities[id]
  end

  def start_operation(name, disable_ui)
    @trace << [:start, name, disable_ui]
  end

  def commit_operation
    @trace << [:commit]
  end

  def abort_operation
    @trace << [:abort]
  end
end


class ControlledAdapterCommands
  attr_reader :calls

  def initialize(result: { success: true })
    @result = result
    @calls = []
  end

  def call(name, arguments, solid_method: nil)
    @calls << [name, arguments, solid_method]
    @result
  end

  def list_resources
    [{ uri: 'sketchup://model' }]
  end
end


class SketchupAdapterHeadlessTest
  include HeadlessTest::Assertions

  def test_callable_model_and_omitted_transform_fields_cross_the_public_adapter_seam
    entity = ControlledAdapterEntity.new
    model = ControlledAdapterModel.new(entities: { 7 => entity })
    commands = ControlledAdapterCommands.new(result: { success: true, id: 7 })
    adapter = SU_MCP::SketchupAdapter.new(commands: commands, model: -> { model })

    result = adapter.transform_component(id: 7, position: nil, rotation: nil, scale: nil)

    assert_equal({ id: 7 }, result)
    assert_equal ['transform_component', { 'id' => 7 }, nil], commands.calls.fetch(0)
  end

  def test_resources_are_delegated_without_a_model_operation
    commands = ControlledAdapterCommands.new
    adapter = SU_MCP::SketchupAdapter.new(
      commands: commands,
      model: ControlledAdapterModel.new
    )

    assert_equal [{ uri: 'sketchup://model' }], adapter.list_resources
  end

  def test_joinery_rejects_entities_from_different_modeling_contexts
    model = ControlledAdapterModel.new(
      entities: {
        1 => ControlledAdapterEntity.new(parent: Object.new),
        2 => ControlledAdapterEntity.new(parent: Object.new)
      }
    )
    adapter = SU_MCP::SketchupAdapter.new(
      commands: ControlledAdapterCommands.new,
      model: model
    )

    error = assert_raises(SU_MCP::CommandExecutionError) do
      adapter.create_mortise_tenon(
        mortise_id: 1, tenon_id: 2, width: 1, height: 1, depth: 1,
        offset_x: 0, offset_y: 0, offset_z: 0
      )
    end

    assert_equal 'incompatible_entity_context', error.kind
    assert_equal [], model.trace
  end

  def test_common_and_installed_materials_pass_preflight
    entity = ControlledAdapterEntity.new
    model = ControlledAdapterModel.new(
      entities: { 1 => entity },
      materials: { 'Installed Wood' => Object.new }
    )
    commands = ControlledAdapterCommands.new(result: { success: true, id: 1 })
    adapter = SU_MCP::SketchupAdapter.new(commands: commands, model: model)

    adapter.set_material(id: 1, material: 'red')
    adapter.set_material(id: 1, material: 'Installed Wood')

    materials = commands.calls.map { |_name, arguments| arguments['material'] }
    assert_equal %w[red Installed\ Wood], materials
  end

  def test_string_keyed_success_and_unsuccessful_results_are_normalized
    successful = SU_MCP::SketchupAdapter.new(
      commands: ControlledAdapterCommands.new(
        result: { 'success' => true, 'path' => '/tmp/model.skp', 'format' => 'skp' }
      ),
      model: ControlledAdapterModel.new
    )
    unsuccessful = SU_MCP::SketchupAdapter.new(
      commands: ControlledAdapterCommands.new(result: { success: false }),
      model: ControlledAdapterModel.new
    )

    assert_equal({ 'path' => '/tmp/model.skp', 'format' => 'skp' },
                 successful.export_scene(format: 'skp'))
    error = assert_raises(RuntimeError) { unsuccessful.get_selection }
    assert_equal 'Operation failed', error.message
  end
end


HeadlessTest.run(SketchupAdapterHeadlessTest)
