require_relative '../su_mcp/sketchup_commands'
require_relative 'headless'

# The composition fakes elsewhere discard face points and pushpull distances, so
# they can only assert topology. These fakes record both, which is what lets the
# tests below pin dimensions rather than shape counts.

module Geom
  class Point3d
    def self.new(*coordinates) = coordinates
  end
  class Vector3d < Point3d; end
end


class RecordingFace
  attr_reader :points, :pushpull_distance

  def initialize(points)
    @points = points
  end

  def pushpull(distance)
    @pushpull_distance = distance
  end
end


class RecordingEntities
  def initialize(recorder, group_id: 900)
    @recorder = recorder
    @group_id = group_id
  end

  def add_group
    entity = RecordingEntity.new(
      @group_id,
      entities: RecordingEntities.new(@recorder, group_id: @group_id + 1)
    )
    @recorder[:groups] << entity
    entity
  end

  # add_face is called both as add_face(p1, p2, ...) and add_face([p1, p2, ...]).
  def add_face(*arguments)
    points =
      if arguments.length == 1 && arguments.first.is_a?(Array) &&
         arguments.first.first.is_a?(Array)
        arguments.first
      else
        arguments
      end
    face = RecordingFace.new(points)
    @recorder[:faces] << face
    face
  end
end


class RecordingEntity
  attr_reader :entityID, :entities

  def initialize(id, entities:)
    @entityID = id
    @entities = entities
  end

  def typename = 'Group'
end


class RecordingModel
  attr_reader :active_entities, :recorder

  def initialize
    @recorder = { faces: [], groups: [] }
    @active_entities = RecordingEntities.new(@recorder)
  end

  def start_operation(*); end
  def commit_operation; end
  def abort_operation; end
end


class GeometryDimensionsTest
  include HeadlessTest::Assertions

  POSITION = [1, 2, 3].freeze
  DIMENSIONS = [4, 6, 10].freeze
  TOLERANCE = 1e-9

  def test_cube_face_spans_exactly_the_requested_footprint
    faces = build('cube')[:faces]

    assert_equal 1, faces.length
    assert_points(
      [[1, 2, 3], [5, 2, 3], [5, 8, 3], [1, 8, 3]],
      faces.first.points
    )
    assert_equal DIMENSIONS[2], faces.first.pushpull_distance
  end

  def test_cylinder_circle_has_the_requested_radius_segments_and_height
    faces = build('cylinder')[:faces]

    assert_equal 1, faces.length
    face = faces.first
    assert_equal 24, face.points.length
    assert_points(expected_circle, face.points)
    assert_equal DIMENSIONS[2], face.pushpull_distance
  end

  def test_cone_base_circle_matches_the_cylinder_profile
    faces = build('cone')[:faces]

    base = faces.first
    assert_equal 24, base.points.length
    assert_points(expected_circle, base.points)
  end

  private

  # radius is dims[0]/2, the circle sweeps a full turn, and the centre is offset
  # from the requested position by the radius on both planar axes.
  def expected_circle
    radius = DIMENSIONS[0] / 2.0
    centre = [POSITION[0] + radius, POSITION[1] + radius, POSITION[2]]
    Array.new(24) do |index|
      angle = Math::PI * 2 * index / 24
      [
        centre[0] + (radius * Math.cos(angle)),
        centre[1] + (radius * Math.sin(angle)),
        centre[2]
      ]
    end
  end

  def build(type)
    model = RecordingModel.new
    commands = SU_MCP::SketchupCommands.new(model: model)
    commands.call(
      'create_component',
      { 'type' => type, 'position' => POSITION, 'dimensions' => DIMENSIONS }
    )
    model.recorder
  end

  def assert_points(expected, actual)
    assert_equal expected.length, actual.length
    expected.zip(actual).each_with_index do |(want, got), index|
      want.zip(got).each_with_index do |(a, b), axis|
        next if (a - b).abs <= TOLERANCE

        raise "point #{index} axis #{axis}: expected #{a}, got #{b}"
      end
    end
  end
end

HeadlessTest.run(GeometryDimensionsTest)
