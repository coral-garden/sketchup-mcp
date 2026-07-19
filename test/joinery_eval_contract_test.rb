require 'json'

require_relative '../su_mcp/su_mcp/command_dispatcher'
require_relative '../su_mcp/su_mcp/command_executor'
require_relative 'headless'


JOINERY_EVAL_CONTRACT = JSON.parse(
  File.read(File.join(__dir__, 'fixtures', 'joinery_eval_contract.json'))
)


class InMemoryJoineryEvalAdapter
  attr_reader :calls

  def initialize(results:, failure: nil)
    @results = results
    @failure = failure
    @calls = []
  end

  def create_mortise_tenon(**arguments) = record('create_mortise_tenon', arguments)
  def create_dovetail(**arguments) = record('create_dovetail', arguments)
  def create_finger_joint(**arguments) = record('create_finger_joint', arguments)
  def eval_ruby(**arguments) = record('eval_ruby', arguments)

  private

  def record(name, arguments)
    @calls << [name, arguments]
    raise @failure if @failure

    @results.fetch(name)
  end
end


class JoineryEvalContractTest
  include HeadlessTest::Assertions

  def test_shared_contract_returns_plain_results_without_resource_ids
    results = JOINERY_EVAL_CONTRACT['commands'].to_h do |command|
      [command['name'], command['command_result']]
    end
    adapter = InMemoryJoineryEvalAdapter.new(results: results)
    dispatcher = dispatcher_for(adapter)

    JOINERY_EVAL_CONTRACT['commands'].each do |command|
      response = dispatcher.call(tool_request(command['name'], command['arguments'], command['request_id']))

      assert_equal '2.0', response[:jsonrpc]
      assert_equal command['request_id'], response[:id]
      assert_equal JSON.generate(command['command_result']), response.dig(:result, :content, 0, :text)
      assert_equal false, response[:result].key?(:resourceId)
    end
    assert_equal JOINERY_EVAL_CONTRACT['commands'].map { |command| command['name'] },
                 adapter.calls.map(&:first)
    assert_equal 101, adapter.calls.first[1][:mortise_id]
    assert_equal 104, adapter.calls[1][1][:pin_id]
  end

  def test_catalog_defaults_are_applied_before_explicit_adapter_calls
    adapter = InMemoryJoineryEvalAdapter.new(
      results: {
        'create_mortise_tenon' => { mortise_id: 1, tenon_id: 2 },
        'create_dovetail' => { tail_id: 3, pin_id: 4 },
        'create_finger_joint' => { board1_id: 5, board2_id: 6 }
      }
    )
    dispatcher = dispatcher_for(adapter)

    dispatcher.call(tool_request('create_mortise_tenon', { 'mortise_id' => 1, 'tenon_id' => 2 }, 1))
    dispatcher.call(tool_request('create_dovetail', { 'tail_id' => 3, 'pin_id' => 4 }, 2))
    dispatcher.call(tool_request('create_finger_joint', { 'board1_id' => 5, 'board2_id' => 6 }, 3))

    adapter.calls.each do |name, arguments|
      expected = JOINERY_EVAL_CONTRACT['defaults'].fetch(name).transform_keys(&:to_sym)
      id_names = arguments.keys - expected.keys
      assert_equal expected, arguments.reject { |key, _value| id_names.include?(key) }
    end
  end

  def test_every_invalid_argument_category_stops_before_the_adapter
    adapter = InMemoryJoineryEvalAdapter.new(results: {})
    dispatcher = dispatcher_for(adapter)

    JOINERY_EVAL_CONTRACT['invalid_arguments'].each_with_index do |fixture, index|
      response = dispatcher.call(
        tool_request(fixture['name'], fixture['arguments'], "invalid-#{index}")
      )

      assert_equal(-32_602, response.dig(:error, :code))
      assert_includes response.dig(:error, :message), fixture['contains']
      assert_equal false, response.dig(:error, :data, :success)
    end
    response = dispatcher.call(
      tool_request(
        'create_mortise_tenon',
        { 'mortise_id' => 1, 'tenon_id' => 2, 'offset_x' => Float::INFINITY },
        'non-finite'
      )
    )
    assert_equal(-32_602, response.dig(:error, :code))
    missing = dispatcher.call(
      tool_request('create_dovetail', { 'tail_id' => 1 }, 'missing-argument')
    )
    assert_equal(-32_602, missing.dig(:error, :code))
    assert_includes missing.dig(:error, :message), 'pin_id'
    assert_equal [], adapter.calls
  end

  def test_execution_failures_are_structured_without_leaking_sensitive_details
    adapter = InMemoryJoineryEvalAdapter.new(
      results: {},
      failure: RuntimeError.new('SECRET geometry and source details')
    )
    dispatcher = dispatcher_for(adapter)

    joinery = dispatcher.call(
      tool_request('create_dovetail', { 'tail_id' => 1, 'pin_id' => 2 }, 'failed-joint')
    )
    evaluation = dispatcher.call(
      tool_request('eval_ruby', { 'code' => 'SECRET_SOURCE' }, 'failed-eval')
    )

    assert_equal(-32_603, joinery.dig(:error, :code))
    assert_equal 'SketchUp joinery execution failed', joinery.dig(:error, :message)
    assert_equal 'joinery_execution_error', joinery.dig(:error, :data, :type)
    assert_equal 'create_dovetail', joinery.dig(:error, :data, :command)
    assert_equal(-32_603, evaluation.dig(:error, :code))
    assert_equal 'Ruby evaluation failed', evaluation.dig(:error, :message)
    assert_equal 'evaluation_error', evaluation.dig(:error, :data, :type)
    assert_equal false, [joinery, evaluation].inspect.include?('SECRET')
  end

  private

  def dispatcher_for(adapter)
    SU_MCP::CommandDispatcher.new(
      executor: SU_MCP::CommandExecutor.new(adapter: adapter)
    )
  end

  def tool_request(name, arguments, id)
    {
      'jsonrpc' => '2.0',
      'method' => 'tools/call',
      'params' => { 'name' => name, 'arguments' => arguments },
      'id' => id
    }
  end
end


HeadlessTest.run(JoineryEvalContractTest)
