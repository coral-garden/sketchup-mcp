# Static startup harness for the protected clean-install acceptance run.
require 'json'
require 'time'

module SketchupMCPInstallAcceptance
  START_TIMEOUT = 60.0
  POLL_INTERVAL = 0.1

  module_function

  def write_json(path, document)
    temporary = "#{path}.tmp"
    File.write(temporary, JSON.pretty_generate(document) + "\n", mode: 'wb')
    File.rename(temporary, path)
  end

  def regular_input(path)
    stat = File.lstat(path)
    raise 'runtime_input_symlink' if stat.symlink?
    raise 'runtime_input_not_regular' unless stat.file?

    JSON.parse(File.binread(path))
  end

  def marker(identity, kind, values = {})
    {
      'schema_version' => 1,
      'kind' => "sketchup_mcp.install_acceptance.#{kind}",
      'run_id' => identity.fetch('run_id'),
      'created_at' => Time.now.iso8601
    }.merge(values)
  end

  def quit_sketchup
    Sketchup.quit
  rescue StandardError
    Sketchup.send_action('exit:')
  end

  def stop(identity, runtime, timer, status: 'stopped', error: nil)
    UI.stop_timer(timer)
    runtime.stop
    values = { 'status' => status }
    values['error'] = error unless error.nil?
    write_json(
      File.join(__dir__, 'bridge-exit.json'),
      marker(identity, 'exit', values)
    )
    quit_sketchup
  rescue StandardError => error
    write_json(
      File.join(__dir__, 'bridge-exit.json'),
      marker(identity, 'exit', 'status' => 'failure', 'error' => error.class.name)
    )
    quit_sketchup
  end

  def start
    identity = regular_input(File.join(__dir__, 'runtime-input.json'))
    unless identity.fetch('kind') == 'sketchup_mcp.install_acceptance.prepared'
      raise 'runtime_input_kind_differs'
    end
    unless identity.fetch('bridge_host') == '127.0.0.1'
      raise 'bridge_host_differs'
    end
    unless Integer(ENV.fetch('SKETCHUP_MCP_BRIDGE_PORT'), 10) == identity.fetch('bridge_port')
      raise 'bridge_port_differs'
    end

    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + START_TIMEOUT
    waiting_timer = nil
    waiting_timer = UI.start_timer(POLL_INTERVAL, true) do
      if defined?(SU_MCP) && SU_MCP.respond_to?(:extension_runtime)
        UI.stop_timer(waiting_timer)
        runtime = SU_MCP.extension_runtime
        Sketchup.active_model.selection.clear
        runtime.start
        write_json(
          File.join(__dir__, 'bridge-ready.json'),
          marker(
            identity,
            'ready',
            'commit' => identity.fetch('commit'),
            'version' => identity.fetch('version'),
            'catalog_sha256' => identity.fetch('catalog_sha256'),
            'port' => identity.fetch('bridge_port'),
            'sketchup_version' => Sketchup.version,
            'os_version' => identity.fetch('os_version')
          )
        )
        stop_path = File.join(__dir__, 'stop')
        stop_timer = nil
        stop_timer = UI.start_timer(POLL_INTERVAL, true) do
          next unless File.file?(stop_path) && !File.symlink?(stop_path)

          expected_stop = "#{identity.fetch('run_id')}\n"
          if File.binread(stop_path) == expected_stop
            stop(identity, runtime, stop_timer)
          else
            stop(
              identity,
              runtime,
              stop_timer,
              status: 'failure',
              error: 'stop_marker_identity_differs'
            )
          end
        end
      elsif Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
        UI.stop_timer(waiting_timer)
        write_json(
          File.join(__dir__, 'bridge-exit.json'),
          marker(identity, 'exit', 'status' => 'failure', 'error' => 'extension_runtime_timeout')
        )
        quit_sketchup
      end
    end
  rescue StandardError => error
    identity ||= { 'run_id' => 'unavailable' }
    write_json(
      File.join(__dir__, 'bridge-exit.json'),
      marker(identity, 'exit', 'status' => 'failure', 'error' => error.class.name)
    )
    quit_sketchup
  end
end

SketchupMCPInstallAcceptance.start
