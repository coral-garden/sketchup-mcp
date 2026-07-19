require_relative 'command_catalog'


module SU_MCP
  class CommandExecutor
    Outcome = Struct.new(:result, :resource_id, keyword_init: true)

    def initialize(sketchup:, catalog: CommandCatalog.new)
      @sketchup = sketchup
      @catalog = catalog
    end

    def call(name, arguments)
      command = @catalog.command(name)
      result = execute(command.fetch('name'), @catalog.validate(command, arguments))
      Outcome.new(
        result: result,
        resource_id: @catalog.resource_id(command, result)
      )
    end

    private

    def execute(name, arguments)
      case name
      when 'create_component'
        { id: @sketchup.create_component(**keywords(arguments)) }
      when 'delete_component'
        @sketchup.delete_component(**keywords(arguments))
      when 'transform_component'
        @sketchup.transform_component(**keywords(arguments))
      when 'get_selection'
        @sketchup.get_selection
      when 'set_material'
        validate_material(arguments.fetch('material'))
        @sketchup.set_material(**keywords(arguments))
      when 'export_scene'
        @sketchup.export_scene(**keywords(arguments))
      when 'boolean_operation'
        validate_boolean_entities(arguments)
        @sketchup.boolean_operation(**keywords(arguments))
      when 'create_mortise_tenon'
        @sketchup.create_mortise_tenon(**keywords(arguments))
      when 'create_dovetail'
        @sketchup.create_dovetail(**keywords(arguments))
      when 'create_finger_joint'
        @sketchup.create_finger_joint(**keywords(arguments))
      when 'eval_ruby'
        @sketchup.eval_ruby(**keywords(arguments))
      else
        @sketchup.execute(name, arguments)
      end
    end

    def keywords(arguments)
      arguments.transform_keys(&:to_sym)
    end

    def validate_material(material)
      if material.start_with?('#') && !material.match?(/\A#[0-9a-fA-F]{6}\z/)
        raise InvalidArguments, 'material hexadecimal colors must use #RRGGBB'
      end
    end

    def validate_boolean_entities(arguments)
      return unless arguments.fetch('target_id') == arguments.fetch('tool_id')

      raise InvalidArguments, 'target_id and tool_id must identify different entities'
    end
  end
end
