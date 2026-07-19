require_relative '../su_mcp/command_catalog'
require_relative '../su_mcp/command_response_builder'
require_relative 'headless'


class CommandResponseBuilderTest
  include HeadlessTest::Assertions

  def test_success_metadata_and_failure_codes_come_from_the_catalog
    catalog = SU_MCP::CommandCatalog.new
    responses = SU_MCP::CommandResponseBuilder.new(catalog: catalog)

    created = responses.success(
      command: catalog.command('create_component'), result: { id: 731 }, id: 'created'
    )
    selected = responses.success(
      command: catalog.command('get_selection'), result: { entities: [] }, id: 'selected'
    )
    invalid = responses.failure(
      'invalid_arguments', message: 'id is invalid', id: 'invalid'
    )

    assert_equal 731, created.dig(:result, :resourceId)
    assert_equal false, selected[:result].key?(:resourceId)
    assert_equal(-32_602, invalid.dig(:error, :code))
    assert_equal false, invalid.dig(:error, :data, :success)
  end
end


HeadlessTest.run(CommandResponseBuilderTest)
