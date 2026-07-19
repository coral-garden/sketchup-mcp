require 'json'
require 'fileutils'
require 'tmpdir'
require 'tempfile'
require_relative 'command_catalog'
require_relative 'command_execution_error'
require_relative 'eval_result'

module SU_MCP
  # SketchUp API implementation retained behind the adapter seam while issues
  # #10 and #11 migrate the remaining command families.
  class SketchupCommands
    COMMAND_METHODS = {
      'create_component' => :create_component,
      'delete_component' => :delete_component,
      'transform_component' => :transform_component,
      'get_selection' => :get_selection,
      'export_scene' => :export_scene,
      'set_material' => :set_material,
      'boolean_operation' => :boolean_operation,
      'create_mortise_tenon' => :create_mortise_tenon,
      'create_dovetail' => :create_dovetail,
      'create_finger_joint' => :create_finger_joint,
      'eval_ruby' => :eval_ruby
    }.freeze

    def initialize(logger: nil, model: nil)
      @logger = logger || ->(_message) {}
      @model = model
    end

    def call(name, arguments, solid_method: nil)
      command_method = COMMAND_METHODS[name]
      raise ArgumentError, "Unknown command: #{name}" unless command_method

      return send(command_method) if command_method == :get_selection
      if command_method == :boolean_operation
        return boolean_operation(arguments || {}, solid_method: solid_method)
      end
      migrated = %i[
        create_mortise_tenon create_dovetail create_finger_joint eval_ruby
      ]
      if migrated.include?(command_method)
        keywords = (arguments || {}).transform_keys(&:to_sym)
        return public_send(command_method, **keywords)
      end

      send(command_method, arguments || {})
    end

    def command?(name)
      COMMAND_METHODS.key?(name)
    end

    def list_resources
      model = @model || Sketchup.active_model
      return [] unless model

      model.entities.map do |entity|
        { id: entity.entityID, type: entity.typename.downcase }
      end
    end

    private

    def log(message)
      @logger.call(message)
    end

    def create_component(params)
      log "Creating component with params: #{params.inspect}"
      model = @model || Sketchup.active_model
      log "Got active model: #{model.inspect}"
      entities = model.active_entities
      log "Got active entities: #{entities.inspect}"

      pos = params["position"] || [0,0,0]
      dims = params["dimensions"] || [1,1,1]

      case params["type"]
      when "cube"
        log "Creating cube at position #{pos.inspect} with dimensions #{dims.inspect}"

        begin
          group = entities.add_group
          log "Created group: #{group.inspect}"

          face = group.entities.add_face(
            [pos[0], pos[1], pos[2]],
            [pos[0] + dims[0], pos[1], pos[2]],
            [pos[0] + dims[0], pos[1] + dims[1], pos[2]],
            [pos[0], pos[1] + dims[1], pos[2]]
          )
          log "Created face: #{face.inspect}"

          face.pushpull(dims[2])
          log "Pushed/pulled face by #{dims[2]}"

          result = {
            id: group.entityID,
            success: true
          }
          log "Returning result: #{result.inspect}"
          result
        rescue StandardError => e
          log "Error in create_component: #{e.message}"
          log e.backtrace.join("\n")
          raise
        end
      when "cylinder"
        log "Creating cylinder at position #{pos.inspect} with dimensions #{dims.inspect}"

        begin
          # Create a group to contain the cylinder
          group = entities.add_group

          # Extract dimensions
          radius = dims[0] / 2.0
          height = dims[2]

          # Create a circle at the base
          center = [pos[0] + radius, pos[1] + radius, pos[2]]

          # Create points for a circle
          num_segments = 24  # Number of segments for the circle
          circle_points = []

          num_segments.times do |i|
            angle = Math::PI * 2 * i / num_segments
            x = center[0] + radius * Math.cos(angle)
            y = center[1] + radius * Math.sin(angle)
            z = center[2]
            circle_points << [x, y, z]
          end

          # Create the circular face
          face = group.entities.add_face(circle_points)

          # Extrude the face to create the cylinder
          face.pushpull(height)

          result = {
            id: group.entityID,
            success: true
          }
          log "Created cylinder, returning result: #{result.inspect}"
          result
        rescue StandardError => e
          log "Error creating cylinder: #{e.message}"
          log e.backtrace.join("\n")
          raise
        end
      when "sphere"
        log "Creating sphere at position #{pos.inspect} with dimensions #{dims.inspect}"

        begin
          # Create a group to contain the sphere
          group = entities.add_group

          # Extract dimensions
          radius = dims[0] / 2.0
          center = [pos[0] + radius, pos[1] + radius, pos[2] + radius]

          # Use SketchUp's built-in sphere method if available
          if Sketchup::Tools.respond_to?(:create_sphere)
            Sketchup::Tools.create_sphere(center, radius, 24, group.entities)
          else
            # Fallback implementation using polygons
            # Create a UV sphere with latitude and longitude segments
            segments = 16

            # Create points for the sphere
            points = []
            for lat_i in 0..segments
              lat = Math::PI * lat_i / segments
              for lon_i in 0..segments
                lon = 2 * Math::PI * lon_i / segments
                x = center[0] + radius * Math.sin(lat) * Math.cos(lon)
                y = center[1] + radius * Math.sin(lat) * Math.sin(lon)
                z = center[2] + radius * Math.cos(lat)
                points << [x, y, z]
              end
            end

            # Create faces for the sphere (simplified approach)
            for lat_i in 0...segments
              for lon_i in 0...segments
                i1 = lat_i * (segments + 1) + lon_i
                i2 = i1 + 1
                i3 = i1 + segments + 1
                i4 = i3 + 1

                # Create a quad face
                begin
                  group.entities.add_face(points[i1], points[i2], points[i4], points[i3])
                rescue StandardError => e
                  # Skip faces that can't be created (may happen at poles)
                  log "Skipping face: #{e.message}"
                end
              end
            end
          end

          result = {
            id: group.entityID,
            success: true
          }
          log "Created sphere, returning result: #{result.inspect}"
          result
        rescue StandardError => e
          log "Error creating sphere: #{e.message}"
          log e.backtrace.join("\n")
          raise
        end
      when "cone"
        log "Creating cone at position #{pos.inspect} with dimensions #{dims.inspect}"

        begin
          # Create a group to contain the cone
          group = entities.add_group

          # Extract dimensions
          radius = dims[0] / 2.0
          height = dims[2]

          # Create a circle at the base
          center = [pos[0] + radius, pos[1] + radius, pos[2]]
          apex = [center[0], center[1], center[2] + height]

          # Create points for a circle
          num_segments = 24  # Number of segments for the circle
          circle_points = []

          num_segments.times do |i|
            angle = Math::PI * 2 * i / num_segments
            x = center[0] + radius * Math.cos(angle)
            y = center[1] + radius * Math.sin(angle)
            z = center[2]
            circle_points << [x, y, z]
          end

          # Create the circular face for the base
          base = group.entities.add_face(circle_points)

          # Create the cone sides
          (0...num_segments).each do |i|
            j = (i + 1) % num_segments
            # Create a triangular face from two adjacent points on the circle to the apex
            group.entities.add_face(circle_points[i], circle_points[j], apex)
          end

          result = {
            id: group.entityID,
            success: true
          }
          log "Created cone, returning result: #{result.inspect}"
          result
        rescue StandardError => e
          log "Error creating cone: #{e.message}"
          log e.backtrace.join("\n")
          raise
        end
      else
        raise "Unknown component type: #{params["type"]}"
      end
    end

    def delete_component(params)
      model = @model || Sketchup.active_model

      # Handle ID format - strip quotes if present
      id_str = params["id"].to_s.gsub('"', '')
      log "Looking for entity with ID: #{id_str}"

      entity = model.find_entity_by_id(id_str.to_i)

      if entity
        log "Found entity: #{entity.inspect}"
        entity.erase!
        { success: true }
      else
        raise "Entity not found"
      end
    end

    def transform_component(params)
      model = @model || Sketchup.active_model

      # Handle ID format - strip quotes if present
      id_str = params["id"].to_s.gsub('"', '')
      log "Looking for entity with ID: #{id_str}"

      entity = model.find_entity_by_id(id_str.to_i)

      if entity
        log "Found entity: #{entity.inspect}"

        # Handle position
        if params["position"]
          pos = params["position"]
          log "Transforming position to #{pos.inspect}"

          # Create a transformation to move the entity
          translation = Geom::Transformation.translation(Geom::Point3d.new(pos[0], pos[1], pos[2]))
          entity.transform!(translation)
        end

        # Handle rotation (in degrees)
        if params["rotation"]
          rot = params["rotation"]
          log "Rotating by #{rot.inspect} degrees"

          # Convert to radians
          x_rot = rot[0] * Math::PI / 180
          y_rot = rot[1] * Math::PI / 180
          z_rot = rot[2] * Math::PI / 180

          # Apply rotations
          if rot[0] != 0
            rotation = Geom::Transformation.rotation(entity.bounds.center, Geom::Vector3d.new(1, 0, 0), x_rot)
            entity.transform!(rotation)
          end

          if rot[1] != 0
            rotation = Geom::Transformation.rotation(entity.bounds.center, Geom::Vector3d.new(0, 1, 0), y_rot)
            entity.transform!(rotation)
          end

          if rot[2] != 0
            rotation = Geom::Transformation.rotation(entity.bounds.center, Geom::Vector3d.new(0, 0, 1), z_rot)
            entity.transform!(rotation)
          end
        end

        # Handle scale
        if params["scale"]
          scale = params["scale"]
          log "Scaling by #{scale.inspect}"

          # Create a transformation to scale the entity
          center = entity.bounds.center
          scaling = Geom::Transformation.scaling(center, scale[0], scale[1], scale[2])
          entity.transform!(scaling)
        end

        { success: true, id: entity.entityID }
      else
        raise "Entity not found"
      end
    end

    def get_selection
      model = @model || Sketchup.active_model
      selection = model.selection

      log "Getting selection, count: #{selection.length}"

      selected_entities = selection.map do |entity|
        {
          id: entity.entityID,
          type: entity.typename.downcase
        }
      end

      { success: true, entities: selected_entities }
    end

    def export_scene(params)
      log "Exporting scene with params: #{params.inspect}"
      model = @model || Sketchup.active_model

      format = params["format"] || "skp"

      begin
        # Create a temporary directory for exports
        temp_dir = File.join(ENV['TEMP'] || ENV['TMP'] || Dir.tmpdir, "sketchup_exports")
        FileUtils.mkdir_p(temp_dir) unless Dir.exist?(temp_dir)
        export_dir = Dir.mktmpdir('sketchup_export_', temp_dir)

        extension = format == 'jpg' ? 'jpeg' : format
        reservation = Tempfile.new(['model_', ".#{extension}"], export_dir)
        export_path = reservation.path
        reservation.close
        reservation.unlink

        case format.downcase
        when "skp"
          # Export as SketchUp file
          log "Exporting to SketchUp file: #{export_path}"
          unless model.respond_to?(:save_copy) && model.save_copy(export_path)
            raise 'SketchUp save-copy failed'
          end

        when "obj"
          # Export as OBJ file
          log "Exporting to OBJ file: #{export_path}"

          # Check if OBJ exporter is available
          options = {
            :triangulated_faces => true,
            :double_sided_faces => true,
            :edges => false,
            :texture_maps => true
          }
          raise 'OBJ export failed' unless model.export(export_path, options)

        when "dae"
          # Export as COLLADA file
          log "Exporting to COLLADA file: #{export_path}"

          # Check if COLLADA exporter is available
          options = { :triangulated_faces => true }
          raise 'COLLADA export failed' unless model.export(export_path, options)

        when "stl"
          # Export as STL file
          log "Exporting to STL file: #{export_path}"

          # Check if STL exporter is available
          options = { :units => "model" }
          raise 'STL export failed' unless model.export(export_path, options)

        when "png", "jpg", "jpeg"
          # Export as image
          ext = extension
          log "Exporting to image file: #{export_path}"

          # Get the current view
          view = model.active_view

          # Set up options for the export
          options = {
            :filename => export_path,
            :width => params["width"] || 1920,
            :height => params["height"] || 1080,
            :antialias => true,
            :transparent => (ext == "png")
          }

          # Export the image
          raise 'image export failed' unless view.write_image(options)

        else
          raise "Unsupported export format: #{format}"
        end

        log "Export completed successfully to: #{export_path}"

        {
          success: true,
          path: export_path,
          format: format
        }
      rescue StandardError => e
        FileUtils.rm_rf(export_dir) if defined?(export_dir) && export_dir
        log "Error in export_scene: #{e.message}"
        log e.backtrace.join("\n")
        raise
      end
    end

    def set_material(params)
      log "Setting material with params: #{params.inspect}"
      model = @model || Sketchup.active_model

      # Handle ID format - strip quotes if present
      id_str = params["id"].to_s.gsub('"', '')
      log "Looking for entity with ID: #{id_str}"

      entity = model.find_entity_by_id(id_str.to_i)

      if entity
        log "Found entity: #{entity.inspect}"

        material_name = params["material"]
        log "Setting material to: #{material_name}"

        # Get or create the material
        material = model.materials[material_name]
        if !material
          # Create a new material if it doesn't exist
          material = model.materials.add(material_name)

          # Handle color specification
          case material_name.downcase
          when "red"
            material.color = Sketchup::Color.new(255, 0, 0)
          when "green"
            material.color = Sketchup::Color.new(0, 255, 0)
          when "blue"
            material.color = Sketchup::Color.new(0, 0, 255)
          when "yellow"
            material.color = Sketchup::Color.new(255, 255, 0)
          when "cyan", "turquoise"
            material.color = Sketchup::Color.new(0, 255, 255)
          when "magenta", "purple"
            material.color = Sketchup::Color.new(255, 0, 255)
          when "white"
            material.color = Sketchup::Color.new(255, 255, 255)
          when "black"
            material.color = Sketchup::Color.new(0, 0, 0)
          when "brown"
            material.color = Sketchup::Color.new(139, 69, 19)
          when "orange"
            material.color = Sketchup::Color.new(255, 165, 0)
          when "gray", "grey"
            material.color = Sketchup::Color.new(128, 128, 128)
          else
            # If it's a hex color code like "#FF0000"
            if material_name.start_with?("#") && material_name.length == 7
              begin
                r = material_name[1..2].to_i(16)
                g = material_name[3..4].to_i(16)
                b = material_name[5..6].to_i(16)
                material.color = Sketchup::Color.new(r, g, b)
              rescue
                # Default to a wood color if parsing fails
                material.color = Sketchup::Color.new(184, 134, 72)
              end
            else
              # Default to a wood color
              material.color = Sketchup::Color.new(184, 134, 72)
            end
          end
        end

        # Apply the material to the entity
        if entity.respond_to?(:material=)
          entity.material = material
        elsif entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)
          # For groups and components, we need to apply to all faces
          entities = entity.is_a?(Sketchup::Group) ? entity.entities : entity.definition.entities
          entities.grep(Sketchup::Face).each { |face| face.material = material }
        end

        { success: true, id: entity.entityID }
      else
        raise "Entity not found"
      end
    end

    def boolean_operation(params, solid_method:)
      log "Performing boolean operation with params: #{params.inspect}"
      model = @model || Sketchup.active_model
      operation_type = params["operation"]
      raise "SketchUp solid #{operation_type} is unavailable" unless solid_method
      target_entity = model.find_entity_by_id(params["target_id"])
      tool_entity = model.find_entity_by_id(params["tool_id"])

      unless target_entity && tool_entity
        missing = []
        missing << "target" unless target_entity
        missing << "tool" unless tool_entity
        raise "Entity not found: #{missing.join(', ')}"
      end

      [target_entity, tool_entity].each do |entity|
        unless entity.respond_to?(:manifold?) && entity.manifold? && entity.respond_to?(:copy)
          raise 'Boolean operations require solid groups'
        end
      end

      target_copy = target_entity.copy
      tool_copy = tool_entity.copy
      unless target_copy.respond_to?(solid_method)
        raise "SketchUp solid #{operation_type} is unavailable"
      end
      result_group = target_copy.public_send(solid_method, tool_copy)
      raise "SketchUp solid #{operation_type} failed" unless result_group

      if params["delete_originals"]
        target_entity.erase! if target_entity.valid?
        tool_entity.erase! if tool_entity.valid?
      end
      [target_copy, tool_copy].each do |copy|
        copy.erase! if !copy.equal?(result_group) && copy.respond_to?(:valid?) && copy.valid?
      end

      { success: true, id: result_group.entityID }
    end

    public

    def create_mortise_tenon(mortise_id:, tenon_id:, **joint)
      results = create_matching_joint(
        first_id: mortise_id,
        second_id: tenon_id,
        pattern: :mortise_tenon,
        first_method: :subtract,
        second_method: :union,
        **joint
      )
      {
        success: true,
        mortise_id: results[0].entityID,
        tenon_id: results[1].entityID
      }
    end

    def create_dovetail(tail_id:, pin_id:, angle:, num_tails:, **joint)
      results = create_matching_joint(
        first_id: tail_id,
        second_id: pin_id,
        pattern: :dovetail,
        first_method: :union,
        second_method: :subtract,
        angle: angle,
        count: num_tails,
        **joint
      )
      {
        success: true,
        tail_id: results[0].entityID,
        pin_id: results[1].entityID
      }
    end

    def create_finger_joint(board1_id:, board2_id:, num_fingers:, **joint)
      results = create_matching_joint(
        first_id: board1_id,
        second_id: board2_id,
        pattern: :finger,
        first_method: :union,
        second_method: :subtract,
        count: num_fingers,
        **joint
      )
      {
        success: true,
        board1_id: results[0].entityID,
        board2_id: results[1].entityID
      }
    end

    private

    def create_matching_joint(
      first_id:, second_id:, pattern:, first_method:, second_method:,
      width:, height:, depth:, offset_x:, offset_y:, offset_z:,
      count: 1, angle: 0
    )
      model = @model || Sketchup.active_model
      first = prepare_joinery_solid(model.find_entity_by_id(first_id))
      second = prepare_joinery_solid(model.find_entity_by_id(second_id))
      unless first && second
        raise CommandExecutionError.new(
          'Joinery entity was not found',
          kind: 'entity_not_found'
        )
      end
      if first.respond_to?(:parent) && second.respond_to?(:parent) &&
         first.parent && second.parent && !first.parent.equal?(second.parent)
        raise CommandExecutionError.new(
          'Joinery entities must share a modeling context',
          kind: 'incompatible_entity_context'
        )
      end

      owner = first.respond_to?(:parent) ? first.parent : nil
      parent_entities = if owner.respond_to?(:add_group)
                          owner
                        elsif owner.respond_to?(:entities)
                          owner.entities
                        else
                          model.active_entities
                        end
      joint = {
        width: width,
        height: height,
        depth: depth,
        offset_x: offset_x,
        offset_y: offset_y,
        offset_z: offset_z,
        count: count,
        angle: angle
      }
      frame = joint_frame(first.bounds, second.bounds, joint)
      first_tool = build_joint_tool(parent_entities, frame, pattern, joint)
      second_tool = build_joint_tool(parent_entities, frame, pattern, joint)
      [
        apply_solid_joint(first, first_method, first_tool),
        apply_solid_joint(second, second_method, second_tool)
      ]
    rescue CommandExecutionError
      raise
    rescue StandardError => error
      raise CommandExecutionError.new(
        'SketchUp could not create joinery geometry',
        kind: 'joinery_geometry_error',
        details: { exception_type: error.class.name }
      )
    end

    def prepare_joinery_solid(entity)
      return nil unless entity

      entity.make_unique if entity.respond_to?(:make_unique)
      unless entity.respond_to?(:manifold?) && entity.manifold? &&
             entity.respond_to?(:subtract) && entity.respond_to?(:union)
        raise CommandExecutionError.new(
          'Joinery requires solid groups or component instances',
          kind: 'unsupported_entity'
        )
      end
      entity
    end

    def build_joint_tool(entities, frame, pattern, joint)
      tool = entities.add_group
      profiles_for(pattern, frame, joint).each do |points|
        face = tool.entities.add_face(points)
        raise 'Joint profile could not be created' unless face

        face.pushpull(joint.fetch(:depth))
      end
      tool
    end

    def profiles_for(pattern, frame, joint)
      width = joint.fetch(:width)
      height = joint.fetch(:height)
      profile_count = pattern == :mortise_tenon ? 1 : joint.fetch(:count)
      cell_count = pattern == :mortise_tenon ? 1 : (profile_count * 2) - 1
      cell_width = width.to_f / cell_count
      Array.new(profile_count) do |profile_index|
        index = pattern == :mortise_tenon ? 0 : profile_index * 2
        distance = -(width / 2.0) + (cell_width * (index + 0.5))
        cell_center = vector_add(
          frame.fetch(:origin),
          vector_scale(frame.fetch(:width_axis), distance)
        )
        taper = if pattern == :dovetail
                  radians = joint.fetch(:angle) * Math::PI / 180.0
                  [joint.fetch(:depth) * Math.tan(radians), cell_width * 0.45].min
                else
                  0
                end
        half_bottom = (cell_width / 2.0) + taper
        half_top = cell_width / 2.0
        bottom = vector_add(
          cell_center,
          vector_scale(frame.fetch(:height_axis), -(height / 2.0))
        )
        top = vector_add(
          cell_center,
          vector_scale(frame.fetch(:height_axis), height / 2.0)
        )
        [
          vector_add(bottom, vector_scale(frame.fetch(:width_axis), -half_bottom)),
          vector_add(bottom, vector_scale(frame.fetch(:width_axis), half_bottom)),
          vector_add(top, vector_scale(frame.fetch(:width_axis), half_top)),
          vector_add(top, vector_scale(frame.fetch(:width_axis), -half_top))
        ]
      end
    end

    def joint_frame(first_bounds, second_bounds, joint)
      first_center = point_coordinates(first_bounds.center)
      second_center = point_coordinates(second_bounds.center)
      normal = vector_normalize(vector_subtract(second_center, first_center))
      reference = normal[2].abs > 0.9 ? [0.0, 1.0, 0.0] : [0.0, 0.0, 1.0]
      width_axis = vector_normalize(vector_cross(reference, normal))
      height_axis = vector_normalize(vector_cross(normal, width_axis))
      first_face = vector_add(
        first_center,
        vector_scale(normal, bounds_extent(first_bounds, normal))
      )
      second_face = vector_add(
        second_center,
        vector_scale(normal, -bounds_extent(second_bounds, normal))
      )
      interface = vector_scale(vector_add(first_face, second_face), 0.5)
      offset = [joint.fetch(:offset_x), joint.fetch(:offset_y), joint.fetch(:offset_z)]
      {
        origin: vector_add(
          vector_add(interface, offset),
          vector_scale(normal, -(joint.fetch(:depth) / 2.0))
        ),
        width_axis: width_axis,
        height_axis: height_axis
      }
    end

    def bounds_extent(bounds, direction)
      minimum = point_coordinates(bounds.min)
      maximum = point_coordinates(bounds.max)
      half_sizes = maximum.zip(minimum).map { |high, low| (high - low) / 2.0 }
      direction.zip(half_sizes).sum { |component, half| component.abs * half }
    end

    def point_coordinates(point)
      return [point.x, point.y, point.z] if point.respond_to?(:x)

      [point[0], point[1], point[2]]
    end

    def vector_add(left, right)
      left.zip(right).map { |a, b| a + b }
    end

    def vector_subtract(left, right)
      left.zip(right).map { |a, b| a - b }
    end

    def vector_scale(vector, factor)
      vector.map { |component| component * factor }
    end

    def vector_cross(left, right)
      [
        (left[1] * right[2]) - (left[2] * right[1]),
        (left[2] * right[0]) - (left[0] * right[2]),
        (left[0] * right[1]) - (left[1] * right[0])
      ]
    end

    def vector_normalize(vector)
      length = Math.sqrt(vector.sum { |component| component * component })
      raise 'Joinery entities must have distinct centers' if length.zero?

      vector_scale(vector, 1.0 / length)
    end

    def apply_solid_joint(board, method, tool)
      result = board.public_send(method, tool)
      unless result
        raise CommandExecutionError.new(
          'SketchUp solid operation failed',
          kind: 'joinery_geometry_error'
        )
      end

      if tool.respond_to?(:valid?) && tool.valid? && !tool.equal?(result)
        tool.erase!
      end
      result
    end

    public

    def eval_ruby(code:)
      result = eval(code, TOPLEVEL_BINDING.dup, '(sketchup-mcp eval)', 1)
      { success: true }.merge(EvalResult.normalize(result))
    rescue CommandExecutionError
      raise
    rescue ScriptError
      raise CommandExecutionError.new(
        'Ruby evaluation failed',
        kind: 'evaluation_error',
        details: { category: 'script_error' }
      )
    rescue StandardError
      raise CommandExecutionError.new(
        'Ruby evaluation failed',
        kind: 'evaluation_error',
        details: { category: 'runtime_error' }
      )
    end

  end

end
