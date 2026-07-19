require 'json'


module SU_MCP
  class CommandDispatcher
    def initialize(executor:, resources: nil)
      @executor = executor
      @resources = resources || -> { [] }
    end

    def call(request)
      return error_response(-32_600, 'Invalid Request', nil) unless request.is_a?(Hash)
      if request.key?('jsonrpc') && request['jsonrpc'] != '2.0'
        return error_response(-32_600, 'Invalid Request', request['id'])
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
        error_response(-32_601, 'Method not found', request['id'])
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

      command_result = @executor.call(command, arguments)
      id = command_result[:id]

      success_response(
        {
          content: [{ type: 'text', text: JSON.generate(command_result) }],
          isError: false,
          success: true,
          resourceId: id
        },
        request['id']
      )
    rescue InvalidArguments => error
      error_response(-32_602, error.message, request['id'])
    rescue UnknownCommand => error
      error_response(-32_601, error.message, request['id'])
    rescue StandardError => error
      error_response(-32_603, error.message, request['id'])
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

    def error_response(code, message, id)
      {
        jsonrpc: '2.0',
        error: { code: code, message: message, data: { success: false } },
        id: id
      }
    end
  end
end
