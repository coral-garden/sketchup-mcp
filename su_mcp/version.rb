module SU_MCP
  VERSION_FILES = [
    File.join(__dir__, 'VERSION'),
    File.expand_path('../VERSION', __dir__)
  ].freeze
  VERSION_FILE = VERSION_FILES.find { |path| File.file?(path) }
  raise 'SketchUp MCP project version is unavailable' unless VERSION_FILE

  VERSION = File.read(VERSION_FILE, encoding: 'UTF-8').strip.freeze

  private_constant :VERSION_FILES, :VERSION_FILE
end
