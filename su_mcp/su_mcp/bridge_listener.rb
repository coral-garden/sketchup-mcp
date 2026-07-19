require 'json'
require 'socket'


module SU_MCP
  class BridgeListener
    HOST = '127.0.0.1'.freeze
    DEFAULT_PORT = 9876
    PORT_ENV = 'SKETCHUP_MCP_BRIDGE_PORT'.freeze

    class PortInUseError < StandardError; end

    attr_reader :port

    def self.port_from_environment
      Integer(ENV.fetch(PORT_ENV, DEFAULT_PORT.to_s), 10)
    end

    def initialize(port: self.class.port_from_environment, handler:, logger: nil)
      @port = port
      @handler = handler
      @logger = logger || ->(_message) {}
      @server = nil
    end

    def start
      return self if running?

      @server = TCPServer.new(HOST, @port)
      @port = @server.local_address.ip_port
      @logger.call("Bridge listening on #{HOST}:#{@port}")
      self
    rescue Errno::EADDRINUSE => error
      @server = nil
      raise PortInUseError,
            "SketchUp bridge cannot bind #{HOST}:#{@port}; port is already in use: #{error.message}"
    end

    def stop
      @server&.close
      @server = nil
    end

    def running?
      !@server.nil? && !@server.closed?
    end

    def address
      raise IOError, 'bridge listener is not running' unless running?

      @server.local_address
    end

    def poll(timeout: 0)
      raise IOError, 'bridge listener is not running' unless running?
      return false unless IO.select([@server], nil, nil, timeout)

      client = @server.accept_nonblock
      process(client)
      true
    rescue IO::WaitReadable
      false
    ensure
      client&.close
    end

    private

    def process(client)
      request = nil
      frame = client.gets
      return unless frame
      unless frame.end_with?("\n")
        raise JSON::ParserError, 'request frame must end with newline'
      end

      request = JSON.parse(frame)
      write_response(client, @handler.call(request))
    rescue JSON::ParserError => error
      @logger.call("Bridge parse error: #{error.message}")
      write_response(
        client,
        jsonrpc: '2.0', error: { code: -32_700, message: 'Parse error' }, id: nil
      )
    rescue StandardError => error
      @logger.call("Bridge request error: #{error.message}")
      id = request.is_a?(Hash) ? request['id'] : nil
      write_response(
        client,
        jsonrpc: '2.0', error: { code: -32_603, message: error.message }, id: id
      )
    end

    def write_response(client, response)
      client.write(JSON.generate(response) + "\n")
      client.flush
    end
  end
end
