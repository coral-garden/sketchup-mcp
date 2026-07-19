require 'fileutils'
require 'tmpdir'


module SketchupMcpTestUp
  class UnsafeExportPath < StandardError; end

  class ExportSandbox
    EXPORT_DIRECTORY = /\Asketchup_export_[A-Za-z0-9_-]+\z/
    EXPORT_FILENAME = /\Amodel_[A-Za-z0-9_-]+\.(?:skp|obj|dae|stl|png|jpeg)\z/

    attr_reader :root

    def initialize(parent: Dir.tmpdir)
      @root = Dir.mktmpdir('sketchup_mcp_testup_', parent)
      @canonical_root = File.realpath(@root)
      @closed = false
    end

    def environment
      { 'TMP' => @root, 'TEMP' => @root }
    end

    def cleanup_export(path)
      target, export_directory = validate_export(path)
      File.delete(target)
      Dir.rmdir(export_directory) if Dir.empty?(export_directory)
      nil
    end

    def close
      return if @closed

      validate_owned_root!
      FileUtils.remove_entry_secure(@canonical_root)
      @closed = true
      nil
    end

    private

    def validate_export(path)
      validate_owned_root!
      candidate = File.expand_path(path.to_s)
      expected_prefix = "#{File.join(@canonical_root, 'sketchup_exports')}#{File::SEPARATOR}"
      unless candidate.start_with?(expected_prefix)
        raise UnsafeExportPath, 'export target is outside suite export root'
      end
      stat = File.lstat(candidate)
      unless stat.file? && !stat.symlink?
        raise UnsafeExportPath, 'export target must be a regular generated file'
      end

      target = File.realpath(candidate)
      export_directory = File.realpath(File.dirname(target))
      export_root = File.realpath(File.join(@canonical_root, 'sketchup_exports'))
      unless File.dirname(export_directory) == export_root
        raise UnsafeExportPath, 'export target is outside suite export root'
      end
      unless File.basename(export_directory).match?(EXPORT_DIRECTORY)
        raise UnsafeExportPath, 'export directory is not a generated SketchUp export'
      end
      unless File.basename(target).match?(EXPORT_FILENAME)
        raise UnsafeExportPath, 'export filename is not a generated SketchUp export'
      end

      [target, export_directory]
    rescue Errno::ENOENT, Errno::EACCES => error
      raise UnsafeExportPath, "export target cannot be validated (#{error.class})"
    end

    def validate_owned_root!
      stat = File.lstat(@root)
      valid = stat.directory? && !stat.symlink? && File.realpath(@root) == @canonical_root
      raise UnsafeExportPath, 'suite export root identity changed' unless valid
    rescue Errno::ENOENT, Errno::EACCES => error
      raise UnsafeExportPath, "suite export root cannot be validated (#{error.class})"
    end
  end
end
