require 'json'
require 'socket'

require_relative '../su_mcp/bridge_listener'
require_relative 'headless'


class ControlledClock
  def initialize
    @now = 0.0
  end

  def call
    @now
  end

  def advance(seconds)
    @now += seconds
  end
end


class StalledWriteClient
  attr_reader :write_attempts

  def initialize(request_frame)
    @request_frame = request_frame
    @write_attempts = 0
    @closed = false
  end

  def read_nonblock(_length, exception:)
    frame = @request_frame
    @request_frame = nil
    frame
  end

  def write_nonblock(_bytes, exception:)
    @write_attempts += 1
    :wait_writable
  end

  def close
    @closed = true
  end

  def closed?
    @closed
  end
end


class IncompleteFrameClient
  attr_reader :response

  def initialize(request_fragment)
    @request_fragment = request_fragment
    @closed = false
    @response = +''
  end

  def read_nonblock(_length, exception:)
    fragment = @request_fragment
    @request_fragment = nil
    fragment
  end

  def write_nonblock(bytes, exception:)
    @response << bytes
    bytes.bytesize
  end

  def close
    @closed = true
  end

  def closed?
    @closed
  end
end


class ControlledServerSocket
  Address = Struct.new(:ip_port, :ip_address)

  def initialize(client)
    @client = client
    @closed = false
  end

  def local_address
    Address.new(12_345, '127.0.0.1')
  end

  def accept_nonblock
    client = @client
    @client = nil
    client
  end

  def close
    @closed = true
  end

  def closed?
    @closed
  end
end


class ControlledSocketTransport
  def initialize(listening_socket:, clock: ControlledClock.new, stall_writes: false)
    @listening_socket = listening_socket
    @clock = clock
    @stall_writes = stall_writes
  end

  def listen(host, port)
    @listened_on = [host, port]
    @listening_socket
  end

  def now
    @clock.call
  end

  def wait(socket, direction, timeout)
    return true if socket.equal?(@listening_socket)
    return true unless @stall_writes && direction == :write

    @clock.advance(timeout)
    false
  end
end


class BridgeListenerTest
  include HeadlessTest::Assertions

  def teardown
    @listeners&.each(&:stop)
  end

  def test_listener_binds_only_to_ipv4_loopback
    listener = start_listener

    assert_equal '127.0.0.1', listener.address.ip_address
  end

  def test_one_newline_framed_request_is_answered_then_connection_closes
    listener = start_listener do |request|
      {
        jsonrpc: '2.0',
        result: { 'seen' => request.dig('params', 'name') },
        id: request['id']
      }
    end

    first_response, first_frame, first_eof = exchange(
      listener,
      { jsonrpc: '2.0', method: 'tools/call', params: { name: 'first' }, id: 17 }
    )
    second_response, = exchange(
      listener,
      { jsonrpc: '2.0', method: 'tools/call', params: { name: 'second' }, id: 'r-18' }
    )

    assert_equal({ 'seen' => 'first' }, first_response['result'])
    assert_equal 17, first_response['id']
    assert_equal "\n", first_frame[-1]
    assert_equal '', first_eof
    assert_equal({ 'seen' => 'second' }, second_response['result'])
    assert_equal 'r-18', second_response['id']
  end

  def test_malformed_json_returns_parse_error_with_null_id
    listener = start_listener

    response, = raw_exchange(listener, "{nope}\n")

    assert_equal '2.0', response['jsonrpc']
    assert_equal(-32_700, response.dig('error', 'code'))
    assert_equal 'Parse error', response.dig('error', 'message')
    assert_equal nil, response['id']
  end

  def test_eof_does_not_replace_the_required_request_newline
    listener = start_listener

    response, = raw_exchange(
      listener,
      JSON.generate(jsonrpc: '2.0', method: 'tools/call', id: 18),
      close_write: true
    )

    assert_equal(-32_700, response.dig('error', 'code'))
    assert_equal nil, response['id']
  end

  def test_handler_jsonrpc_error_is_returned_without_rewriting_it
    listener = start_listener do |request|
      {
        jsonrpc: '2.0',
        error: { code: -32_603, message: 'operation failed', data: { retryable: false } },
        id: request['id']
      }
    end

    response, = exchange(
      listener,
      { jsonrpc: '2.0', method: 'tools/call', params: {}, id: 19 }
    )

    assert_equal(-32_603, response.dig('error', 'code'))
    assert_equal 'operation failed', response.dig('error', 'message')
    assert_equal false, response.dig('error', 'data', 'retryable')
    assert_equal 19, response['id']
  end

  def test_port_collision_raises_an_explicit_startup_error
    first = start_listener
    second = SU_MCP::BridgeListener.new(port: first.port, handler: ->(_request) { {} })

    error = assert_raises(SU_MCP::BridgeListener::PortInUseError) { second.start }

    assert_includes error.message, "127.0.0.1:#{first.port}"
    assert_includes error.message, 'port is already in use'
  end

  def test_port_comes_from_the_shared_environment_variable
    previous = ENV['SKETCHUP_MCP_BRIDGE_PORT']
    ENV['SKETCHUP_MCP_BRIDGE_PORT'] = '12345'

    assert_equal 12_345, SU_MCP::BridgeListener.port_from_environment
  ensure
    ENV['SKETCHUP_MCP_BRIDGE_PORT'] = previous
  end

  def test_accepting_a_silent_client_never_blocks_the_polling_thread
    listener = start_listener(io_timeout: 0.05)
    client = TCPSocket.new('127.0.0.1', listener.port)

    started_at = Process.clock_gettime(Process::CLOCK_MONOTONIC)
    accepted = listener.poll(timeout: 1)
    elapsed = Process.clock_gettime(Process::CLOCK_MONOTONIC) - started_at

    assert_equal true, accepted
    assert_operator elapsed, :<, 0.04
  ensure
    client&.close
  end

  def test_stalled_response_write_times_out_off_the_polling_thread
    clock = ControlledClock.new
    client = StalledWriteClient.new(
      JSON.generate(jsonrpc: '2.0', method: 'tools/call', id: 54) + "\n"
    )
    listening_socket = ControlledServerSocket.new(client)
    messages = []
    logging_threads = []
    listener = SU_MCP::BridgeListener.new(
      port: 0,
      handler: ->(request) { { jsonrpc: '2.0', result: {}, id: request['id'] } },
      io_timeout: 0.25,
      transport: ControlledSocketTransport.new(
        listening_socket: listening_socket,
        clock: clock,
        stall_writes: true
      ),
      logger: ->(message) {
        messages << message
        logging_threads << Thread.current
      }
    )
    @listeners ||= []
    @listeners << listener
    listener.start

    assert_equal true, listener.poll
    wait_until { listener.drain == 1 }
    wait_until { client.closed? }
    listener.drain

    assert_equal 1, client.write_attempts
    assert_includes messages.join("\n"), 'Bridge listener: I/O error: write timed out'
    assert_equal [Thread.current], logging_threads.uniq
  end

  def test_half_closed_request_without_newline_is_rejected_by_the_io_worker
    client = IncompleteFrameClient.new('{"jsonrpc":"2.0","id":56}')
    listening_socket = ControlledServerSocket.new(client)
    handler_calls = 0
    listener = SU_MCP::BridgeListener.new(
      port: 0,
      handler: ->(_request) { handler_calls += 1 },
      transport: ControlledSocketTransport.new(listening_socket: listening_socket)
    )
    @listeners ||= []
    @listeners << listener
    listener.start

    assert_equal true, listener.poll
    wait_until { client.closed? }
    response = JSON.parse(client.response)

    assert_equal(-32_700, response.dig('error', 'code'))
    assert_equal nil, response['id']
    assert_equal 0, handler_calls
  end

  private

  def exchange(listener, request)
    raw_exchange(listener, JSON.generate(request) + "\n")
  end

  def raw_exchange(listener, request_frame, close_write: false)
    client = TCPSocket.new('127.0.0.1', listener.port)
    client.write(request_frame)
    client.flush
    client.close_write if close_write
    listener.poll(timeout: 1)
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 1
    until IO.select([client], nil, nil, 0.001)
      listener.drain
      if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
        raise 'listener did not produce a response'
      end
    end
    response_frame = client.gets
    eof = client.read
    [JSON.parse(response_frame), response_frame, eof]
  ensure
    client&.close
  end

  def start_listener(port: 0, io_timeout: 1, &handler)
    @listeners ||= []
    listener = SU_MCP::BridgeListener.new(
      port: port,
      io_timeout: io_timeout,
      handler: handler || ->(request) {
        { jsonrpc: '2.0', result: {}, id: request['id'] }
      }
    )
    listener.start
    @listeners << listener
    listener
  end


end


HeadlessTest.run(BridgeListenerTest)
