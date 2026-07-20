require 'json'

require 'testup/testcase'

require_relative 'export_sandbox'


module SketchupMcpTestUp
  class ManualSuiteError < StandardError; end

  SUITE_ROOT = File.expand_path(__dir__)
  MANIFEST_PATH = File.join(SUITE_ROOT, 'suite_manifest.json')

  module_function

  def manifest
    @manifest ||= JSON.parse(File.read(MANIFEST_PATH))
  end

  def scenario_names
    manifest.fetch('scenarios')
  end

  def complete_scenario!(test_name)
    scenario = test_name.to_s.delete_prefix('test_')
    return nil if scenario_names.include?(scenario)

    raise ManualSuiteError, "unknown manual scenario: #{scenario}"
  end

  def loaded_adapter_path
    location = SU_MCP::SketchupAdapter.instance_method(:initialize).source_location
    raise ManualSuiteError, 'adapter source location is unavailable' unless location

    File.realpath(location.fetch(0))
  rescue SystemCallError
    raise ManualSuiteError, 'adapter source file is unavailable'
  end

  def packaged_catalog_path
    File.join(File.dirname(loaded_adapter_path), 'command_catalog.json')
  end
end
