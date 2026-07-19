require 'digest'
require 'json'
require 'rbconfig'
require 'time'

require 'testup/testcase'

require_relative 'export_sandbox'


module SketchupMcpTestUp
  class RuntimeReportError < StandardError; end

  SUITE_ROOT = File.expand_path(__dir__)
  MANIFEST_PATH = File.join(SUITE_ROOT, 'suite_manifest.json')
  COVERAGE_SCOPE = ['su_mcp/sketchup_adapter.rb'].freeze
  FINAL_SCENARIO = 'zz_write_runtime_report'
  SHA256 = /\A[0-9a-f]{64}\z/

  module_function

  def manifest
    @manifest ||= JSON.parse(File.read(MANIFEST_PATH))
  end

  def scenario_names
    manifest.fetch('scenarios')
  end

  def behavior_scenarios
    scenario_names - [FINAL_SCENARIO]
  end

  def run_marker_test_name(run_id = ENV.fetch('SKETCHUP_MCP_TESTUP_RUN_ID'))
    raise RuntimeReportError, 'invalid_testup_run_id' unless run_id.match?(SHA256)

    "test_run_id_#{run_id}"
  end

  def expected_test_count
    scenario_names.length + 1
  end

  def complete_scenario!(test_name)
    scenario = test_name.to_s.delete_prefix('test_')
    raise RuntimeReportError, 'unknown_testup_scenario' unless behavior_scenarios.include?(scenario)

    @completed_scenarios ||= []
    raise RuntimeReportError, 'duplicate_testup_scenario' if @completed_scenarios.include?(scenario)

    @completed_scenarios << scenario
    nil
  end

  def assert_report_ready!
    completed = (@completed_scenarios || []).sort
    expected = behavior_scenarios.sort
    return if completed == expected

    raise RuntimeReportError, 'testup_scenarios_incomplete'
  end

  def suite_sha256
    digest = Digest::SHA256.new
    Dir[File.join(SUITE_ROOT, '**', '*')].sort.each do |path|
      next unless File.file?(path)

      relative = path.sub(/\A#{Regexp.escape(SUITE_ROOT)}[\\\/]/, '').tr('\\', '/')
      digest.update(relative)
      digest.update("\0")
      digest.update(File.binread(path))
      digest.update("\0")
    end
    digest.hexdigest
  end

  def loaded_adapter_path
    location = SU_MCP::SketchupAdapter.instance_method(:initialize).source_location
    raise RuntimeReportError, 'adapter_source_location_unavailable' unless location

    canonical_regular_file(location.fetch(0), 'adapter_source_invalid')
  end

  def installed_package_files
    support_root = File.dirname(loaded_adapter_path)
    install_root = File.dirname(support_root)
    root_loader = File.join(install_root, 'su_mcp.rb')
    candidates = [root_loader] + Dir[File.join(support_root, '**', '*')].sort
    files = candidates.filter_map do |path|
      stat = File.lstat(path)
      raise RuntimeReportError, 'installed_source_invalid' if stat.symlink?
      next if stat.directory?
      raise RuntimeReportError, 'installed_source_invalid' unless stat.file?

      canonical = canonical_regular_file(path, 'installed_source_invalid')
      prefix = "#{install_root}#{File::SEPARATOR}"
      unless canonical.start_with?(prefix)
        raise RuntimeReportError, 'installed_source_outside_extension_root'
      end

      member = canonical.delete_prefix(prefix).tr('\\', '/')
      [member, Digest::SHA256.hexdigest(File.binread(canonical))]
    end.to_h
    unless files.key?('su_mcp.rb') && files.key?(COVERAGE_SCOPE.fetch(0))
      raise RuntimeReportError, 'installed_source_manifest_incomplete'
    end

    files.sort.to_h
  rescue SystemCallError
    raise RuntimeReportError, 'installed_source_invalid'
  end

  def packaged_catalog_path
    File.join(File.dirname(loaded_adapter_path), 'command_catalog.json')
  end

  def testup_version
    return TestUp::VERSION.to_s if defined?(TestUp::VERSION)

    extension = nil
    Sketchup.extensions.each do |candidate|
      next unless candidate.respond_to?(:name)
      next unless candidate.name.to_s.downcase.start_with?('testup')

      extension = candidate
      break
    end
    raise RuntimeReportError, 'testup_extension_metadata_unavailable' unless extension

    extension.version.to_s
  end

  def os_family
    case Sketchup.platform
    when :platform_win then 'windows'
    when :platform_osx then 'macos'
    else 'unsupported'
    end
  end

  def coverage_metric(counts, missing_values)
    total = counts.length
    covered = counts.count { |count| count && count.positive? }
    {
      covered: covered,
      total: total,
      percent: total.zero? ? 0.0 : (covered * 100.0 / total),
      missing: missing_values
    }
  end

  def coverage_report
    raise RuntimeReportError, 'ruby_coverage_not_started' unless defined?(Coverage)
    raise RuntimeReportError, 'ruby_coverage_not_running' unless Coverage.running?

    result = Coverage.peek_result
    adapter_path = loaded_adapter_path
    measured_path = result.keys.find { |path| File.expand_path(path) == adapter_path }
    raise RuntimeReportError, 'adapter_loaded_before_coverage' unless measured_path

    measurement = result.fetch(measured_path)
    lines = measurement[:lines]
    branches = measurement[:branches]
    raise RuntimeReportError, 'line_coverage_unavailable' unless lines.is_a?(Array)
    raise RuntimeReportError, 'branch_coverage_unavailable' unless branches.is_a?(Hash)

    executable_lines = lines.each_with_index.filter_map do |count, index|
      [index + 1, count] unless count.nil?
    end
    branch_entries = branches.flat_map do |condition, alternatives|
      alternatives.map { |branch, count| [condition, branch, count] }
    end
    {
      engine: 'ruby Coverage',
      scope: COVERAGE_SCOPE,
      source_sha256: Digest::SHA256.hexdigest(File.binread(adapter_path)),
      lines: coverage_metric(
        executable_lines.map(&:last),
        executable_lines.filter_map { |line, count| line unless count.positive? }
      ),
      branches: coverage_metric(
        branch_entries.map(&:last),
        branch_entries.filter_map do |condition, branch, count|
          "#{condition.inspect} -> #{branch.inspect}" unless count.positive?
        end
      )
    }
  end

  def runtime_report(run_id, generated_at)
    catalog_contents = File.binread(packaged_catalog_path)
    catalog = JSON.parse(catalog_contents)
    {
      schema_version: 3,
      run_id: run_id,
      generated_at: generated_at,
      branch_supported: true,
      expected_test_count: expected_test_count,
      suite_sha256: suite_sha256,
      catalog_sha256: Digest::SHA256.hexdigest(catalog_contents),
      commands: catalog.fetch('commands').map { |command| command.fetch('name') },
      project_version: SU_MCP::VERSION,
      commit: ENV.fetch('SKETCHUP_MCP_TESTUP_COMMIT'),
      installed_files: installed_package_files,
      os_family: os_family,
      os_version: ENV.fetch('SKETCHUP_MCP_TESTUP_OS_VERSION'),
      architecture: RbConfig::CONFIG.fetch('host_cpu'),
      sketchup_version: Sketchup.version,
      testup_version: testup_version,
      ruby_version: RUBY_VERSION,
      ruby_platform: RUBY_PLATFORM,
      coverage: coverage_report
    }
  end

  def suite_marker(run_id, generated_at)
    {
      schema_version: 2,
      run_id: run_id,
      generated_at: generated_at,
      test_class: 'TC_ProductionAdapter',
      scenarios: scenario_names,
      run_marker_test: run_marker_test_name(run_id)
    }
  end

  def write_runtime_report!
    assert_report_ready!
    run_id = ENV.fetch('SKETCHUP_MCP_TESTUP_RUN_ID')
    run_marker_test_name(run_id)

    generated_at = Time.now.utc.iso8601
    marker_path = ENV.fetch('SKETCHUP_MCP_TESTUP_SUITE_MARKER')
    report_path = ENV.fetch('SKETCHUP_MCP_TESTUP_RUNTIME_REPORT')
    write_json!(marker_path, suite_marker(run_id, generated_at), 'suite_marker_write_failed')
    write_json!(report_path, runtime_report(run_id, generated_at), 'runtime_report_write_failed')
    nil
  end

  def assert_runtime_configuration(test_case)
    test_case.assert_equal '1', ENV['SKETCHUP_MCP_TESTUP_COVERAGE']
    test_case.assert_match(/\A[0-9a-f]{40}\z/, ENV['SKETCHUP_MCP_TESTUP_COMMIT'])
    test_case.assert_match(SHA256, ENV['SKETCHUP_MCP_TESTUP_RUN_ID'])
    test_case.refute_empty ENV.fetch('SKETCHUP_MCP_TESTUP_OS_VERSION')
    test_case.refute_empty ENV.fetch('SKETCHUP_MCP_TESTUP_RUNTIME_REPORT')
    test_case.refute_empty ENV.fetch('SKETCHUP_MCP_TESTUP_SUITE_MARKER')
    test_case.assert defined?(Coverage) && Coverage.running?, 'Ruby Coverage must be running'
  end

  def canonical_regular_file(path, error_code)
    stat = File.lstat(path)
    raise RuntimeReportError, error_code unless stat.file? && !stat.symlink?

    File.realpath(path)
  rescue SystemCallError
    raise RuntimeReportError, error_code
  end

  def write_json!(path, value, error_code)
    File.write(path, JSON.pretty_generate(value))
  rescue SystemCallError
    raise RuntimeReportError, error_code
  end
end
