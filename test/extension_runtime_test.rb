require_relative '../su_mcp/su_mcp/extension_runtime'
require_relative 'headless'


class ExtensionRuntimeScheduler
  attr_reader :interval

  def every(interval, &_task)
    @interval = interval
    :extension_runtime_timer
  end

  def cancel(_timer); end
end


class ExtensionRuntimeTest
  include HeadlessTest::Assertions

  def teardown
    @extension_runtime&.stop
  end

  def test_role_correct_runtime_starts_and_stops_the_bridge_listener
    messages = []
    scheduler = ExtensionRuntimeScheduler.new
    @extension_runtime = SU_MCP::ExtensionRuntime.new(
      port: 0,
      scheduler: scheduler,
      logger: ->(message) { messages << message }
    )

    assert_equal @extension_runtime, @extension_runtime.start
    assert_equal SU_MCP::BridgeRuntime::POLL_INTERVAL, scheduler.interval
    assert_equal @extension_runtime, @extension_runtime.stop
    assert_equal false, SU_MCP.const_defined?(:Server, false)
    assert_equal true, messages.any? { |message| message.start_with?('Bridge listener:') }
    assert_equal true, messages.include?('Extension runtime: bridge started')
    assert_equal true, messages.include?('Extension runtime: bridge stopped')
  end
end


HeadlessTest.run(ExtensionRuntimeTest)
