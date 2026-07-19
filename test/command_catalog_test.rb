require_relative '../su_mcp/su_mcp/command_catalog'
require_relative '../su_mcp/su_mcp/sketchup_adapter'
require_relative 'headless'


class CommandCatalogTest
  include HeadlessTest::Assertions

  def test_every_catalog_command_is_reachable_on_the_sketchup_adapter
    catalog = SU_MCP::CommandCatalog.new
    adapter = SU_MCP::SketchupAdapter.new(commands: Object.new, model: Object.new)

    assert_equal [], catalog.names.reject { |name| adapter.respond_to?(name) }
  end

  def test_command_contract_policy_is_immutable
    command = SU_MCP::CommandCatalog.new.command('create_component')

    assert_equal true, command.frozen?
    assert_raises(FrozenError) do
      command.optional_arguments.fetch('type')['default'] = 'sphere'
    end
  end
end


HeadlessTest.run(CommandCatalogTest)
