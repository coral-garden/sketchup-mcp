require_relative 'bridge_listener'
require_relative 'bridge_runtime'
require_relative 'command_dispatcher'
require_relative 'command_executor'
require_relative 'sketchup_adapter'
require_relative 'sketchup_commands'


module SU_MCP
  class ConsoleLogger
    def call(message)
      SKETCHUP_CONSOLE.write("SketchUp MCP: #{message}\n")
      STDOUT.flush
    rescue StandardError
      puts "SketchUp MCP: #{message}"
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

  class ExtensionRuntime
    def initialize(
      port: BridgeListener.port_from_environment,
      scheduler: UIScheduler.new,
      logger: ConsoleLogger.new
    )
      commands = SketchupCommands.new
      adapter = SketchupAdapter.new(commands: commands)
      executor = CommandExecutor.new(adapter: adapter, logger: logger)
      dispatcher = CommandDispatcher.new(executor: executor)
      listener = BridgeListener.new(
        port: port,
        handler: dispatcher.method(:call),
        logger: logger
      )
      @bridge_runtime = BridgeRuntime.new(
        listener: listener,
        scheduler: scheduler,
        logger: logger
      )
      @logger = logger
    end

    def start
      @bridge_runtime.start
      @logger.call('Extension runtime: bridge started')
      self
    end

    def stop
      @bridge_runtime.stop
      @logger.call('Extension runtime: bridge stopped')
      self
    end
  end
end
