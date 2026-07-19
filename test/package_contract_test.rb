require_relative '../su_mcp/version'
require_relative 'headless'


class UnavailableVersionFilesystem
  def file?(_path)
    false
  end

  def read(_path, encoding:)
    raise "unexpected Project version read with #{encoding}"
  end
end


class PackageContractTest
  include HeadlessTest::Assertions

  def test_extension_runtime_version_matches_the_project_version
    repo_root = File.expand_path('..', __dir__)
    expected = File.read(File.join(repo_root, 'VERSION')).strip

    assert_equal expected, SU_MCP::VERSION
  end

  def test_single_loader_metadata_names_the_sketchup_extension_role
    repo_root = File.expand_path('..', __dir__)
    loader = File.read(File.join(repo_root, 'su_mcp.rb'))
    support_loaders = Dir[File.join(repo_root, 'su_mcp', '**', 'su_mcp.rb')]

    assert_equal [], support_loaders
    assert_includes loader, "SketchupExtension.new('SketchUp MCP', 'su_mcp/main')"
    assert_includes loader, 'SketchUp extension'
    assert_equal false, loader.include?('MCP Server')
    assert_equal false, loader.include?('MCP server')
  end

  def test_project_version_source_rejects_missing_project_and_package_files
    source = SU_MCP::VersionSource.new(filesystem: UnavailableVersionFilesystem.new)

    error = assert_raises(RuntimeError) do
      source.read(%w[packaged/VERSION project/VERSION])
    end

    assert_equal 'SketchUp MCP project version is unavailable', error.message
  end
end


HeadlessTest.run(PackageContractTest)
