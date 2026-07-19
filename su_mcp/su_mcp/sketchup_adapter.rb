require_relative 'command_catalog'
require_relative 'command_execution_error'


module SU_MCP
  class SketchupAdapter
    OPERATION_NAMES = {
      'create_component' => 'Create component',
      'delete_component' => 'Delete component',
      'transform_component' => 'Transform component',
      'set_material' => 'Set material',
      'boolean_operation' => 'Boolean operation',
      'create_mortise_tenon' => 'Create mortise and tenon',
      'create_dovetail' => 'Create dovetail',
      'create_finger_joint' => 'Create finger joint',
      'eval_ruby' => 'Evaluate Ruby'
    }.freeze
    COMMON_COLORS = %w[
      red green blue yellow cyan turquoise magenta purple white black brown orange gray grey
    ].freeze
    SOLID_METHODS = {
      'union' => :union,
      'difference' => :subtract,
      'intersection' => :intersect
    }.freeze

    def initialize(commands:, model: nil)
      @commands = commands
      @model = model || -> { Sketchup.active_model }
    end

    def create_component(type:, position:, dimensions:)
      result = mutate('create_component') do
        command_result(
          'create_component',
          {
            'type' => type,
            'position' => position,
            'dimensions' => dimensions
          }
        )
      end
      result.fetch(:id)
    end

    def delete_component(id:)
      model = active_model
      require_entity(model, id)
      mutate('delete_component', model) { command_result('delete_component', { 'id' => id }) }
    end

    def transform_component(id:, position:, rotation:, scale:)
      model = active_model
      require_entity(model, id)
      arguments = { 'id' => id }
      arguments['position'] = position unless position.nil?
      arguments['rotation'] = rotation unless rotation.nil?
      arguments['scale'] = scale unless scale.nil?
      mutate('transform_component', model) { command_result('transform_component', arguments) }
    end

    def get_selection
      command_result('get_selection', {})
    end

    def set_material(id:, material:)
      model = active_model
      require_entity(model, id)
      require_material(model, material)
      mutate('set_material', model) do
        command_result('set_material', { 'id' => id, 'material' => material })
      end
    end

    def export_scene(format:)
      command_result('export_scene', { 'format' => format })
    end

    def boolean_operation(operation:, target_id:, tool_id:, delete_originals:)
      model = active_model
      solid_method = SOLID_METHODS.fetch(operation)
      [target_id, tool_id].each do |id|
        entity = require_entity(model, id)
        unless entity.respond_to?(:manifold?) && entity.manifold? &&
               entity.respond_to?(:copy) && entity.respond_to?(solid_method)
          raise "Entity #{id} is not a solid group with #{solid_method} support"
        end
      end
      mutate('boolean_operation', model) do
        command_result(
          'boolean_operation',
          {
            'operation' => operation,
            'target_id' => target_id,
            'tool_id' => tool_id,
            'delete_originals' => delete_originals
          },
          solid_method: solid_method
        )
      end
    end

    def create_mortise_tenon(mortise_id:, tenon_id:, **joint)
      model = active_model
      require_joinery_entities(model, mortise_id, tenon_id)
      mutate('create_mortise_tenon', model) do
        plain_result(
          @commands.create_mortise_tenon(
            mortise_id: mortise_id, tenon_id: tenon_id, **joint
          )
        )
      end
    end

    def create_dovetail(tail_id:, pin_id:, **joint)
      model = active_model
      require_joinery_entities(model, tail_id, pin_id)
      mutate('create_dovetail', model) do
        plain_result(
          @commands.create_dovetail(tail_id: tail_id, pin_id: pin_id, **joint)
        )
      end
    end

    def create_finger_joint(board1_id:, board2_id:, **joint)
      model = active_model
      require_joinery_entities(model, board1_id, board2_id)
      mutate('create_finger_joint', model) do
        plain_result(
          @commands.create_finger_joint(
            board1_id: board1_id, board2_id: board2_id, **joint
          )
        )
      end
    end

    def eval_ruby(code:)
      mutate('eval_ruby') { plain_result(@commands.eval_ruby(code: code)) }
    end

    def execute(name, arguments)
      raise UnknownCommand, "Unknown command: #{name}" unless @commands.command?(name)

      command_result(name, arguments)
    end

    def list_resources
      @commands.list_resources
    end

    private

    def mutate(name, model = active_model)
      started = false
      finalized = false
      model.start_operation(OPERATION_NAMES.fetch(name), true)
      started = true
      result = yield
      model.commit_operation
      finalized = true
      result
    rescue StandardError, ScriptError
      model.abort_operation if started
      finalized = true
      raise
    ensure
      model.abort_operation if started && !finalized
    end

    def active_model
      @model.respond_to?(:call) ? @model.call : @model
    end

    def require_entity(model, id)
      entity = model.find_entity_by_id(id)
      raise "Entity not found: #{id}" unless entity

      entity
    end

    def require_joinery_entities(model, *ids)
      entities = ids.map do |id|
        entity = require_entity(model, id)
        supported = entity.respond_to?(:manifold?) && entity.manifold? &&
                    entity.respond_to?(:subtract) && entity.respond_to?(:union)
        next entity if supported

        raise CommandExecutionError.new(
          'Joinery requires solid groups or component instances',
          kind: 'unsupported_entity',
          details: { entity_id: id }
        )
      end
      parents = entities.map do |entity|
        entity.parent if entity.respond_to?(:parent)
      end.compact
      if parents.length == entities.length && parents.uniq.length > 1
        raise CommandExecutionError.new(
          'Joinery entities must share a modeling context',
          kind: 'incompatible_entity_context'
        )
      end
      entities
    rescue CommandExecutionError
      raise
    rescue StandardError
      raise CommandExecutionError.new(
        'Joinery entity was not found',
        kind: 'entity_not_found'
      )
    end

    def require_material(model, material)
      return if material.match?(/\A#[0-9a-fA-F]{6}\z/)
      return if COMMON_COLORS.include?(material.downcase)
      return if model.materials[material]

      raise "Material not found: #{material}"
    end

    def command_result(name, arguments, solid_method: nil)
      result = if solid_method
                 @commands.call(name, arguments, solid_method: solid_method)
               else
                 @commands.call(name, arguments)
               end
      plain_result(result)
    end

    def plain_result(result)
      success = result.key?(:success) ? result[:success] : result['success']
      raise 'Operation failed' unless success

      result.reject { |key, _value| key.to_s == 'success' }
    end
  end
end
