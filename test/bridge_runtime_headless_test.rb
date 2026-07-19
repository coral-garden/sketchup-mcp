require_relative '../su_mcp/bridge_runtime'
require_relative 'headless'


class HeadlessListener
  attr_reader :calls

  def initialize(start_failure: nil, poll_failure: nil)
    @start_failure = start_failure
    @poll_failure = poll_failure
    @calls = []
  end

  def start
    @calls << :start
    raise @start_failure if @start_failure

    self
  end

  def stop
    @calls << :stop
  end

  def poll(timeout:)
    @calls << [:poll, timeout]
    raise @poll_failure if @poll_failure
  end

  def drain
    @calls << :drain
  end
end


class HeadlessScheduler
  attr_reader :calls, :interval

  def every(interval, &task)
    @calls ||= []
    @calls << [:every, interval]
    @interval = interval
    @task = task
    :headless_timer
  end

  def cancel(timer)
    @calls << [:cancel, timer]
    @task = nil
  end

  def tick
    @task.call
  end
end


class BridgeRuntimeHeadlessTest
  include HeadlessTest::Assertions

  def test_lifecycle_is_idempotent_and_ticks_a_controlled_listener
    listener = HeadlessListener.new
    scheduler = HeadlessScheduler.new
    runtime = SU_MCP::BridgeRuntime.new(listener: listener, scheduler: scheduler)

    assert_equal runtime, runtime.start
    assert_equal runtime, runtime.start
    scheduler.tick
    assert_equal runtime, runtime.stop
    assert_equal runtime, runtime.stop

    assert_equal SU_MCP::BridgeRuntime::POLL_INTERVAL, scheduler.interval
    assert_equal [:start, [:poll, 0], :drain, :stop, :stop], listener.calls
    assert_equal [[:every, SU_MCP::BridgeRuntime::POLL_INTERVAL],
                  [:cancel, :headless_timer]], scheduler.calls
  end

  def test_startup_failure_stops_the_listener_and_is_propagated
    failure = RuntimeError.new('controlled start failed')
    listener = HeadlessListener.new(start_failure: failure)
    runtime = SU_MCP::BridgeRuntime.new(listener: listener, scheduler: HeadlessScheduler.new)

    error = assert_raises(RuntimeError) { runtime.start }

    assert_equal failure, error
    assert_equal [:start, :stop], listener.calls
  end

  def test_tick_failure_is_reported_without_escaping_the_scheduler
    listener = HeadlessListener.new(poll_failure: IOError.new('controlled poll failed'))
    scheduler = HeadlessScheduler.new
    messages = []
    runtime = SU_MCP::BridgeRuntime.new(
      listener: listener,
      scheduler: scheduler,
      logger: ->(message) { messages << message }
    )
    runtime.start

    scheduler.tick
    assert_equal ['Extension runtime: scheduler tick failed: IOError'], messages
    assert_equal [:start, [:poll, 0]], listener.calls
  ensure
    runtime&.stop
  end
end


HeadlessTest.run(BridgeRuntimeHeadlessTest)
