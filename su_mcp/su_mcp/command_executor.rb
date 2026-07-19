require_relative 'command_catalog'


module SU_MCP
  class CommandExecutor
    Execution = Struct.new(:command, :result, keyword_init: true)

    def initialize(sketchup:, catalog: CommandCatalog.new)
      @sketchup = sketchup
      @catalog = catalog
    end

    def call(name, arguments)
      command = @catalog.command(name)
      command_name = command.name
      raise UnknownCommand, "Unknown command: #{name}" unless @sketchup.respond_to?(command_name)

      normalized = @catalog.validate(command, arguments)
      Execution.new(
        command: command,
        result: @sketchup.public_send(command_name, **keywords(normalized))
      )
    end

    private

    def keywords(arguments)
      arguments.transform_keys(&:to_sym)
    end
  end
end
