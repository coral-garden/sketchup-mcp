require_relative '../su_mcp/sketchup_adapter'
require_relative 'headless'


class JoineryAdapterEntity
  def initialize(solid: true)
    @solid = solid
  end

  def manifold? = @solid
  def subtract(_other); end
  def union(_other); end
end


class JoineryAdapterModel
  attr_reader :trace

  def initialize(entities: nil)
    @trace = []
    @entities = entities || { 1 => JoineryAdapterEntity.new, 2 => JoineryAdapterEntity.new }
  end

  def find_entity_by_id(id)
    @trace << [:find, id]
    @entities[id]
  end

  def start_operation(name, disable_ui)
    @trace << [:start, name, disable_ui]
  end

  def commit_operation = @trace << [:commit]
  def abort_operation = @trace << [:abort]
end


class JoineryAdapterCommands
  attr_reader :trace

  def initialize(trace:, failure: nil)
    @trace = trace
    @failure = failure
  end

  def create_mortise_tenon(**arguments)
    record(:create_mortise_tenon, arguments, { success: true, mortise_id: 11, tenon_id: 12 })
  end

  def create_dovetail(**arguments)
    record(:create_dovetail, arguments, { success: true, tail_id: 13, pin_id: 14 })
  end

  def create_finger_joint(**arguments)
    record(:create_finger_joint, arguments, { success: true, board1_id: 15, board2_id: 16 })
  end

  def eval_ruby(**arguments)
    record(:eval_ruby, arguments, { success: true, result: 3, result_type: 'Integer' })
  end

  private

  def record(name, arguments, result)
    @trace << [:command, name, arguments]
    raise @failure if @failure

    result
  end
end


class JoineryEvalAdapterTest
  include HeadlessTest::Assertions

  def test_every_command_owns_exactly_one_successful_model_operation
    invocations.each do |name, invocation|
      model = JoineryAdapterModel.new
      commands = JoineryAdapterCommands.new(trace: model.trace)
      adapter = SU_MCP::SketchupAdapter.new(commands: commands, model: model)

      invocation.call(adapter)

      assert_equal 1, model.trace.count { |event| event.first == :start }
      assert_equal [:commit], model.trace.last
      assert_equal 0, model.trace.count { |event| event.first == :abort }
      assert_equal name, model.trace.find { |event| event.first == :command }[1]
      assert_operator model.trace.rindex { |event| event.first == :find }, :<,
                      model.trace.index { |event| event.first == :start } unless name == :eval_ruby
    end
  end

  def test_missing_or_unsupported_entities_never_start_an_operation
    [
      JoineryAdapterModel.new(entities: { 1 => JoineryAdapterEntity.new }),
      JoineryAdapterModel.new(
        entities: { 1 => JoineryAdapterEntity.new(solid: false), 2 => JoineryAdapterEntity.new }
      )
    ].each do |model|
      commands = JoineryAdapterCommands.new(trace: model.trace)
      adapter = SU_MCP::SketchupAdapter.new(commands: commands, model: model)

      assert_raises(SU_MCP::CommandExecutionError) do
        adapter.create_mortise_tenon(
          mortise_id: 1, tenon_id: 2, width: 1, height: 1, depth: 1,
          offset_x: 0, offset_y: 0, offset_z: 0
        )
      end

      assert_equal 0, model.trace.count { |event| event.first == :start }
      assert_equal 0, model.trace.count { |event| event.first == :command }
    end
  end

  def test_post_start_standard_and_script_failures_abort_without_committing
    [RuntimeError.new('geometry failed'), SyntaxError.new('secret source')].each do |failure|
      model = JoineryAdapterModel.new
      commands = JoineryAdapterCommands.new(trace: model.trace, failure: failure)
      adapter = SU_MCP::SketchupAdapter.new(commands: commands, model: model)

      assert_raises(failure.class) do
        if failure.is_a?(ScriptError)
          adapter.eval_ruby(code: 'secret source')
        else
          invocations[:create_dovetail].call(adapter)
        end
      end

      assert_equal 1, model.trace.count { |event| event.first == :start }
      assert_equal 0, model.trace.count { |event| event.first == :commit }
      assert_equal [:abort], model.trace.last
    end
  end

  private

  def invocations
    common = { width: 1, height: 1, depth: 1, offset_x: 0, offset_y: 0, offset_z: 0 }
    {
      create_mortise_tenon: lambda do |adapter|
        adapter.create_mortise_tenon(**common, mortise_id: 1, tenon_id: 2)
      end,
      create_dovetail: lambda do |adapter|
        adapter.create_dovetail(
          **common, tail_id: 1, pin_id: 2, angle: 15, num_tails: 3
        )
      end,
      create_finger_joint: lambda do |adapter|
        adapter.create_finger_joint(
          **common, board1_id: 1, board2_id: 2, num_fingers: 5
        )
      end,
      eval_ruby: ->(adapter) { adapter.eval_ruby(code: '1 + 2') }
    }
  end
end


HeadlessTest.run(JoineryEvalAdapterTest)
