require 'sketchup'
require 'extensions'

module SU_MCP
  unless file_loaded?(__FILE__)
    ext = SketchupExtension.new('SketchUp MCP', 'su_mcp/main')
    ext.description = 'SketchUp extension that runs the local command bridge'
    ext.version     = '1.5.0'
    ext.copyright   = '2024'
    ext.creator     = 'MCP Team'

    Sketchup.register_extension(ext, true)

    file_loaded(__FILE__)
  end
end
