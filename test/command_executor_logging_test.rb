require_relative '../su_mcp/command_executor'
require_relative 'headless'


class LoggingCommandAdapter
  def delete_component(id:)
    { id: id }
  end

  def eval_ruby(code:)
    raise "RAW_EXCEPTION_#{code}" if code.include?('raise')

    { result: code.length }
  end
end


class CommandExecutorLoggingTest
  include HeadlessTest::Assertions

  def test_executor_logs_only_role_command_and_outcome_metadata
    messages = []
    executor = SU_MCP::CommandExecutor.new(
      adapter: LoggingCommandAdapter.new,
      logger: ->(message) { messages << message }
    )

    executor.call('delete_component', { 'id' => '987654321' })
    executor.call('eval_ruby', { 'code' => "'RAW_EVAL_SOURCE'" })
    assert_raises(RuntimeError) do
      executor.call('eval_ruby', { 'code' => 'raise RAW_FAILURE_SOURCE' })
    end

    assert_equal(
      [
        'Command executor: command started: delete_component',
        'Command executor: command completed: delete_component',
        'Command executor: command started: eval_ruby',
        'Command executor: command completed: eval_ruby',
        'Command executor: command started: eval_ruby',
        'Command executor: command failed: eval_ruby: RuntimeError'
      ],
      messages
    )
    assert_equal false, messages.join("\n").include?('987654321')
    assert_equal false, messages.join("\n").include?('RAW_EVAL_SOURCE')
    assert_equal false, messages.join("\n").include?('RAW_EXCEPTION')
    assert_equal false, messages.join("\n").include?('RAW_FAILURE_SOURCE')
  end
end


HeadlessTest.run(CommandExecutorLoggingTest)
