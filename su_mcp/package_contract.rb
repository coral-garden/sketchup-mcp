require 'fileutils'


module SU_MCP
  module PackageContract
    module_function

    def stage_catalog(repo_root:, staging_root:)
      source = File.join(repo_root, 'src', 'sketchup_mcp', 'command_catalog.json')
      packaged = File.join(staging_root, 'su_mcp', 'command_catalog.json')
      FileUtils.mkdir_p(File.dirname(packaged))
      FileUtils.copy_file(source, packaged)
      packaged
    end
  end
end
