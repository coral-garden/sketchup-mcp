require 'json'
require_relative 'command_catalog'
require_relative 'command_execution_error'
require_relative 'command_response_builder'


module SU_MCP
  class CommandDispatcher
    def initialize(executor:, resources: nil, catalog: CommandCatalog.new)
      @executor = executor
      @resources = resources || -> { [] }
      @responses = CommandResponseBuilder.new(catalog: catalog)
    end

    def call(request)
      return @responses.error(-32_600, message: 'Invalid Request', id: nil) unless request.is_a?(Hash)
      if request.key?('jsonrpc') && request['jsonrpc'] != '2.0'
        return @responses.error(-32_600, message: 'Invalid Request', id: request['id'])
      end

      request = legacy_request(request) if request['command']
      case request['method']
      when 'tools/call'
        dispatch_command(request)
      when 'resources/list'
        success_response({ resources: @resources.call, success: true }, request['id'])
      when 'prompts/list'
        success_response({ prompts: [], success: true }, request['id'])
      else
        @responses.error(-32_601, message: 'Method not found', id: request['id'])
      end
    end

    private

    def dispatch_command(request)
      params = request['params']
      raise InvalidArguments, 'params must be an object' unless params.is_a?(Hash)

      command = params['name']
      raise InvalidArguments, 'name must be a string' unless command.is_a?(String)

      arguments = params.fetch('arguments', {})
      raise InvalidArguments, 'arguments must be an object' unless arguments.is_a?(Hash)

      execution = @executor.call(command, arguments)
      @responses.success(
        command: execution.command,
        result: execution.result,
        id: request['id']
      )
    rescue InvalidArguments => error
      @responses.failure('invalid_arguments', message: error.message, id: request['id'])
    rescue UnknownCommand => error
      @responses.error(-32_601, message: error.message, id: request['id'])
    rescue StandardError => error
      @responses.execution_failure(command, error: error, id: request['id'])
    end

    def legacy_request(request)
      {
        'jsonrpc' => request.fetch('jsonrpc', '2.0'),
        'method' => 'tools/call',
        'params' => {
          'name' => request['command'],
          'arguments' => request['parameters'] || {}
        },
        'id' => request['id']
      }
    end

    def success_response(result, id)
      { jsonrpc: '2.0', result: result, id: id }
    end
  end
end
