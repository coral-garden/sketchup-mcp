require 'tmpdir'

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
end


HeadlessTest.run(PackageContractTest)
