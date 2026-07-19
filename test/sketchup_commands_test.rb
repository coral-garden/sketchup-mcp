require 'tmpdir'

require_relative '../su_mcp/sketchup_commands'
require_relative 'headless'


class ExportTestModel
  attr_reader :save_copy_paths, :export_paths

  def initialize(result: true)
    @result = result
    @save_copy_paths = []
    @export_paths = []
  end

  def save_copy(path)
    @save_copy_paths << path
    File.write(path, @result == true ? 'complete model' : 'partial model')
    raise @result if @result.is_a?(Exception)

    @result
  end

  def save(_path)
    raise 'save must not replace the active model path'
  end

  def export(path, _options)
    @export_paths << path
    File.write(path, @result == true ? 'complete export' : 'partial export')
    File.write("#{path}.sidecar", 'partial sidecar') unless @result == true
    raise @result if @result.is_a?(Exception)

    @result
  end
end


class SolidOperationResult
  attr_reader :entityID

  def initialize(id)
    @entityID = id
  end
end


class SolidCopy
  attr_reader :operations

  def initialize(result: nil)
    @result = result
    @operations = []
    @valid = true
  end

  def union(tool)
    @operations << [:union, tool]
    @result
  end

  def subtract(tool)
    @operations << [:subtract, tool]
    @result
  end

  def intersect(tool)
    @operations << [:intersect, tool]
    @result
  end

  def valid?
    @valid
  end

  def erase!
    @valid = false
  end
end


class OriginalSolid
  attr_reader :copy_value, :erase_count

  def initialize(copy_value)
    @copy_value = copy_value
    @erase_count = 0
  end

  def manifold?
    true
  end

  def copy
    @copy_value
  end

  def valid?
    true
  end

  def erase!
    @erase_count += 1
  end
end


class SolidTestModel
  def initialize(target:, tool:)
    @entities = { 1 => target, 2 => tool }
  end

  def find_entity_by_id(id)
    @entities[id]
  end
end


class SketchupCommandsTest
  include HeadlessTest::Assertions

  def test_skp_export_uses_save_copy_without_changing_the_active_model_path
    Dir.mktmpdir do |directory|
      with_tmp(directory) do
        model = ExportTestModel.new
        commands = SU_MCP::SketchupCommands.new(model: model)

        result = commands.call('export_scene', { 'format' => 'skp' })

        assert_equal true, result[:success]
        assert_equal 'skp', result[:format]
        assert_equal 1, model.save_copy_paths.length
        assert_equal true, File.exist?(result[:path])
      end
    end
  end

  def test_repeated_exports_always_receive_distinct_paths
    Dir.mktmpdir do |directory|
      with_tmp(directory) do
        model = ExportTestModel.new
        commands = SU_MCP::SketchupCommands.new(model: model)

        first = commands.call('export_scene', { 'format' => 'skp' })
        second = commands.call('export_scene', { 'format' => 'skp' })

        assert_equal 2, model.save_copy_paths.uniq.length
        assert_equal false, first[:path] == second[:path]
      end
    end
  end

  def test_failed_export_removes_its_partial_external_file
    Dir.mktmpdir do |directory|
      with_tmp(directory) do
        model = ExportTestModel.new(result: false)
        commands = SU_MCP::SketchupCommands.new(model: model)

        assert_raises(RuntimeError) { commands.call('export_scene', { 'format' => 'obj' }) }

        assert_equal 1, model.export_paths.length
        assert_equal false, File.exist?(model.export_paths.first)
        assert_equal false, Dir.exist?(File.dirname(model.export_paths.first))
      end
    end
  end

  def test_raised_export_failure_removes_its_partial_external_file
    Dir.mktmpdir do |directory|
      with_tmp(directory) do
        model = ExportTestModel.new(result: RuntimeError.new('exporter crashed'))
        commands = SU_MCP::SketchupCommands.new(model: model)

        assert_raises(RuntimeError) { commands.call('export_scene', { 'format' => 'skp' }) }

        assert_equal 1, model.save_copy_paths.length
        assert_equal false, File.exist?(model.save_copy_paths.first)
        assert_equal false, Dir.exist?(File.dirname(model.save_copy_paths.first))
      end
    end
  end

  def test_boolean_operations_use_the_official_solid_methods_on_copies
    { 'union' => :union, 'difference' => :subtract, 'intersection' => :intersect }.each do |name, method|
      result = SolidOperationResult.new(73)
      target_copy = SolidCopy.new(result: result)
      tool_copy = SolidCopy.new
      target = OriginalSolid.new(target_copy)
      tool = OriginalSolid.new(tool_copy)
      commands = SU_MCP::SketchupCommands.new(
        model: SolidTestModel.new(target: target, tool: tool)
      )

      command_result = commands.call(
        'boolean_operation',
        {
          'operation' => name,
          'target_id' => 1,
          'tool_id' => 2,
          'delete_originals' => false
        },
        solid_method: method
      )

      assert_equal({ success: true, id: 73 }, command_result)
      assert_equal [[method, tool_copy]], target_copy.operations
      assert_equal 0, target.erase_count
      assert_equal 0, tool.erase_count
    end
  end

  def test_boolean_operation_deletes_originals_only_after_solid_success
    result = SolidOperationResult.new(74)
    target = OriginalSolid.new(SolidCopy.new(result: result))
    tool = OriginalSolid.new(SolidCopy.new)
    commands = SU_MCP::SketchupCommands.new(
      model: SolidTestModel.new(target: target, tool: tool)
    )

    commands.call(
      'boolean_operation',
      {
        'operation' => 'difference',
        'target_id' => 1,
        'tool_id' => 2,
        'delete_originals' => true
      },
      solid_method: :subtract
    )

    assert_equal 1, target.erase_count
    assert_equal 1, tool.erase_count
  end

  def test_failed_boolean_result_keeps_originals
    target = OriginalSolid.new(SolidCopy.new(result: nil))
    tool = OriginalSolid.new(SolidCopy.new)
    commands = SU_MCP::SketchupCommands.new(
      model: SolidTestModel.new(target: target, tool: tool)
    )

    assert_raises(RuntimeError) do
      commands.call(
        'boolean_operation',
        {
          'operation' => 'intersection',
          'target_id' => 1,
          'tool_id' => 2,
          'delete_originals' => true
        },
        solid_method: :intersect
      )
    end

    assert_equal 0, target.erase_count
    assert_equal 0, tool.erase_count
  end

  private

  def with_tmp(directory)
    previous = ENV['TMP']
    ENV['TMP'] = directory
    yield
  ensure
    ENV['TMP'] = previous
  end
end


HeadlessTest.run(SketchupCommandsTest)
