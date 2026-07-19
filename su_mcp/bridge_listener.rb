require 'thread'
require_relative 'bridge_protocol'
require_relative 'socket_transport'


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
      @logger = logger || ->(_message) {}
      @protocol = BridgeProtocol.new(handler: handler, logger: @logger)
      @io_timeout = io_timeout
      @transport = transport
      @listening_socket = nil
      @ready = Queue.new
      @clients = []
      @workers = []
      @connections_lock = Mutex.new
    end

    def start
      return self if running?

      @listening_socket = @transport.listen(HOST, @port)
      @port = @listening_socket.local_address.ip_port
      @logger.call("Bridge listener: listening on #{HOST}:#{@port}")
      self
    rescue Errno::EADDRINUSE => error
      @listening_socket = nil
      raise PortInUseError,
            "Bridge listener cannot bind #{HOST}:#{@port}; port is already in use: #{error.message}"
    end

    def stop
      @listening_socket&.close
      @listening_socket = nil

      clients, workers = @connections_lock.synchronize do
        [@clients.dup, @workers.dup]
      end
      clients.each { |client| close(client) }
      workers.each(&:kill)
      workers.each(&:join)
      @connections_lock.synchronize do
        @clients.clear
        @workers.clear
      end
      clear_ready_requests
      nil
    end

    def running?
      !@listening_socket.nil? && !@listening_socket.closed?
    end

    def address
      raise IOError, 'bridge listener is not running' unless running?

      @listening_socket.local_address
    end

    def poll(timeout: 0)
      raise IOError, 'bridge listener is not running' unless running?
      return false unless @transport.wait(@listening_socket, :read, timeout)

      client = @listening_socket.accept_nonblock
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
          item.response_queue << @protocol.response_frame(item.request)
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
      request = @protocol.decode(read_frame(client))
      response_queue = Queue.new
      @ready << WorkItem.new(request: request, response_queue: response_queue)
      write_frame(client, response_queue.pop)
    rescue JSON::ParserError, IncompleteFrameError => error
      enqueue_log('Bridge listener: rejected malformed JSON')
      write_parse_error(client)
    rescue IOTimeoutError => error
      enqueue_log("Bridge listener: I/O error: #{error.message}")
    rescue StandardError => error
      enqueue_log("Bridge listener: I/O error: #{error.message}")
    ensure
      close(client)
      @connections_lock.synchronize do
        @clients.delete(client)
        @workers.delete(Thread.current)
      end
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

    def write_frame(client, bytes)
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
      write_frame(client, @protocol.parse_error_frame)
    rescue StandardError => error
      enqueue_log("Bridge listener: I/O error: #{error.message}")
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
