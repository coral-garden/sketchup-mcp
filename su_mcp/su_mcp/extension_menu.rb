module SU_MCP
  module ExtensionMenu
    module_function

    def install(ui:, runtime:)
      menu = ui.menu('Plugins').add_submenu('SketchUp MCP')
      menu.add_item('Start Bridge') { runtime.start }
      menu.add_item('Stop Bridge') { runtime.stop }
      menu
    end
  end
end
