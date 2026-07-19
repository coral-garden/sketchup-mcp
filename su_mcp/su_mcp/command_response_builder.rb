require 'json'
require_relative 'command_catalog'
require_relative 'command_execution_error'


module SU_MCP
  class CommandResponseBuilder
    def initialize(catalog: CommandCatalog.new)
      @catalog = catalog
    end

    def success(command:, result:, id:)
      envelope = {
        content: [{ type: 'text', text: JSON.generate(result) }],
        isError: false,
        success: true
      }
      resource_id = command.resource_id(result)
      envelope[:resourceId] = resource_id unless resource_id.nil?
      { jsonrpc: '2.0', result: envelope, id: id }
    end

    def failure(semantic, message:, id:, data: {})
      error(
        @catalog.failure_code(semantic),
        message: message,
        id: id,
        data: data
      )
    end

    def execution_failure(command_name, error:, id:)
      command = @catalog.command(command_name)
      if error.is_a?(CommandExecutionError)
        message = error.message
        data = { type: error.kind, command: command.name }.merge(error.details)
      else
        profile = command.execution_error
        message = profile ? profile.fetch('message') : error.message
        data = profile ? { type: profile.fetch('type'), command: command.name } : {}
      end
      failure('execution_error', message: message, id: id, data: data)
    end

    def error(code, message:, id:, data: {})
      {
        jsonrpc: '2.0',
        error: {
          code: code,
          message: message,
          data: { success: false }.merge(data)
        },
        id: id
      }
    end
  end
end
