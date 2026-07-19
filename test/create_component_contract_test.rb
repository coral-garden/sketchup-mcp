require 'json'

require_relative '../su_mcp/su_mcp/command_dispatcher'
require_relative '../su_mcp/su_mcp/command_executor'
require_relative 'headless'

# The same fixture drives tests/test_python_runtime.py. Both runtimes assert the
# create_component envelope against this one file so neither can drift alone.
CONTRACT = JSON.parse(
  File.read(File.join(__dir__, 'fixtures/create_component_contract.json'))
)

class ContractSketchup
  def initialize(created_id:, failure: nil)
    @created_id = created_id
    @failure = failure
  end

  def create_component(type:, position:, dimensions:)
    raise @failure if @failure

    { id: @created_id }
  end

  def execute(_name, _arguments)
    {}
  end
end

class CreateComponentContractTest
  include HeadlessTest::Assertions

  def test_success_envelope_matches_the_shared_contract
    contract = CONTRACT['success_result']

    response = dispatch(
      CONTRACT['defaults'],
      request_id: 'mcp-create-17',
      created_id: contract['resourceId']
    )

    assert_equal(contract, stringify(response[:result]))
    assert_equal('mcp-create-17', response[:id])
  end

  def test_invalid_type_error_matches_the_shared_contract
    contract = CONTRACT['invalid_type']

    response = dispatch(
      contract['arguments'],
      request_id: contract['request_id'],
      created_id: contract.dig('arguments', 'id') || 1
    )

    assert_equal(contract['error'], stringify(response[:error]))
    assert_equal(contract['request_id'], response[:id])
  end

  def test_execution_error_matches_the_shared_contract
    contract = CONTRACT['execution_error']

    response = dispatch(
      CONTRACT['defaults'],
      request_id: contract['request_id'],
      created_id: 1,
      failure: contract['error']['message']
    )

    assert_equal(contract['error'], stringify(response[:error]))
    assert_equal(contract['request_id'], response[:id])
  end

  private

  def dispatch(arguments, request_id:, created_id:, failure: nil)
    sketchup = ContractSketchup.new(created_id: created_id, failure: failure)
    dispatcher = SU_MCP::CommandDispatcher.new(
      executor: SU_MCP::CommandExecutor.new(adapter: sketchup)
    )

    dispatcher.call(
      'jsonrpc' => '2.0',
      'method' => 'tools/call',
      'params' => { 'name' => 'create_component', 'arguments' => arguments },
      'id' => request_id
    )
  end

  def stringify(value)
    JSON.parse(JSON.generate(value))
  end
end

HeadlessTest.run(CreateComponentContractTest)
