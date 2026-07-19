module HeadlessTest
  module Assertions
    def assert_equal(expected, actual)
      return if expected == actual

      raise "Expected #{expected.inspect}, got #{actual.inspect}"
    end

    def assert_includes(value, fragment)
      return if value.include?(fragment)

      raise "Expected #{value.inspect} to include #{fragment.inspect}"
    end

    def assert_operator(left, operator, right)
      return if left.public_send(operator, right)

      raise "Expected #{left.inspect} to be #{operator} #{right.inspect}"
    end

    def assert_raises(error_class)
      yield
      raise "Expected #{error_class} to be raised"
    rescue error_class => error
      error
    end

    def wait_until
      deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 1
      until yield
        raise 'condition was not met before the deadline' if monotonic_time >= deadline

        Thread.pass
      end
    end

    private

    def monotonic_time
      Process.clock_gettime(Process::CLOCK_MONOTONIC)
    end
  end

  def self.run(test_class)
    failures = []
    tests = test_class.instance_methods(false).grep(/^test_/).sort
    tests.each do |test_name|
      test = test_class.new
      begin
        test.public_send(test_name)
        print '.'
      rescue StandardError => error
        print 'F'
        failures << [test_name, error]
      ensure
        test.teardown if test.respond_to?(:teardown)
      end
    end
    puts
    failures.each do |test_name, error|
      warn "#{test_name}: #{error.class}: #{error.message}"
      warn error.backtrace.join("\n")
    end
    puts "#{tests.length} tests, #{failures.length} failures"
    exit(failures.empty? ? 0 : 1)
  end
end
