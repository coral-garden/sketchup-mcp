module SU_MCP
  class InvalidArguments < StandardError; end
  class UnknownCommand < StandardError; end

  class CommandExecutor
    COMPONENT_TYPES = %w[cube cylinder sphere cone].freeze
    RENAMED_COMMANDS = {
      'export' => 'export_scene'
    }.freeze

    def initialize(sketchup:)
      @sketchup = sketchup
    end

    def call(name, arguments)
      canonical_name = RENAMED_COMMANDS.fetch(name, name)

      case canonical_name
      when 'create_component'
        create_component(arguments)
      else
        @sketchup.execute(canonical_name, arguments)
      end
    end

    private

    def create_component(arguments)
      unless arguments.is_a?(Hash)
        raise InvalidArguments, 'arguments must be an object'
      end

      component_type = arguments.fetch('type', 'cube')
      unless COMPONENT_TYPES.include?(component_type)
        raise InvalidArguments,
              "type must be one of: #{COMPONENT_TYPES.join(', ')}"
      end


      position = vector(arguments.fetch('position', [0, 0, 0]), 'position')
      dimensions = vector(arguments.fetch('dimensions', [1, 1, 1]), 'dimensions')

      id = @sketchup.create_component(
        type: component_type,
        position: position,
        dimensions: dimensions
      )
      { id: id }
    end

    def vector(value, name)
      valid = value.is_a?(Array) && value.length == 3 && value.all? do |number|
        number.is_a?(Numeric) && (!number.respond_to?(:finite?) || number.finite?)
      end
      raise InvalidArguments, "#{name} must contain exactly three numbers" unless valid

      value
    end
  end
end
