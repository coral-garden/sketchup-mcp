require 'json'

require_relative '../su_mcp/command_dispatcher'
require_relative '../su_mcp/command_executor'
require_relative '../su_mcp/sketchup_adapter'
require_relative '../su_mcp/sketchup_commands'
require_relative 'headless'


module Geom
  class Point3d
    def self.new(*coordinates)
      coordinates
    end
  end

  class Vector3d < Point3d; end

  class Transformation
    class << self
      def translation(value) = [:translation, value]
      def rotation(*values) = [:rotation, values]
      def scaling(*values) = [:scaling, values]
    end
  end
end


module Sketchup
  class Color
    def self.new(*channels)
      channels
    end
  end
end


class CompositionFace
  def pushpull(distance)
    @distance = distance
  end
end


class CompositionEntities
  def initialize(group_id: 900)
    @group_id = group_id
  end

  def add_group
    CompositionEntity.new(@group_id, entities: CompositionEntities.new(group_id: @group_id + 1))
  end

  def add_face(*_points)
    CompositionFace.new
  end
end


class CompositionEntity
  Bounds = Struct.new(:center)

  attr_reader :entityID, :entities, :transformations
  attr_accessor :material

  def initialize(id, entities: CompositionEntities.new)
    @entityID = id
    @entities = entities
    @transformations = []
    @valid = true
  end

  def typename = 'Group'
  def bounds = Bounds.new([0, 0, 0])
  def valid? = @valid

  def erase!
    @valid = false
  end

  def transform!(transformation)
    @transformations << transformation
  end
end


class CompositionMaterial
  attr_accessor :color
end


class CompositionMaterials
  def initialize
    @materials = {}
  end

  def [](name)
    @materials[name]
  end

  def add(name)
    @materials[name] = CompositionMaterial.new
  end
end


class CompositionModel
  attr_reader :active_entities, :materials, :selection, :operations

  def initialize
    @active_entities = CompositionEntities.new
    @entity = CompositionEntity.new(731)
    @materials = CompositionMaterials.new
    @selection = [@entity]
    @operations = []
  end

  def find_entity_by_id(id)
    @entity if id == 731
  end

  def start_operation(name, disable_ui)
    @operations << [:start, name, disable_ui]
  end

  def commit_operation
    @operations << [:commit]
  end

  def abort_operation
    @operations << [:abort]
  end
end


class SceneGeometryCompositionTest
  include HeadlessTest::Assertions

  CASES = [
    ['create_component', {}, { id: 900 }, true],
    ['delete_component', { 'id' => '731' }, {}, true],
    [
      'transform_component',
      { 'id' => 731, 'position' => [1, 2, 3], 'rotation' => [0, 0, 90], 'scale' => [1, 2, 3] },
      { id: 731 },
      true
    ],
    ['get_selection', {}, { entities: [{ id: 731, type: 'group' }] }, false],
    ['set_material', { 'id' => 731, 'material' => '#ff8800' }, { id: 731 }, true]
  ].freeze

  def test_production_composition_carries_each_model_command_end_to_end
    CASES.each_with_index do |(name, arguments, expected, mutates), index|
      model = CompositionModel.new
      commands = SU_MCP::SketchupCommands.new(model: model)
      adapter = SU_MCP::SketchupAdapter.new(commands: commands, model: model)
      dispatcher = SU_MCP::CommandDispatcher.new(
        executor: SU_MCP::CommandExecutor.new(adapter: adapter)
      )

      response = dispatcher.call(
        'jsonrpc' => '2.0',
        'method' => 'tools/call',
        'params' => { 'name' => name, 'arguments' => arguments },
        'id' => "composition-#{index}"
      )

      assert_equal JSON.generate(expected), response.dig(:result, :content, 0, :text)
      assert_equal "composition-#{index}", response[:id]
      assert_equal(mutates ? 1 : 0, model.operations.count { |event| event.first == :start })
      assert_equal(mutates ? 1 : 0, model.operations.count { |event| event.first == :commit })
      assert_equal 0, model.operations.count { |event| event.first == :abort }
    end
  end
end


HeadlessTest.run(SceneGeometryCompositionTest)
