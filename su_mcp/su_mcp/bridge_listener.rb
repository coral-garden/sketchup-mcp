require 'json'
require 'socket'
require 'thread'


module SU_MCP
  class BridgeListener
    HOST = '127.0.0.1'.freeze
    DEFAULT_PORT = 9876
    DEFAULT_IO_TIMEOUT = 1.0
    MAX_REQUESTS_PER_DRAIN = 16
    PORT_ENV = 'SKETCHUP_MCP_BRIDGE_PORT'.freeze

    WorkItem = Struct.new(:request, :response_queue, keyword_init: true)
    LogItem = Struct.new(:message, keyword_init: true)

    class PortInUseError < StandardError; end
    class IncompleteFrameError < StandardError; end
    class IOTimeoutError < StandardError; end

    class SocketTransport
      def listen(host, port)
        TCPServer.new(host, port)
      end

      def now
        Process.clock_gettime(Process::CLOCK_MONOTONIC)
      end

      def wait(socket, direction, timeout)
        readers = direction == :read ? [socket] : nil
        writers = direction == :write ? [socket] : nil
        !IO.select(readers, writers, nil, timeout).nil?
      end
    end

    attr_reader :port

    def self.port_from_environment
      Integer(ENV.fetch(PORT_ENV, DEFAULT_PORT.to_s), 10)
    end

    def initialize(
      port: self.class.port_from_environment,
      handler:,
      logger: nil,
      io_timeout: DEFAULT_IO_TIMEOUT,
      transport: SocketTransport.new
    )
      @port = port
      @handler = handler
      @logger = logger || ->(_message) {}
      @io_timeout = io_timeout
      @transport = transport
      @server = nil
      @ready = Queue.new
      @clients = []
      @workers = []
      @connections_lock = Mutex.new
    end

    def start
      return self if running?

      @server = @transport.listen(HOST, @port)
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

      clients, workers = @connections_lock.synchronize do
        [@clients.dup, @workers.dup]
      end
      clients.each { |client| close(client) }
      workers.each { |worker| worker.kill if worker.alive? }
      workers.each { |worker| worker.join unless worker == Thread.current }
      @connections_lock.synchronize do
        @clients.clear
        @workers.clear
      end
      clear_ready_requests
      nil
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
      return false unless @transport.wait(@server, :read, timeout)

      client = @server.accept_nonblock
      @connections_lock.synchronize do
        @clients << client
        @workers << Thread.new { serve(client) }
      end
      true
    rescue IO::WaitReadable
      false
    end

    def drain(limit: MAX_REQUESTS_PER_DRAIN)
      count = 0
      while count < limit
        item = @ready.pop(true)
        if item.is_a?(WorkItem)
          item.response_queue << handle(item.request)
        else
          @logger.call(item.message)
        end
        count += 1
      end
      count
    rescue ThreadError
      count
    end

    private

    def serve(client)
      request = JSON.parse(read_frame(client))
      response_queue = Queue.new
      @ready << WorkItem.new(request: request, response_queue: response_queue)
      write_frame(client, response_queue.pop)
    rescue JSON::ParserError, IncompleteFrameError => error
      enqueue_log("Bridge parse error: #{error.message}")
      write_parse_error(client)
    rescue IOTimeoutError => error
      enqueue_log("Bridge I/O error: #{error.message}")
    rescue StandardError => error
      enqueue_log("Bridge I/O error: #{error.message}")
    ensure
      close(client)
      @connections_lock.synchronize do
        @clients.delete(client)
        @workers.delete(Thread.current)
      end
    end

    def handle(request)
      @handler.call(request)
    rescue StandardError => error
      @logger.call("Bridge request error: #{error.message}")
      {
        jsonrpc: '2.0',
        error: { code: -32_603, message: error.message },
        id: request.is_a?(Hash) ? request['id'] : nil
      }
    end

    def read_frame(client)
      frame = +''
      deadline = @transport.now + @io_timeout

      loop do
        chunk = client.read_nonblock(4096, exception: false)
        case chunk
        when :wait_readable
          wait_until_ready(client, :read, deadline)
        when nil
          raise IncompleteFrameError, 'request frame must end with newline'
        else
          frame << chunk
          newline = frame.index("\n")
          return frame[0..newline] if newline
        end
      end
    end

    def write_frame(client, response)
      bytes = JSON.generate(response) + "\n"
      offset = 0
      deadline = @transport.now + @io_timeout

      while offset < bytes.bytesize
        written = client.write_nonblock(bytes.byteslice(offset..), exception: false)
        if written == :wait_writable
          wait_until_ready(client, :write, deadline)
        else
          offset += written
        end
      end
    end

    def write_parse_error(client)
      write_frame(
        client,
        jsonrpc: '2.0', error: { code: -32_700, message: 'Parse error' }, id: nil
      )
    rescue StandardError => error
      enqueue_log("Bridge I/O error: #{error.message}")
    end

    def wait_until_ready(client, direction, deadline)
      remaining = deadline - @transport.now
      unless remaining.positive? && @transport.wait(client, direction, remaining)
        raise IOTimeoutError, "#{direction} timed out after #{@io_timeout} seconds"
      end
    end

    def close(socket)
      socket.close unless socket.closed?
    rescue IOError, SystemCallError
      nil
    end

    def enqueue_log(message)
      @ready << LogItem.new(message: message)
    end

    def clear_ready_requests
      @ready.pop(true) while true
    rescue ThreadError
      nil
    end
  end
end
