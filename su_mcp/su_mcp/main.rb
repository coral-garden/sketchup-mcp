require 'sketchup'

require_relative 'extension_menu'
require_relative 'extension_runtime'


module SU_MCP
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

    @extension_runtime = ExtensionRuntime.new(logger: logger)
    ExtensionMenu.install(ui: UI, runtime: @extension_runtime)
    file_loaded(__FILE__)
  end
end
