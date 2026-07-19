require 'json'

require_relative '../su_mcp/bridge_protocol'
require_relative 'headless'


class BridgeProtocolHeadlessTest
  include HeadlessTest::Assertions

  def test_valid_frame_is_decoded_and_success_is_newline_encoded
    protocol = SU_MCP::BridgeProtocol.new(
      handler: ->(request) {
        { jsonrpc: '2.0', result: { command: request['command'] }, id: request['id'] }
      }
    )
    request = protocol.decode("{\"command\":\"get_selection\",\"id\":17}\n")

    response_frame = protocol.response_frame(request)

    assert_equal({ 'command' => 'get_selection', 'id' => 17 }, request)
    assert_equal "\n", response_frame[-1]
    assert_equal({ 'command' => 'get_selection' }, JSON.parse(response_frame)['result'])
    assert_equal 17, JSON.parse(response_frame)['id']
  end

  def test_malformed_frame_has_the_protocol_parse_error
    protocol = SU_MCP::BridgeProtocol.new(handler: ->(_request) { {} })

    assert_raises(JSON::ParserError) { protocol.decode("{nope}\n") }
    response = JSON.parse(protocol.parse_error_frame)

    assert_equal '2.0', response['jsonrpc']
    assert_equal(-32_700, response.dig('error', 'code'))
    assert_equal 'Parse error', response.dig('error', 'message')
    assert_equal nil, response['id']
  end

  def test_dispatch_failure_is_final_and_preserves_hash_request_id
    messages = []
    protocol = SU_MCP::BridgeProtocol.new(
      handler: ->(_request) { raise 'controlled dispatch failure' },
      logger: ->(message) { messages << message }
    )

    response = JSON.parse(protocol.response_frame('id' => 'failed-18'))

    assert_equal(-32_603, response.dig('error', 'code'))
    assert_equal 'controlled dispatch failure', response.dig('error', 'message')
    assert_equal 'failed-18', response['id']
    assert_equal ['Bridge listener: command dispatch failed'], messages
  end

  def test_dispatch_failure_for_a_non_object_request_has_a_null_id
    protocol = SU_MCP::BridgeProtocol.new(
      handler: ->(_request) { raise 'controlled dispatch failure' }
    )

    response = JSON.parse(protocol.response_frame([]))

    assert_equal nil, response['id']
  end
end


HeadlessTest.run(BridgeProtocolHeadlessTest)
