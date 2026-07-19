require_relative 'command_catalog'


module SU_MCP
  class CommandExecutor
    Execution = Struct.new(:command, :result, keyword_init: true)

    def initialize(adapter:, catalog: CommandCatalog.new, logger: nil)
      @adapter = adapter
      @catalog = catalog
      @logger = logger || ->(_message) {}
    end

    def call(name, arguments)
      command = @catalog.command(name)
      command_name = command.name
      @logger.call("Command executor: command started: #{command_name}")
      raise UnknownCommand, "Unknown command: #{name}" unless @adapter.respond_to?(command_name)

      normalized = @catalog.validate(command, arguments)
      execution = Execution.new(
        command: command,
        result: @adapter.public_send(command_name, **keywords(normalized))
      )
      @logger.call("Command executor: command completed: #{command_name}")
      execution
    rescue StandardError, ScriptError => error
      @logger.call("Command executor: command failed: #{name}: #{error.class}")
      raise
    end

    private

    def keywords(arguments)
      arguments.transform_keys(&:to_sym)
    end
  end
end
