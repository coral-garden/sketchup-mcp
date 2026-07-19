require 'tmpdir'

require_relative '../testup/production_adapter/export_sandbox'
require_relative 'headless'


class ExportSandboxTest
  include HeadlessTest::Assertions

  def open_sandbox
    @parent = Dir.mktmpdir('sketchup_mcp_export_sandbox_test_')
    @sandbox = SketchupMcpTestUp::ExportSandbox.new(parent: @parent)
  end

  def teardown
    @sandbox.close if @sandbox
    FileUtils.remove_entry_secure(@parent) if @parent && File.directory?(@parent)
  end

  def test_cleanup_removes_only_a_valid_generated_export
    open_sandbox
    path = generated_export('model_20260720-123-abcdef.skp')

    @sandbox.cleanup_export(path)

    assert_equal false, File.exist?(path)
    assert_equal false, Dir.exist?(File.dirname(path))
    assert_equal true, Dir.exist?(@sandbox.root)
  end

  def test_cleanup_rejects_an_out_of_root_path_without_deleting_it
    open_sandbox
    outside = File.join(@parent, 'model_20260720-123-abcdef.skp')
    File.write(outside, 'must survive')

    error = assert_raises(SketchupMcpTestUp::UnsafeExportPath) do
      @sandbox.cleanup_export(outside)
    end

    assert_includes error.message, 'outside suite export root'
    assert_equal true, File.exist?(outside)
  end

  def test_cleanup_rejects_unexpected_directory_filename_and_extension
    open_sandbox
    cases = [
      ['not_generated/model_20260720-123-abcdef.skp', 'directory'],
      ['sketchup_export_safe/hostile.skp', 'filename'],
      ['sketchup_export_safe/model_20260720-123-abcdef.exe', 'filename']
    ]
    cases.each do |relative, message|
      path = File.join(@sandbox.root, 'sketchup_exports', relative)
      FileUtils.mkdir_p(File.dirname(path))
      File.write(path, 'must survive')

      error = assert_raises(SketchupMcpTestUp::UnsafeExportPath) do
        @sandbox.cleanup_export(path)
      end

      assert_includes error.message, message
      assert_equal true, File.exist?(path)
    end
  end

  def test_cleanup_rejects_a_symlink_escape_without_deleting_the_target
    open_sandbox
    outside = File.join(@parent, 'outside.skp')
    File.write(outside, 'must survive')
    export_directory = File.join(
      @sandbox.root, 'sketchup_exports', 'sketchup_export_safe'
    )
    FileUtils.mkdir_p(export_directory)
    link = File.join(export_directory, 'model_20260720-123-abcdef.skp')
    File.symlink(outside, link)

    assert_raises(SketchupMcpTestUp::UnsafeExportPath) do
      @sandbox.cleanup_export(link)
    end

    assert_equal true, File.exist?(outside)
  end

  def test_close_removes_the_exact_suite_owned_root_and_preserves_its_parent
    open_sandbox
    root = @sandbox.root
    sentinel = File.join(@parent, 'sentinel')
    File.write(sentinel, 'must survive')

    @sandbox.close
    @sandbox = nil

    assert_equal false, Dir.exist?(root)
    assert_equal true, File.exist?(sentinel)
  end

  private

  def generated_export(filename)
    directory = File.join(
      @sandbox.root, 'sketchup_exports', 'sketchup_export_20260720-123-abcdef'
    )
    FileUtils.mkdir_p(directory)
    path = File.join(directory, filename)
    File.write(path, 'suite export')
    path
  end
end


HeadlessTest.run(ExportSandboxTest)
