module SU_MCP
  class VersionSource
    def initialize(filesystem: File)
      @filesystem = filesystem
    end

    def read(candidates)
      path = candidates.find { |candidate| @filesystem.file?(candidate) }
      raise 'SketchUp MCP project version is unavailable' unless path

      @filesystem.read(path, encoding: 'UTF-8').strip
    end
  end

  VERSION_FILES = [
    File.join(__dir__, 'VERSION'),
    File.expand_path('../VERSION', __dir__)
  ].freeze
  VERSION = VersionSource.new.read(VERSION_FILES).freeze

  private_constant :VERSION_FILES
end
