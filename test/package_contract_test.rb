require 'tmpdir'
require 'json'

require_relative '../su_mcp/package_contract'
require_relative 'headless'


class PackageContractTest
  include HeadlessTest::Assertions

  def test_staged_extension_contains_the_exact_authoritative_catalog_bytes
    repo_root = File.expand_path('..', __dir__)
    source = File.join(repo_root, 'src', 'sketchup_mcp', 'command_catalog.json')

    Dir.mktmpdir do |staging_root|
      packaged = SU_MCP::PackageContract.stage_catalog(
        repo_root: repo_root,
        staging_root: staging_root
      )

      assert_equal File.join(staging_root, 'su_mcp', 'command_catalog.json'), packaged
      assert_equal File.binread(source), File.binread(packaged)
    end
  end

  def test_user_visible_extension_metadata_does_not_claim_to_be_the_mcp_server
    repo_root = File.expand_path('..', __dir__)
    metadata = JSON.parse(
      File.read(File.join(repo_root, 'su_mcp', 'extension.json'))
    )
    loaders = %w[su_mcp.rb su_mcp/su_mcp.rb].map do |relative_path|
      File.read(File.join(repo_root, relative_path))
    end.join("\n")

    assert_equal 'SketchUp MCP', metadata.fetch('name')
    assert_includes metadata.fetch('description'), 'SketchUp extension'
    assert_equal false, metadata.fetch('description').include?('server')
    assert_equal false, loaders.include?('MCP Server')
    assert_equal false, loaders.include?('MCP server')
  end
end


HeadlessTest.run(PackageContractTest)
