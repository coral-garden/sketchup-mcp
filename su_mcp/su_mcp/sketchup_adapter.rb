module SU_MCP
  class SketchupAdapter
    def initialize(commands:)
      @commands = commands
    end

    def create_component(type:, position:, dimensions:)
      result = @commands.call(
        'create_component',
        'type' => type,
        'position' => position,
        'dimensions' => dimensions
      )
      result.fetch(:id)
    end

    def execute(name, arguments)
      raise UnknownCommand, "Unknown command: #{name}" unless @commands.command?(name)

      result = @commands.call(name, arguments)
      raise 'Operation failed' unless result[:success]

      result.reject { |key, _value| key == :success }
    end

    def list_resources
      @commands.list_resources
    end
  end
end
