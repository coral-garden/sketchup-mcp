require 'json'


module SU_MCP
  class BridgeProtocol
    def initialize(handler:, logger: nil)
      @handler = handler
      @logger = logger || ->(_message) {}
    end

    def decode(frame)
      JSON.parse(frame)
    end

    def response_frame(request)
      encode(dispatch(request))
    end

    def parse_error_frame
      encode(
        jsonrpc: '2.0',
        error: { code: -32_700, message: 'Parse error' },
        id: nil
      )
    end

    private

    def dispatch(request)
      @handler.call(request)
    rescue StandardError => error
      @logger.call('Bridge listener: command dispatch failed')
      {
        jsonrpc: '2.0',
        error: { code: -32_603, message: error.message },
        id: request.is_a?(Hash) ? request['id'] : nil
      }
    end

    def encode(response)
      JSON.generate(response) + "\n"
    end
  end
end
