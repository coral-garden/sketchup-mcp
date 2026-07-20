require_relative 'support'
require_relative 'controlled_adapters'


class TC_ProductionAdapter < TestUp::TestCase
  def self.test_order = :alpha

  def setup
    @export_sandbox = SketchupMcpTestUp::ExportSandbox.new
    @previous_temp = @export_sandbox.environment.to_h do |name, _value|
      [name, ENV[name]]
    end
    @export_sandbox.environment.each { |name, value| ENV[name] = value }
    @model = start_with_empty_model
    @commands = SU_MCP::SketchupCommands.new(model: @model)
    @adapter = SU_MCP::SketchupAdapter.new(commands: @commands, model: @model)
    @executor = SU_MCP::CommandExecutor.new(adapter: @adapter)
  end

  def teardown
    start_with_empty_model
  ensure
    @previous_temp&.each { |name, value| ENV[name] = value }
    @export_sandbox&.close
  end

  def test_abort_failure_retries_operation_cleanup
    commands = ControlledSketchupCommands.new
    commands.failure = RuntimeError.new('controlled command failure')
    model = OperationLifecycleModel.new(abort_error_once: true)
    adapter = SU_MCP::SketchupAdapter.new(commands: commands, model: model)

    assert_raises(RuntimeError) do
      adapter.create_component(
        type: 'cube', position: [0, 0, 0], dimensions: [1, 1, 1]
      )
    end
    assert_equal 2, model.abort_count
    scenario_passed!
  end

  def test_boolean_geometry
    cases = {
      'union' => { volume: 12.0, width: 3.0, delete: false },
      'difference' => { volume: 4.0, width: 1.0, delete: false },
      'intersection' => { volume: 4.0, width: 1.0, delete: true }
    }
    cases.each_with_index do |(operation, expected), index|
      target_id = create_box([index * 10, 0, 0], [2, 2, 2])
      tool_id = create_box([(index * 10) + 1, 0, 0], [2, 2, 2])
      entity_count = @model.entities.length

      result = execute(
        'boolean_operation',
        'operation' => operation,
        'target_id' => target_id,
        'tool_id' => tool_id,
        'delete_originals' => expected.fetch(:delete)
      )
      solid = entity!(result.fetch(:id))

      refute_includes [target_id, tool_id], solid.entityID
      assert solid.manifold?
      assert_in_delta expected.fetch(:volume), solid.volume, 0.01
      assert_in_delta expected.fetch(:width), solid.bounds.width, 0.01
      if expected.fetch(:delete)
        assert_nil @model.find_entity_by_id(target_id)
        assert_nil @model.find_entity_by_id(tool_id)
        assert_equal entity_count - 1, @model.entities.length
      else
        assert entity!(target_id).valid?
        assert entity!(tool_id).valid?
        assert_equal entity_count + 1, @model.entities.length
      end
    end
    scenario_passed!
  end

  def test_catalog_parity
    manifest_commands = SketchupMcpTestUp.manifest.fetch('commands')

    assert_equal manifest_commands, SU_MCP::CommandCatalog.new.names
    assert_equal manifest_commands,
                 packaged_catalog.fetch('commands').map { |item| item.fetch('name') }
    scenario_passed!
  end

  def test_command_results_accept_string_success_and_reject_failure
    commands = ControlledSketchupCommands.new
    model = OperationLifecycleModel.new
    adapter = SU_MCP::SketchupAdapter.new(commands: commands, model: model)

    commands.call_result = { 'success' => true, 'entities' => [] }
    assert_equal({ 'entities' => [] }, adapter.get_selection)

    commands.call_result = { success: false }
    error = assert_raises(RuntimeError) { adapter.export_scene(format: 'skp') }
    assert_equal 'Operation failed', error.message
    scenario_passed!
  end

  def test_create_component_geometry
    cases = {
      'cube' => [2, 3, 4],
      'cylinder' => [2, 2, 4],
      'sphere' => [2, 2, 2],
      'cone' => [2, 2, 4]
    }
    cases.each_with_index do |(type, dimensions), index|
      result = execute(
        'create_component',
        'type' => type,
        'position' => [index * 10, 0, 0],
        'dimensions' => dimensions
      )
      entity = entity!(result.fetch(:id))

      assert_instance_of Sketchup::Group, entity
      refute_empty entity.entities.to_a
      assert_in_delta dimensions.fetch(0), entity.bounds.width, 0.01
      assert_in_delta dimensions.fetch(1), entity.bounds.height, 0.01
      assert_in_delta dimensions.fetch(2), entity.bounds.depth, 0.01
    end
    scenario_passed!
  end

  def test_default_model_resolution
    adapter = SU_MCP::SketchupAdapter.new(commands: @commands)

    result = adapter.create_component(
      type: 'cube', position: [20, 0, 0], dimensions: [1, 1, 1]
    )

    assert entity!(result.fetch(:id)).valid?
    scenario_passed!
  end

  def test_delete_component_and_undo
    entity_id = execute('create_component', 'type' => 'cube').fetch(:id)
    count_before_delete = @model.entities.length

    assert_equal({}, execute('delete_component', 'id' => entity_id))
    assert_nil @model.find_entity_by_id(entity_id)
    assert_equal count_before_delete - 1, @model.entities.length

    Sketchup.undo
    assert_equal count_before_delete, @model.entities.length
    scenario_passed!
  end

  def test_dovetail_geometry
    dovetail_ids, dovetail_volumes = joint_fixture(0)
    dovetail = execute(
      'create_dovetail',
      'tail_id' => dovetail_ids.fetch(0),
      'pin_id' => dovetail_ids.fetch(1),
      'width' => 3,
      'height' => 1,
      'depth' => 1,
      'num_tails' => 3,
      'angle' => 15
    )
    dovetail_delta = assert_joint_modification(
      dovetail, dovetail_ids, dovetail_volumes, :tail_id, :pin_id,
      first_direction: :increase, second_direction: :decrease
    )

    finger_ids, finger_volumes = joint_fixture(10)
    straight = execute(
      'create_finger_joint',
      'board1_id' => finger_ids.fetch(0),
      'board2_id' => finger_ids.fetch(1),
      'width' => 3,
      'height' => 1,
      'depth' => 1,
      'num_fingers' => 3
    )
    straight_delta = assert_joint_modification(
      straight, finger_ids, finger_volumes, :board1_id, :board2_id,
      first_direction: :increase, second_direction: :decrease
    )

    assert_operator dovetail_delta.fetch(0), :>, straight_delta.fetch(0)
    scenario_passed!
  end

  def test_eval_ruby_rejects_operation_control
    before = @model.entities.length

    error = assert_raises(SU_MCP::InvalidArguments) do
      execute(
        'eval_ruby',
        'code' => "Sketchup.active_model.start_operation('unsafe', true)"
      )
    end

    assert_match(/forbidden operation-management call/, error.message)
    assert_equal before, @model.entities.length
    scenario_passed!
  end

  def test_eval_ruby_success_and_normalization
    result = execute(
      'eval_ruby',
      'code' => "{'version' => Sketchup.version, :entities => Sketchup.active_model.entities.length}"
    )

    assert_equal 'Hash', result.fetch(:result_type)
    assert_equal Sketchup.version, result.fetch(:result).fetch('version')
    assert_equal 0, result.fetch(:result).fetch('entities')
    scenario_passed!
  end

  def test_export_failure_cleanup
    export_root = File.join(@export_sandbox.root, 'sketchup_exports')
    before = Dir[File.join(export_root, 'sketchup_export_*')]

    assert_raises(RuntimeError) do
      @commands.call('export_scene', { 'format' => 'unsupported' })
    end

    assert_equal before, Dir[File.join(export_root, 'sketchup_export_*')]
    scenario_passed!
  end

  def test_export_success_cleanup
    execute('create_component', 'type' => 'cube')
    result = execute('export_scene', 'format' => 'skp')
    path = result.fetch(:path)

    assert_equal 'skp', result.fetch(:format)
    assert File.file?(path)
    assert_operator File.size(path), :>, 0
    @export_sandbox.cleanup_export(path)
    refute File.exist?(path)
    scenario_passed!
  end

  def test_failure_aborts_model_changes
    before = @model.entities.length

    error = assert_raises(SU_MCP::CommandExecutionError) do
      execute(
        'eval_ruby',
        'code' => "Sketchup.active_model.active_entities.add_group; raise 'secret TestUp failure'"
      )
    end

    assert_equal 'evaluation_error', error.kind
    refute_match(/secret TestUp failure/, error.message)
    assert_equal before, @model.entities.length
    scenario_passed!
  end

  def test_finger_joint_geometry
    many_ids, many_volumes = joint_fixture(0)
    many = execute(
      'create_finger_joint',
      'board1_id' => many_ids.fetch(0),
      'board2_id' => many_ids.fetch(1),
      'width' => 3,
      'height' => 1,
      'depth' => 1,
      'num_fingers' => 5
    )
    many_delta = assert_joint_modification(
      many, many_ids, many_volumes, :board1_id, :board2_id,
      first_direction: :increase, second_direction: :decrease
    )

    single_ids, single_volumes = joint_fixture(10)
    single = execute(
      'create_finger_joint',
      'board1_id' => single_ids.fetch(0),
      'board2_id' => single_ids.fetch(1),
      'width' => 3,
      'height' => 1,
      'depth' => 1,
      'num_fingers' => 1
    )
    single_delta = assert_joint_modification(
      single, single_ids, single_volumes, :board1_id, :board2_id,
      first_direction: :increase, second_direction: :decrease
    )

    refute_in_delta many_delta.fetch(0), single_delta.fetch(0), 0.01
    scenario_passed!
  end

  def test_invalid_material_and_boolean_preflight
    commands = ControlledSketchupCommands.new
    model = OperationLifecycleModel.new(
      entities: {
        1 => ControlledSolidEntity.new(solid: false),
        2 => ControlledSolidEntity.new
      }
    )
    adapter = SU_MCP::SketchupAdapter.new(commands: commands, model: model)

    material_error = assert_raises(RuntimeError) do
      adapter.set_material(id: 2, material: 'Missing TestUp Material')
    end
    assert_equal 'Material not found: Missing TestUp Material', material_error.message

    boolean_error = assert_raises(RuntimeError) do
      adapter.boolean_operation(
        operation: 'union', target_id: 1, tool_id: 2, delete_originals: false
      )
    end
    assert_match(/not a solid group with union support/, boolean_error.message)
    scenario_passed!
  end

  def test_joinery_preflight_rejects_invalid_entities
    commands = ControlledSketchupCommands.new

    missing = adapter_with(commands, {})
    error = assert_raises(SU_MCP::CommandExecutionError) do
      missing.create_mortise_tenon(mortise_id: 1, tenon_id: 2)
    end
    assert_equal 'entity_not_found', error.kind

    unsupported = adapter_with(
      commands,
      1 => ControlledSolidEntity.new(solid: false),
      2 => ControlledSolidEntity.new
    )
    error = assert_raises(SU_MCP::CommandExecutionError) do
      unsupported.create_dovetail(tail_id: 1, pin_id: 2)
    end
    assert_equal 'unsupported_entity', error.kind

    incompatible = adapter_with(
      commands,
      1 => ControlledSolidEntity.new(parent: Object.new),
      2 => ControlledSolidEntity.new(parent: Object.new)
    )
    error = assert_raises(SU_MCP::CommandExecutionError) do
      incompatible.create_finger_joint(board1_id: 1, board2_id: 2)
    end
    assert_equal 'incompatible_entity_context', error.kind

    parentless = adapter_with(
      commands,
      1 => ParentlessControlledSolid.new,
      2 => ParentlessControlledSolid.new
    )
    assert_equal(
      { mortise_id: 1, tenon_id: 2 },
      parentless.create_mortise_tenon(mortise_id: 1, tenon_id: 2)
    )
    scenario_passed!
  end

  def test_mortise_tenon_geometry
    ids, volumes = joint_fixture(0)

    result = execute(
      'create_mortise_tenon',
      'mortise_id' => ids.fetch(0),
      'tenon_id' => ids.fetch(1),
      'width' => 1,
      'height' => 1,
      'depth' => 1
    )

    deltas = assert_joint_modification(
      result, ids, volumes, :mortise_id, :tenon_id,
      first_direction: :decrease, second_direction: :increase
    )
    assert_in_delta 0.5, deltas.fetch(0), 0.05
    assert_in_delta 0.5, deltas.fetch(1), 0.05
    scenario_passed!
  end

  def test_operation_start_failure_does_not_abort
    model = OperationLifecycleModel.new(
      start_error: RuntimeError.new('controlled start failure')
    )
    adapter = SU_MCP::SketchupAdapter.new(
      commands: ControlledSketchupCommands.new,
      model: model
    )

    assert_raises(RuntimeError) do
      adapter.create_component(
        type: 'cube', position: [0, 0, 0], dimensions: [1, 1, 1]
      )
    end
    assert_equal 0, model.abort_count
    scenario_passed!
  end

  def test_selection_and_resources
    entity_id = execute('create_component', 'type' => 'cube').fetch(:id)
    entity = entity!(entity_id)
    @model.selection.add(entity)

    selection = execute('get_selection')
    resources = @adapter.list_resources

    assert_equal [{ id: entity_id, type: 'group' }], selection.fetch(:entities)
    assert_includes resources, { id: entity_id, type: 'group' }
    scenario_passed!
  end

  def test_set_material
    entity_id = execute('create_component', 'type' => 'cube').fetch(:id)
    entity = entity!(entity_id)

    result = execute('set_material', 'id' => entity_id, 'material' => '#336699')
    assert_equal({ id: entity_id }, result)
    assert_equal Sketchup::Color.new(51, 102, 153), entity.material.color

    existing = @model.materials.add('TestUp Existing Material')
    existing.color = Sketchup::Color.new(10, 20, 30)
    execute('set_material', 'id' => entity_id, 'material' => existing.name)
    assert_equal existing, entity.material

    execute('set_material', 'id' => entity_id, 'material' => 'red')
    assert_equal Sketchup::Color.new(255, 0, 0), entity.material.color
    scenario_passed!
  end

  def test_transform_component_geometry
    entity_id = execute(
      'create_component',
      'type' => 'cube',
      'dimensions' => [2, 3, 4]
    ).fetch(:id)

    result = execute(
      'transform_component',
      'id' => entity_id,
      'position' => [5, 6, 7],
      'rotation' => [0, 0, 90],
      'scale' => [2, 1, 0.5]
    )
    entity = entity!(result.fetch(:id))

    assert_in_delta 6, entity.bounds.center.x, 0.001
    assert_in_delta 7.5, entity.bounds.center.y, 0.001
    assert_in_delta 9, entity.bounds.center.z, 0.001
    assert_in_delta 6, entity.bounds.width, 0.001
    assert_in_delta 2, entity.bounds.height, 0.001
    assert_in_delta 2, entity.bounds.depth, 0.001

    unchanged = @adapter.transform_component(
      id: entity_id, position: nil, rotation: nil, scale: nil
    )
    assert_equal entity_id, unchanged.fetch(:id)
    scenario_passed!
  end

  private

  def scenario_passed!
    SketchupMcpTestUp.complete_scenario!(name)
  end

  def execute(name, arguments = {})
    @executor.call(name, arguments).result
  end

  def packaged_catalog
    JSON.parse(File.read(SketchupMcpTestUp.packaged_catalog_path))
  end

  def entity!(id)
    entity = @model.find_entity_by_id(id)
    refute_nil entity, "expected entity #{id} to exist"
    entity
  end

  def create_box(position, dimensions)
    execute(
      'create_component',
      'type' => 'cube',
      'position' => position,
      'dimensions' => dimensions
    ).fetch(:id)
  end

  def joint_fixture(y_offset)
    ids = [
      create_box([0, y_offset, 0], [4, 6, 4]),
      create_box([4, y_offset, 0], [4, 6, 4])
    ]
    [ids, ids.map { |id| entity!(id).volume }]
  end

  def assert_joint_modification(
    result, original_ids, original_volumes, first_key, second_key,
    first_direction:, second_direction:
  )
    entities = [entity!(result.fetch(first_key)), entity!(result.fetch(second_key))]
    refute_includes original_ids, entities.fetch(0).entityID
    refute_includes original_ids, entities.fetch(1).entityID
    entities.each { |entity| assert entity.manifold? }
    deltas = entities.each_with_index.map do |entity, index|
      direction = index.zero? ? first_direction : second_direction
      original = original_volumes.fetch(index)
      if direction == :increase
        assert_operator entity.volume, :>, original
      else
        assert_operator entity.volume, :<, original
      end
      (entity.volume - original).abs
    end
    deltas
  end

  def adapter_with(commands, entities)
    SU_MCP::SketchupAdapter.new(
      commands: commands,
      model: OperationLifecycleModel.new(entities: entities)
    )
  end
end
