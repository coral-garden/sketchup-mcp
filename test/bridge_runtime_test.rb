require 'socket'

require_relative '../su_mcp/bridge_listener'
require_relative '../su_mcp/bridge_runtime'
require_relative '../su_mcp/command_dispatcher'
require_relative '../su_mcp/command_executor'
require_relative 'headless'


class ControlledScheduler
  attr_reader :interval

  def every(interval, &task)
    @interval = interval
    @task = task
    :timer
  end

  def cancel(timer)
    @task = nil if timer == :timer
  end

  def tick
    @task.call
  end
end


class ThreadRecordingSketchup
  attr_reader :calling_threads

  def initialize(failure: nil)
    @failure = failure
    @calling_threads = []
  end

  def create_component(type:, position:, dimensions:)
    @calling_threads << Thread.current
    raise @failure if @failure

    { id: 321 }
  end
end


class BridgeRuntimeTest
  include HeadlessTest::Assertions

  def teardown
    @runtime&.stop
  end

  def test_silent_client_cannot_block_the_ui_scheduler_tick
    scheduler = ControlledScheduler.new
    listener = SU_MCP::BridgeListener.new(
      port: 0,
      handler: ->(_request) { {} },
      io_timeout: 0.05
    )
    @runtime = SU_MCP::BridgeRuntime.new(
      listener: listener,
      scheduler: scheduler
    )
    @runtime.start
    client = TCPSocket.new('127.0.0.1', listener.port)

    started_at = Process.clock_gettime(Process::CLOCK_MONOTONIC)
    scheduler.tick
    elapsed = Process.clock_gettime(Process::CLOCK_MONOTONIC) - started_at

    assert_operator elapsed, :<, 0.04
  ensure
    client&.close
  end

  def test_complete_request_executes_sketchup_on_the_scheduler_thread
    scheduler = ControlledScheduler.new
    sketchup = ThreadRecordingSketchup.new
    dispatcher = SU_MCP::CommandDispatcher.new(
      executor: SU_MCP::CommandExecutor.new(adapter: sketchup)
    )
    listener = SU_MCP::BridgeListener.new(
      port: 0,
      handler: dispatcher.method(:call)
    )
    @runtime = SU_MCP::BridgeRuntime.new(
      listener: listener,
      scheduler: scheduler
    )
    @runtime.start
    client = TCPSocket.new('127.0.0.1', listener.port)
    request = {
      jsonrpc: '2.0',
      method: 'tools/call',
      params: {
        name: 'create_component',
        arguments: { type: 'cube', position: [0, 0, 0], dimensions: [1, 1, 1] }
      },
      id: nil
    }
    client.write(JSON.generate(request) + "\n")

    response = nil
    wait_until do
      scheduler.tick
      if IO.select([client], nil, nil, 0.001)
        response = JSON.parse(client.gets)
        true
      else
        false
      end
    end

    assert_equal(
      {
        'jsonrpc' => '2.0',
        'result' => {
          'content' => [{ 'type' => 'text', 'text' => '{"id":321}' }],
          'isError' => false,
          'success' => true,
          'resourceId' => 321
        },
        'id' => nil
      },
      response
    )
    assert_equal [Thread.current], sketchup.calling_threads
  ensure
    client&.close
  end

  def test_sketchup_failure_returns_jsonrpc_error_through_the_listener
    scheduler = ControlledScheduler.new
    sketchup = ThreadRecordingSketchup.new(
      failure: RuntimeError.new('active model is unavailable')
    )
    dispatcher = SU_MCP::CommandDispatcher.new(
      executor: SU_MCP::CommandExecutor.new(adapter: sketchup)
    )
    listener = SU_MCP::BridgeListener.new(
      port: 0,
      handler: dispatcher.method(:call)
    )
    @runtime = SU_MCP::BridgeRuntime.new(
      listener: listener,
      scheduler: scheduler
    )
    @runtime.start
    client = TCPSocket.new('127.0.0.1', listener.port)
    request = {
      jsonrpc: '2.0',
      method: 'tools/call',
      params: { name: 'create_component', arguments: {} },
      id: 'failed-runtime-63'
    }
    client.write(JSON.generate(request) + "\n")

    response = nil
    wait_until do
      scheduler.tick
      if IO.select([client], nil, nil, 0.001)
        response = JSON.parse(client.gets)
        true
      else
        false
      end
    end

    assert_equal(
      {
        'jsonrpc' => '2.0',
        'error' => {
          'code' => -32_603,
          'message' => 'active model is unavailable',
          'data' => { 'success' => false }
        },
        'id' => 'failed-runtime-63'
      },
      response
    )
    assert_equal [Thread.current], sketchup.calling_threads
  ensure
    client&.close
  end

end


HeadlessTest.run(BridgeRuntimeTest)
