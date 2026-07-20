require 'sketchup.rb'
require 'extensions.rb'

require_relative 'su_mcp/version'

module SU_MCP
  unless file_loaded?(__FILE__)
    extension = SketchupExtension.new('SketchUp MCP', 'su_mcp/main')
    extension.description = 'SketchUp extension that runs the local command bridge'
    extension.version = SU_MCP::VERSION
    extension.copyright = '2024'
    extension.creator = 'Coral Garden'
    Sketchup.register_extension(extension, true)
    file_loaded(__FILE__)
  end
end
