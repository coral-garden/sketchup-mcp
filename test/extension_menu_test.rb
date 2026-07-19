require_relative '../su_mcp/su_mcp/extension_menu'
require_relative '../su_mcp/su_mcp/extension_runtime'
require_relative 'headless'


class RecordedMenu
  attr_reader :submenu_name, :item_names

  def initialize
    @items = {}
  end

  def add_submenu(name)
    @submenu_name = name
    self
  end

  def add_item(name, &action)
    @items[name] = action
  end

  def item_names
    @items.keys
  end

  def invoke(name)
    @items.fetch(name).call
  end
end


class RecordedSketchupUI
  attr_reader :menu_name, :menu

  def initialize
    @menu = RecordedMenu.new
  end

  def menu(name = nil)
    return @menu unless name

    @menu_name = name
    @menu
  end
end


class MenuScheduler
  def every(_interval, &_task) = :menu_timer
  def cancel(_timer); end
end


class ExtensionMenuTest
  include HeadlessTest::Assertions

  def teardown
    @extension_runtime&.stop
  end

  def test_sketchup_menu_names_the_extension_and_bridge_roles
    ui = RecordedSketchupUI.new
    @extension_runtime = SU_MCP::ExtensionRuntime.new(
      port: 0,
      scheduler: MenuScheduler.new,
      logger: ->(_message) {}
    )

    SU_MCP::ExtensionMenu.install(ui: ui, runtime: @extension_runtime)

    assert_equal 'Plugins', ui.menu_name
    assert_equal 'SketchUp MCP', ui.menu.submenu_name
    assert_equal ['Start Bridge', 'Stop Bridge'], ui.menu.item_names
    assert_equal @extension_runtime, ui.menu.invoke('Start Bridge')
    assert_equal @extension_runtime, ui.menu.invoke('Stop Bridge')
  end
end


HeadlessTest.run(ExtensionMenuTest)
