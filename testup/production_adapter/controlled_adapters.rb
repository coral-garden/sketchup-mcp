class ControlledSolidEntity
  attr_reader :parent

  def initialize(parent: nil, solid: true)
    @parent = parent
    @solid = solid
  end

  def manifold? = @solid
  def copy; end
  def union(_other); end
  def subtract(_other); end
  def intersect(_other); end
end


class ParentlessControlledSolid
  def manifold? = true
  def copy; end
  def union(_other); end
  def subtract(_other); end
  def intersect(_other); end
end


class OperationLifecycleModel
  attr_reader :abort_count, :materials

  def initialize(entities: {}, start_error: nil, abort_error_once: false)
    @entities = entities
    @start_error = start_error
    @abort_error_once = abort_error_once
    @abort_count = 0
    @materials = {}
  end

  def find_entity_by_id(id) = @entities[id]

  def start_operation(_name, _disable_ui)
    raise @start_error if @start_error

    true
  end

  def commit_operation = true

  def abort_operation
    @abort_count += 1
    if @abort_error_once
      @abort_error_once = false
      raise 'controlled abort failure'
    end
    true
  end
end


class ControlledSketchupCommands
  attr_accessor :call_result, :failure

  def initialize
    @call_result = { success: true }
  end

  def call(_name, _arguments, solid_method: nil)
    raise @failure if @failure

    @call_result.merge(solid_method: solid_method).compact
  end

  def create_mortise_tenon(**_arguments)
    { success: true, mortise_id: 1, tenon_id: 2 }
  end

  def create_dovetail(**_arguments)
    { success: true, tail_id: 1, pin_id: 2 }
  end

  def create_finger_joint(**_arguments)
    { success: true, board1_id: 1, board2_id: 2 }
  end

  def eval_ruby(code:)
    raise @failure if @failure

    { success: true, result: code, result_type: 'String' }
  end

  def list_resources = []
end
