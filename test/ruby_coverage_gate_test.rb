require 'json'
require 'tmpdir'

require_relative '../scripts/ruby_coverage'
require_relative 'headless'


class RubyCoverageGateTest
  include HeadlessTest::Assertions

  def test_gate_rejects_any_uncovered_line
    error = assert_raises(RubyCoverage::IncompleteCoverage) do
      RubyCoverage::Gate.verify!(covered_lines: 19, total_lines: 20,
                                 covered_branches: 8, total_branches: 8)
    end

    assert_includes error.message, 'Line coverage: 95.00% (19/20)'
  end

  def test_gate_rejects_any_uncovered_branch
    error = assert_raises(RubyCoverage::IncompleteCoverage) do
      RubyCoverage::Gate.verify!(covered_lines: 20, total_lines: 20,
                                 covered_branches: 7, total_branches: 8)
    end

    assert_includes error.message, 'Branch coverage: 87.50% (7/8)'
  end

  def test_gate_accepts_exact_line_and_branch_coverage
    summary = RubyCoverage::Gate.verify!(
      covered_lines: 20,
      total_lines: 20,
      covered_branches: 8,
      total_branches: 8
    )

    assert_equal 'Line coverage: 100.00% (20/20)', summary.fetch(:lines)
    assert_equal 'Branch coverage: 100.00% (8/8)', summary.fetch(:branches)
  end

  def test_gate_writes_exact_machine_readable_metrics_when_requested
    Dir.mktmpdir('ruby-coverage-report') do |directory|
      path = File.join(directory, 'ruby.json')

      RubyCoverage::Gate.write_report(
        path,
        covered_lines: 20,
        total_lines: 20,
        covered_branches: 8,
        total_branches: 8
      )

      assert_equal(
        {
          'schema_version' => 1,
          'scope' => 'headless_ruby',
          'thresholds' => { 'lines' => 100, 'branches' => 100 },
          'lines' => { 'covered' => 20, 'total' => 20 },
          'branches' => { 'covered' => 8, 'total' => 8 }
        },
        JSON.parse(File.read(path))
      )
    end
  end
end


HeadlessTest.run(RubyCoverageGateTest)
