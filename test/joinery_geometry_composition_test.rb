require_relative '../su_mcp/command_dispatcher'
require_relative '../su_mcp/command_executor'
require_relative '../su_mcp/sketchup_adapter'
require_relative '../su_mcp/sketchup_commands'
require_relative 'headless'


class GeometryPoint
  attr_reader :x, :y, :z

  def initialize(x, y, z)
    @x = x
    @y = y
    @z = z
  end
end


class GeometryBounds
  attr_reader :min, :max, :center

  def initialize(min: [0, 0, 0], max: [10, 8, 4])
    @min = GeometryPoint.new(*min)
    @max = GeometryPoint.new(*max)
    @center = GeometryPoint.new(
      (min[0] + max[0]) / 2.0,
      (min[1] + max[1]) / 2.0,
      (min[2] + max[2]) / 2.0
    )
  end
end


class GeometryFace
  def initialize(trace)
    @trace = trace
  end

  def pushpull(distance)
    @trace << [:pushpull, distance]
  end
end


class GeometryEntities
  attr_reader :trace

  def initialize(trace, ids)
    @trace = trace
    @ids = ids
  end

  def add_group
    id = @ids.shift
    @trace << [:add_group, id]
    GeometrySolid.new(id, parent: self, trace: @trace, ids: @ids)
  end

  def add_face(points)
    @trace << [:add_face, points]
    GeometryFace.new(@trace)
  end
end


class GeometrySolid
  attr_reader :entityID, :parent, :bounds, :unique_count

  def initialize(
    id, parent:, trace:, ids:, component: false, operation_results: nil,
    bounds: GeometryBounds.new
  )
    @entityID = id
    @parent = parent
    @bounds = bounds
    @trace = trace
    @ids = ids
    @component = component
    @unique_count = 0
    @valid = true
    @operation_results = operation_results || []
    @entities = GeometryEntities.new(@trace, @ids)
  end

  def entities = @entities
  def manifold? = true
  def valid? = @valid

  def make_unique
    @unique_count += 1
    @trace << [:make_unique, @entityID]
  end

  def to_group
    @trace << [:to_group, @entityID]
    @component = false
    self
  end

  def subtract(tool) = solid_result(:subtract, tool)
  def union(tool) = solid_result(:union, tool)

  def erase!
    @valid = false
    @trace << [:erase, @entityID]
  end

  private

  def solid_result(method, tool)
    @trace << [method, @entityID, tool.entityID]
    configured = @operation_results.shift
    return nil if configured == :fail

    GeometrySolid.new(configured || @ids.shift, parent: @parent, trace: @trace, ids: @ids)
  end
end


class JoineryGeometryModel
  attr_reader :active_entities, :operations, :trace, :entities

  def initialize(
    fail_first: false,
    second_bounds: GeometryBounds.new(min: [0, 0, 4], max: [10, 8, 8])
  )
    @trace = []
    @operations = []
    ids = (300..380).to_a
    @active_entities = GeometryEntities.new(@trace, ids)
    @entities = {
      1 => GeometrySolid.new(
        1, parent: @active_entities, trace: @trace, ids: ids,
        component: true, operation_results: fail_first ? [:fail] : []
      ),
      2 => GeometrySolid.new(
        2,
        parent: @active_entities,
        trace: @trace,
        ids: ids,
        component: true,
        bounds: second_bounds
      )
    }
  end

  def find_entity_by_id(id) = @entities[id]
  def start_operation(name, disable_ui) = @operations << [:start, name, disable_ui]
  def commit_operation = @operations << [:commit]
  def abort_operation = @operations << [:abort]
end


class JoineryGeometryCompositionTest
  include HeadlessTest::Assertions

  def test_each_joinery_command_builds_matching_solids_and_changes_both_topologies
    cases = {
      'create_mortise_tenon' => [
        { 'mortise_id' => 1, 'tenon_id' => 2 }, %w[mortise_id tenon_id], 2
      ],
      'create_dovetail' => [
        { 'tail_id' => 1, 'pin_id' => 2, 'angle' => 15, 'num_tails' => 3 },
        %w[tail_id pin_id],
        6
      ],
      'create_finger_joint' => [
        { 'board1_id' => 1, 'board2_id' => 2, 'num_fingers' => 5 },
        %w[board1_id board2_id],
        10
      ]
    }

    cases.each_with_index do |(name, (arguments, result_fields, profile_count)), index|
      model = JoineryGeometryModel.new
      response = dispatcher_for(model).call(tool_request(name, arguments, index))
      assert_equal nil, response[:error]
      result = JSON.parse(response.dig(:result, :content, 0, :text))

      assert_equal result_fields, result.keys
      assert_equal 2, model.trace.count { |event| %i[subtract union].include?(event.first) }
      assert_equal profile_count, model.trace.count { |event| event.first == :add_face }
      profiles = model.trace.select { |event| event.first == :add_face }.map(&:last)
      per_tool = profile_count / 2
      assert_equal profiles.first(per_tool), profiles.last(per_tool)
      unless name == 'create_mortise_tenon'
        first_max = profiles[0].map(&:first).max
        second_min = profiles[1].map(&:first).min
        assert_operator second_min, :>, first_max
      end
      assert_equal 1, model.entities[1].unique_count
      assert_equal 1, model.entities[2].unique_count
      assert_equal [[:start, operation_name(name), true], [:commit]], model.operations
    end
  end

  def test_geometry_failure_is_rolled_back_and_returns_a_typed_execution_error
    model = JoineryGeometryModel.new(fail_first: true)

    response = dispatcher_for(model).call(
      tool_request('create_mortise_tenon', { 'mortise_id' => 1, 'tenon_id' => 2 }, 'failure')
    )

    assert_equal(-32_603, response.dig(:error, :code))
    assert_equal 'joinery_geometry_error', response.dig(:error, :data, :type)
    assert_equal [[:start, 'Create mortise and tenon', true], [:abort]], model.operations
  end

  def test_translated_rotated_parent_space_bounds_share_one_mating_frame
    # SketchUp Drawingelement#bounds already reflects an instance transform in
    # its parent coordinate system. A diagonal center-to-center direction here
    # therefore represents adjacent instances rotated in that shared parent.
    model = JoineryGeometryModel.new(
      second_bounds: GeometryBounds.new(min: [8, 8, 0], max: [18, 16, 4])
    )

    response = dispatcher_for(model).call(
      tool_request('create_dovetail', { 'tail_id' => 1, 'pin_id' => 2 }, 'rotated')
    )

    assert_equal nil, response[:error]
    profiles = model.trace.select { |event| event.first == :add_face }.map(&:last)
    assert_equal profiles.first(3), profiles.last(3)
    edge = profiles.first[1].zip(profiles.first[0]).map { |right, left| right - left }
    assert_operator edge[0].abs, :>, 0
    assert_operator edge[1].abs, :>, 0
    assert_equal [[:start, 'Create dovetail', true], [:commit]], model.operations
  end

  def test_missing_entities_return_a_typed_error_before_an_operation_starts
    model = JoineryGeometryModel.new
    model.entities.delete(2)

    response = dispatcher_for(model).call(
      tool_request('create_finger_joint', { 'board1_id' => 1, 'board2_id' => 2 }, 'missing')
    )

    assert_equal(-32_603, response.dig(:error, :code))
    assert_equal 'entity_not_found', response.dig(:error, :data, :type)
    assert_equal 'Joinery entity was not found', response.dig(:error, :message)
    assert_equal [], model.operations
  end

  # The assertions above count profiles; these measure them. Without dimensional
  # checks, changes to cell width, spacing, taper, or extrusion depth leave every
  # topology assertion green.
  def test_finger_profiles_have_the_requested_cell_width_spacing_and_depth
    model = JoineryGeometryModel.new
    response = dispatcher_for(model).call(
      tool_request(
        'create_finger_joint',
        {
          'board1_id' => 1, 'board2_id' => 2, 'num_fingers' => 3,
          'width' => 12.0, 'height' => 6.0, 'depth' => 2.0
        },
        'fingers'
      )
    )
    assert_equal nil, response[:error]

    profiles = face_profiles(model).first(3)
    # width 12 over (3 * 2) - 1 = 5 cells
    cell_width = 12.0 / 5
    profiles.each do |points|
      assert_close cell_width, bottom_width(points)
      assert_close cell_width, top_width(points)
      assert_close 6.0, profile_height(points)
    end

    centres = profiles.map { |points| centre_x(points) }
    assert_close 2 * cell_width, centres[1] - centres[0]
    assert_close 2 * cell_width, centres[2] - centres[1]
    # Spacing alone is shift-invariant, so pin the absolute position too: the
    # middle profile of an odd-count joint sits on the mating-face centre.
    assert_close 5.0, centres[1]

    depths = model.trace.select { |event| event.first == :pushpull }.map(&:last)
    assert_equal [2.0] * 6, depths
  end

  def test_dovetail_profiles_flare_by_the_capped_taper
    model = JoineryGeometryModel.new
    response = dispatcher_for(model).call(
      tool_request(
        'create_dovetail',
        {
          'tail_id' => 1, 'pin_id' => 2, 'num_tails' => 3, 'angle' => 60.0,
          'width' => 12.0, 'height' => 6.0, 'depth' => 2.0
        },
        'tails'
      )
    )
    assert_equal nil, response[:error]

    cell_width = 12.0 / 5
    # depth * tan(60°) is 3.46, so the cell_width * 0.45 cap binds at 1.08.
    taper = cell_width * 0.45
    face_profiles(model).first(3).each do |points|
      assert_close cell_width, top_width(points)
      assert_close cell_width + (2 * taper), bottom_width(points)
      assert_operator bottom_width(points), :>, top_width(points)
    end
  end

  private

  def face_profiles(model)
    model.trace.select { |event| event.first == :add_face }.map(&:last)
  end

  # The mating frame for these bounds puts the width axis on X, height on Y.
  def bottom_width(points) = (points[1][0] - points[0][0]).abs
  def top_width(points) = (points[2][0] - points[3][0]).abs
  def profile_height(points) = (points[3][1] - points[0][1]).abs
  def centre_x(points) = points.map(&:first).sum / points.length

  def assert_close(expected, actual, tolerance: 1e-9)
    return if (expected - actual).abs <= tolerance

    raise "expected #{expected}, got #{actual}"
  end

  def dispatcher_for(model)
    commands = SU_MCP::SketchupCommands.new(model: model)
    adapter = SU_MCP::SketchupAdapter.new(commands: commands, model: model)
    SU_MCP::CommandDispatcher.new(
      executor: SU_MCP::CommandExecutor.new(adapter: adapter)
    )
  end

  def tool_request(name, arguments, id)
    {
      'jsonrpc' => '2.0',
      'method' => 'tools/call',
      'params' => { 'name' => name, 'arguments' => arguments },
      'id' => id
    }
  end

  def operation_name(command)
    {
      'create_mortise_tenon' => 'Create mortise and tenon',
      'create_dovetail' => 'Create dovetail',
      'create_finger_joint' => 'Create finger joint'
    }.fetch(command)
  end
end


HeadlessTest.run(JoineryGeometryCompositionTest)
