require 'json'
require 'socket'

require_relative '../su_mcp/su_mcp/bridge_listener'


class BridgeListenerTest
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

  private

  def assert_equal(expected, actual)
    return if expected == actual

    raise "Expected #{expected.inspect}, got #{actual.inspect}"
  end

  def assert_includes(value, fragment)
    return if value.include?(fragment)

    raise "Expected #{value.inspect} to include #{fragment.inspect}"
  end

  def assert_raises(error_class)
    yield
    raise "Expected #{error_class} to be raised"
  rescue error_class => error
    error
  end

  def exchange(listener, request)
    raw_exchange(listener, JSON.generate(request) + "\n")
  end

  def raw_exchange(listener, request_frame, close_write: false)
    client = TCPSocket.new('127.0.0.1', listener.port)
    client.write(request_frame)
    client.flush
    client.close_write if close_write
    listener.poll(timeout: 1)
    response_frame = client.gets
    eof = client.read
    [JSON.parse(response_frame), response_frame, eof]
  ensure
    client&.close
  end

  def start_listener(port: 0, &handler)
    @listeners ||= []
    listener = SU_MCP::BridgeListener.new(
      port: port,
      handler: handler || ->(request) {
        { jsonrpc: '2.0', result: {}, id: request['id'] }
      }
    )
    listener.start
    @listeners << listener
    listener
  end
end


failures = []
tests = BridgeListenerTest.instance_methods(false).grep(/^test_/).sort
tests.each do |test_name|
  test = BridgeListenerTest.new
  begin
    test.public_send(test_name)
    print '.'
  rescue StandardError => error
    print 'F'
    failures << [test_name, error]
  ensure
    test.teardown
  end
end
puts
failures.each do |test_name, error|
  warn "#{test_name}: #{error.class}: #{error.message}"
  warn error.backtrace.join("\n")
end
puts "#{tests.length} tests, #{failures.length} failures"
exit(failures.empty? ? 0 : 1)
