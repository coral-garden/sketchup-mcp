require 'coverage'
require 'json'

coverage_file = File.expand_path(__FILE__)
$LOADED_FEATURES << coverage_file unless $LOADED_FEATURES.include?(coverage_file)


module RubyCoverage
  ROOT = File.expand_path('..', __dir__)
  RUNTIME_SOURCE_DIR = File.join(ROOT, 'su_mcp')

  # This is the deterministic command-orchestration core. Every dependency at
  # its seams is replaceable with an in-memory adapter.
  HEADLESS_SOURCE = %w[
    bridge_listener.rb
    bridge_protocol.rb
    bridge_runtime.rb
    command_catalog.rb
    command_dispatcher.rb
    command_execution_error.rb
    command_executor.rb
    command_response_builder.rb
    eval_result.rb
    sketchup_adapter.rb
    version.rb
  ].map { |name| File.join(RUNTIME_SOURCE_DIR, name) }.freeze

  # SocketTransport owns real TCP. The other files are SketchUp-owned adapters
  # or composition roots whose final acceptance belongs to the SketchUp runtime
  # suite (#15); the classification does not imply that the files cannot load
  # under controlled tests.
  RUNTIME_BOUND_SOURCE = %w[
    extension_menu.rb
    extension_runtime.rb
    main.rb
    sketchup_commands.rb
    socket_transport.rb
  ].map { |name| File.join(RUNTIME_SOURCE_DIR, name) }.freeze

  # BridgeRuntimeTest opens real TCP; the extension tests exercise SketchUp-
  # owned composition roots. They remain outside this deterministic gate.
  RUNTIME_BOUND_TESTS = %w[
    bridge_runtime_test.rb
    extension_menu_test.rb
    extension_runtime_test.rb
  ].map { |name| File.join(ROOT, 'test', name) }.freeze

  # The remaining BridgeListener methods use controlled transports and stay in
  # the gate; only these live-loopback integration methods are skipped.
  REAL_TCP_TEST_METHODS = {
    'BridgeListenerTest' => %i[
      test_accepting_a_silent_client_never_blocks_the_polling_thread
      test_eof_does_not_replace_the_required_request_newline
      test_handler_jsonrpc_error_is_returned_without_rewriting_it
      test_listener_binds_only_to_ipv4_loopback
      test_malformed_json_returns_parse_error_with_null_id
      test_one_newline_framed_request_is_answered_then_connection_closes
      test_port_collision_raises_an_explicit_startup_error
    ]
  }.freeze

  class IncompleteCoverage < StandardError; end

  module Gate
    module_function

    def verify!(covered_lines:, total_lines:, covered_branches:, total_branches:)
      summary = {
        lines: format_metric('Line', covered_lines, total_lines),
        branches: format_metric('Branch', covered_branches, total_branches)
      }
      return summary if covered_lines == total_lines && covered_branches == total_branches

      raise IncompleteCoverage, summary.values.join("\n")
    end

    def format_metric(label, covered, total)
      percentage = total.zero? ? 100.0 : covered.fdiv(total) * 100
      format('%s coverage: %.2f%% (%d/%d)', label, percentage, covered, total)
    end
    private_class_method :format_metric

    def write_report(path, covered_lines:, total_lines:, covered_branches:, total_branches:)
      report = {
        schema_version: 1,
        scope: 'headless_ruby',
        thresholds: { lines: 100, branches: 100 },
        lines: { covered: covered_lines, total: total_lines },
        branches: { covered: covered_branches, total: total_branches }
      }
      File.write(path, JSON.pretty_generate(report) + "\n")
    end
  end

  module Runner
    module_function

    def run(report_path: nil)
      verify_source_classification!
      test_classes = capture_test_classes do
        Coverage.start(lines: true, branches: true)
        HEADLESS_SOURCE.each { |path| require path.delete_suffix('.rb') }
        load_tests
      end
      tests, failures = run_tests(test_classes)
      result = Coverage.result
      report_failures(failures)
      abort "#{failures.length} headless coverage tests failed" unless failures.empty?

      coverage_metrics = metrics(result)
      summary = Gate.verify!(**coverage_metrics)
      Gate.write_report(report_path, **coverage_metrics) if report_path
      puts "#{test_classes.length} test classes, #{tests} tests, 0 failures"
      puts summary.fetch(:lines)
      puts summary.fetch(:branches)
      summary
    rescue IncompleteCoverage => error
      report_uncovered(result) if result
      raise error
    end

    def capture_test_classes
      require File.join(ROOT, 'test', 'headless')
      classes = []
      original = HeadlessTest.method(:run)
      HeadlessTest.define_singleton_method(:run) { |test_class| classes << test_class }
      yield
      classes
    ensure
      HeadlessTest.define_singleton_method(:run, original) if original
    end

    def load_tests
      files = Dir[File.join(ROOT, 'test', '*_test.rb')].sort - RUNTIME_BOUND_TESTS
      abort 'No deterministic headless test files found' if files.empty?

      files.each { |file| load file }
    end

    def run_tests(test_classes)
      failures = []
      count = 0
      test_classes.each do |test_class|
        test_class.instance_methods(false).grep(/^test_/).sort.each do |test_name|
          next if REAL_TCP_TEST_METHODS.fetch(test_class.name, []).include?(test_name)

          count += 1
          test = test_class.new
          begin
            test.public_send(test_name)
          rescue StandardError, ScriptError => error
            failures << [test_class, test_name, error]
          ensure
            test.teardown if test.respond_to?(:teardown)
          end
        end
      end
      [count, failures]
    end

    def report_failures(failures)
      failures.each do |test_class, test_name, error|
        warn "#{test_class}##{test_name}: #{error.class}: #{error.message}"
        warn error.backtrace.join("\n")
      end
    end

    def metrics(result)
      files = HEADLESS_SOURCE.map do |path|
        [path, result.fetch(path) { abort "Headless source was not loaded: #{relative(path)}" }]
      end
      lines = files.flat_map { |_path, coverage| coverage.fetch(:lines).compact }
      branches = files.flat_map do |_path, coverage|
        coverage.fetch(:branches).values.flat_map(&:values)
      end
      {
        covered_lines: lines.count(&:positive?),
        total_lines: lines.length,
        covered_branches: branches.count(&:positive?),
        total_branches: branches.length
      }
    end

    def report_uncovered(result)
      HEADLESS_SOURCE.each do |path|
        coverage = result.fetch(path)
        missed_lines = coverage.fetch(:lines).each_index.select do |index|
          coverage.fetch(:lines)[index] == 0
        end.map { |index| index + 1 }
        missed_branches = coverage.fetch(:branches).values.flat_map do |children|
          children.filter_map do |branch, count|
            next unless count.zero?

            type, _id, start_line, start_column, = branch
            "#{type}@#{start_line}:#{start_column}"
          end
        end
        next if missed_lines.empty? && missed_branches.empty?

        warn "UNCOVERED #{relative(path)}"
        warn "  lines: #{missed_lines.join(', ')}" unless missed_lines.empty?
        warn "  branches: #{missed_branches.join(', ')}" unless missed_branches.empty?
      end
    end

    def verify_source_classification!
      actual = Dir[File.join(RUNTIME_SOURCE_DIR, '*.rb')].sort
      classified = (HEADLESS_SOURCE + RUNTIME_BOUND_SOURCE).sort
      return if actual == classified

      missing = actual - classified
      stale = classified - actual
      abort "Ruby source classification is incomplete: " \
            "unclassified=#{missing.map { |path| relative(path) }.inspect}, " \
            "missing=#{stale.map { |path| relative(path) }.inspect}"
    end

    def relative(path)
      path.delete_prefix("#{ROOT}/")
    end
  end
end


if __FILE__ == $PROGRAM_NAME
  report_path = if ARGV.empty?
                  nil
                elsif ARGV.length == 2 && ARGV.first == '--json'
                  ARGV.fetch(1)
                else
                  abort 'Usage: ruby scripts/ruby_coverage.rb [--json REPORT]'
                end
  RubyCoverage::Runner.run(report_path: report_path)
end
