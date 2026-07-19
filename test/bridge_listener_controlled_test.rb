require 'json'

require_relative '../su_mcp/bridge_listener'
require_relative 'headless'
require_relative 'support/controlled_bridge'


class BridgeListenerControlledTest
  include HeadlessTest::Assertions

  def teardown
    @listeners&.each(&:stop)
  end

  def test_controlled_listener_lifecycle_is_observable_without_tcp
    server = ControlledBridgeListeningSocket.new
    transport = ControlledBridgeTransport.new(
      listening_socket: server,
      listener_ready: false
    )
    listener = build_listener(transport: transport)

    assert_raises(IOError) { listener.address }
    assert_raises(IOError) { listener.poll }
    assert_equal listener, listener.start
    assert_equal listener, listener.start
    assert_equal '127.0.0.1', listener.address.ip_address
    assert_equal false, listener.poll(timeout: 0.5)
    assert_equal 0, listener.drain(limit: 0)
    assert_equal nil, listener.stop
    assert_equal nil, listener.stop

    assert_equal ['127.0.0.1', 0], transport.listened_on
    assert_equal true, server.closed?
  end

  def test_controlled_port_collision_has_the_explicit_role_error
    collision = Errno::EADDRINUSE.new('controlled collision')
    transport = ControlledBridgeTransport.new(
      listening_socket: ControlledBridgeListeningSocket.new,
      listen_error: collision
    )
    listener = build_listener(transport: transport)

    error = assert_raises(SU_MCP::BridgeListener::PortInUseError) { listener.start }

    assert_includes error.message, 'Bridge listener cannot bind 127.0.0.1:0'
    assert_includes error.message, 'port is already in use'
    assert_equal false, listener.running?
  end

  def test_controlled_accept_race_returns_false
    server = ControlledBridgeListeningSocket.new(
      accept_error: IO::EAGAINWaitReadable.new
    )
    listener = build_listener(
      transport: ControlledBridgeTransport.new(listening_socket: server)
    )
    listener.start

    assert_equal false, listener.poll
  end

  def test_wait_readable_then_partial_writes_complete_one_exchange
    request = JSON.generate(jsonrpc: '2.0', method: 'tools/call', id: 91) + "\n"
    client = ScriptedBridgeClient.new(
      reads: [:wait_readable, request],
      writes: [5]
    )
    server = ControlledBridgeListeningSocket.new(clients: [client])
    transport = ControlledBridgeTransport.new(
      listening_socket: server,
      client_waits: { read: [true] }
    )
    listener = build_listener(
      transport: transport,
      handler: ->(parsed) { { jsonrpc: '2.0', result: { ok: true }, id: parsed['id'] } }
    )
    listener.start

    assert_equal true, listener.poll
    drain_exactly(listener, 1)
    wait_until { client.closed? }
    response = JSON.parse(client.response)

    assert_equal({ 'ok' => true }, response['result'])
    assert_equal 91, response['id']
  end

  def test_unexpected_read_and_close_errors_are_role_logged
    messages = []
    client = ScriptedBridgeClient.new(
      reads: [RuntimeError.new('controlled read failure')],
      close_error: IOError.new('controlled close failure')
    )
    listener = build_listener(
      transport: ControlledBridgeTransport.new(
        listening_socket: ControlledBridgeListeningSocket.new(clients: [client])
      ),
      logger: ->(message) { messages << message }
    )
    listener.start

    listener.poll
    drain_exactly(listener, 1)

    assert_equal [
      'Bridge listener: listening on 127.0.0.1:23456',
      'Bridge listener: I/O error: controlled read failure'
    ], messages
  end

  def test_parse_error_write_failure_is_logged_and_already_closed_client_is_safe
    messages = []
    client = ScriptedBridgeClient.new(
      reads: ["{nope}\n"],
      writes: [RuntimeError.new('controlled parse write failure')],
      closed: true
    )
    listener = build_listener(
      transport: ControlledBridgeTransport.new(
        listening_socket: ControlledBridgeListeningSocket.new(clients: [client])
      ),
      logger: ->(message) { messages << message }
    )
    listener.start

    listener.poll
    drain_exactly(listener, 2)

    assert_equal [
      'Bridge listener: listening on 127.0.0.1:23456',
      'Bridge listener: rejected malformed JSON',
      'Bridge listener: I/O error: controlled parse write failure'
    ], messages
  end

  def test_stop_terminates_a_worker_blocked_on_controlled_input
    client = ScriptedBridgeClient.new(reads: [:wait_readable])
    server = ControlledBridgeListeningSocket.new(clients: [client])
    transport = ControlledBridgeTransport.new(
      listening_socket: server,
      block_direction: :read
    )
    listener = build_listener(
      transport: transport
    )
    listener.start
    listener.poll
    wait_until { transport.client_wait_entered? }

    listener.stop

    assert_equal true, client.closed?
    assert_equal true, server.closed?
  end

  private

  def build_listener(transport:, handler: ->(_request) { {} }, logger: nil)
    @listeners ||= []
    listener = SU_MCP::BridgeListener.new(
      port: 0,
      handler: handler,
      logger: logger,
      transport: transport
    )
    @listeners << listener
    listener
  end

  def drain_exactly(listener, expected)
    drained = 0
    wait_until do
      drained += listener.drain
      drained >= expected
    end
    assert_equal expected, drained
  end
end


HeadlessTest.run(BridgeListenerControlledTest)
