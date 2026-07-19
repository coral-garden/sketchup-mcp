require 'json'
require 'tmpdir'

require_relative '../su_mcp/command_catalog'
require_relative '../su_mcp/command_execution_error'
require_relative '../su_mcp/command_executor'
require_relative '../su_mcp/eval_result'
require_relative 'headless'


class ControlledCatalogFilesystem
  def initialize(existing_path)
    @existing_path = existing_path
  end

  def file?(path)
    path == @existing_path
  end
end


class CommandCatalogHeadlessTest
  include HeadlessTest::Assertions

  def test_catalog_rejects_non_objects_and_invalid_string_arguments
    catalog = SU_MCP::CommandCatalog.new

    non_object = assert_raises(SU_MCP::InvalidArguments) do
      catalog.validate(catalog.command('get_selection'), [])
    end
    invalid_string = assert_raises(SU_MCP::InvalidArguments) do
      catalog.validate(catalog.command('eval_ruby'), 'code' => 41)
    end

    assert_equal 'arguments must be an object', non_object.message
    assert_equal 'code must be a string', invalid_string.message
  end

  def test_catalog_preserves_explicitly_untyped_contract_values
    Dir.mktmpdir('ruby-catalog-coverage') do |directory|
      path = File.join(directory, 'catalog.json')
      File.write(path, JSON.generate(untyped_catalog))
      catalog = SU_MCP::CommandCatalog.new(path: path)

      assert_equal({ 'value' => { 'opaque' => true } }, catalog.validate(
        catalog.command('controlled_command'),
        'value' => { 'opaque' => true }
      ))
    end
  end

  def test_catalog_path_preference_is_testable_without_mutating_the_package
    packaged = SU_MCP::CommandCatalog::PACKAGED_PATH
    source = SU_MCP::CommandCatalog::SOURCE_PATH

    assert_equal packaged, SU_MCP::CommandCatalog.default_path(
      filesystem: ControlledCatalogFilesystem.new(packaged)
    )
    assert_equal source, SU_MCP::CommandCatalog.default_path(
      filesystem: ControlledCatalogFilesystem.new(source)
    )
  end

  def test_execution_error_rejects_unknown_semantics
    error = assert_raises(ArgumentError) do
      SU_MCP::CommandExecutionError.new('controlled failure', kind: 'not_a_kind')
    end

    assert_includes error.message, 'Unknown command execution error kind'
  end

  def test_executor_rejects_an_adapter_without_the_catalog_command
    executor = SU_MCP::CommandExecutor.new(adapter: Object.new)

    error = assert_raises(SU_MCP::UnknownCommand) do
      executor.call('get_selection', {})
    end

    assert_equal 'Unknown command: get_selection', error.message
  end

  def test_eval_result_normalizes_finite_float_keys_and_rejects_other_keys
    normalized = SU_MCP::EvalResult.normalize(1.5 => 'value')
    non_finite = assert_raises(SU_MCP::CommandExecutionError) do
      SU_MCP::EvalResult.normalize(Float::NAN => 'value')
    end
    unsupported = assert_raises(SU_MCP::CommandExecutionError) do
      SU_MCP::EvalResult.normalize(Object.new => 'value')
    end

    assert_equal({ '1.5' => 'value' }, normalized.fetch(:result))
    assert_equal 'unsupported_result', non_finite.kind
    assert_equal 'unsupported_result', unsupported.kind
  end

  private

  def untyped_catalog
    {
      'schema_version' => 1,
      'failure_semantics' => {},
      'executable_aliases' => {},
      'commands' => [{
        'name' => 'controlled_command',
        'arguments' => {
          'required' => { 'value' => { 'type' => 'opaque' } },
          'optional' => {}
        },
        'success' => {}
      }]
    }
  end
end


HeadlessTest.run(CommandCatalogHeadlessTest)
