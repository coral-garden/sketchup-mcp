require_relative '../su_mcp/command_dispatcher'
require_relative '../su_mcp/command_executor'
require_relative '../su_mcp/sketchup_adapter'
require_relative '../su_mcp/sketchup_commands'
require_relative 'headless'


class EvalContractModel
  attr_reader :operations

  def initialize
    @operations = []
  end

  def start_operation(name, disable_ui) = @operations << [:start, name, disable_ui]
  def commit_operation = @operations << [:commit]
  def abort_operation = @operations << [:abort]
end


class EvalRubyContractTest
  include HeadlessTest::Assertions

  def test_json_safe_values_are_normalized_recursively_with_their_ruby_type
    response, model = evaluate("[1, 2.5, true, nil, {:count => 2, 'name' => 'joint'}]", 1)

    assert_equal(
      { 'result' => [1, 2.5, true, nil, { 'count' => 2, 'name' => 'joint' }],
        'result_type' => 'Array' },
      JSON.parse(response.dig(:result, :content, 0, :text))
    )
    assert_equal [[:start, 'Evaluate Ruby', true], [:commit]], model.operations
  end

  def test_unsupported_results_and_evaluation_failures_are_typed_and_redacted
    cases = {
      'unsupported object' => ['Object.new', 'unsupported_result', nil],
      'cycle' => ['value = []; value << value; value', 'unsupported_result', nil],
      'non-finite' => ['Float::NAN', 'unsupported_result', nil],
      'duplicate normalized keys' => ["{'token' => 1, :token => 2}", 'unsupported_result', nil],
      'runtime error' => ["raise 'SECRET_MESSAGE'", 'evaluation_error', 'runtime_error'],
      'syntax error' => ['def SECRET_SOURCE(', 'evaluation_error', 'script_error']
    }

    cases.each_with_index do |(_label, (source, expected_type, category)), index|
      response, model = evaluate(source, "failure-#{index}")

      assert_equal(-32_603, response.dig(:error, :code))
      assert_equal expected_type, response.dig(:error, :data, :type)
      assert_equal category, response.dig(:error, :data, :category)
      assert_equal false, response.inspect.include?('SECRET')
      assert_equal [[:start, 'Evaluate Ruby', true], [:abort]], model.operations
    end
  end

  def test_process_control_exceptions_propagate_after_the_operation_is_aborted
    { SystemExit => 'system-exit', Interrupt => 'interrupt' }.each do |error_class, id|
      model = EvalContractModel.new
      dispatcher = dispatcher_for(model)

      assert_raises(error_class) do
        dispatcher.call(tool_request("raise #{error_class}", id))
      end

      assert_equal [[:start, 'Evaluate Ruby', true], [:abort]], model.operations
    end
  end

  private

  def evaluate(source, id)
    model = EvalContractModel.new
    [dispatcher_for(model).call(tool_request(source, id)), model]
  end

  def dispatcher_for(model)
    commands = SU_MCP::SketchupCommands.new(model: model)
    adapter = SU_MCP::SketchupAdapter.new(commands: commands, model: model)
    SU_MCP::CommandDispatcher.new(
      executor: SU_MCP::CommandExecutor.new(adapter: adapter)
    )
  end

  def tool_request(source, id)
    {
      'jsonrpc' => '2.0',
      'method' => 'tools/call',
      'params' => { 'name' => 'eval_ruby', 'arguments' => { 'code' => source } },
      'id' => id
    }
  end
end


HeadlessTest.run(EvalRubyContractTest)
