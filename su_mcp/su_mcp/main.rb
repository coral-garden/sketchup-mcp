require 'sketchup'

require_relative 'bridge_listener'
require_relative 'bridge_runtime'
require_relative 'command_dispatcher'
require_relative 'command_executor'
require_relative 'sketchup_adapter'
require_relative 'sketchup_commands'


module SU_MCP
  class ConsoleLogger
    def call(message)
      SKETCHUP_CONSOLE.write("MCP: #{message}\n")
      STDOUT.flush
    rescue StandardError
      puts "MCP: #{message}"
    end
  end

  class UIScheduler
    def every(interval, &task)
      UI.start_timer(interval, true, &task)
    end

    def cancel(timer)
      UI.stop_timer(timer)
    end
  end

  class Server
    def initialize(
      port: BridgeListener.port_from_environment,
      scheduler: UIScheduler.new,
      logger: ConsoleLogger.new
    )
      commands = SketchupCommands.new(logger: logger)
      sketchup = SketchupAdapter.new(commands: commands)
      executor = CommandExecutor.new(sketchup: sketchup)
      dispatcher = CommandDispatcher.new(
        executor: executor,
        resources: sketchup.method(:list_resources)
      )
      listener = BridgeListener.new(
        port: port,
        handler: dispatcher.method(:call),
        logger: logger
      )
      @runtime = BridgeRuntime.new(
        listener: listener,
        scheduler: scheduler,
        logger: logger
      )
      @logger = logger
    end

    def start
      @runtime.start
      @logger.call('Bridge started and listening')
      self
    end

    def stop
      @runtime.stop
      @logger.call('Bridge stopped')
      self
    end
  end

  unless file_loaded?(__FILE__)
    logger = ConsoleLogger.new
    begin
      SKETCHUP_CONSOLE.show
    rescue StandardError
      begin
        Sketchup.send_action('showRubyPanel:')
      rescue StandardError
        UI.start_timer(0) { SKETCHUP_CONSOLE.show }
      end
    end

    @server = Server.new(logger: logger)
    menu = UI.menu('Plugins').add_submenu('MCP Server')
    menu.add_item('Start Server') { @server.start }
    menu.add_item('Stop Server') { @server.stop }
    file_loaded(__FILE__)
  end
end
