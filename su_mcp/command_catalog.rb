require 'json'


module SU_MCP
  class InvalidArguments < StandardError; end
  class UnknownCommand < StandardError; end

  class CommandContract
    attr_reader :name, :required_arguments, :optional_arguments, :constraints,
                :execution_error

    def initialize(raw)
      raw = deep_freeze(raw)
      @name = raw.fetch('name')
      @required_arguments = raw.dig('arguments', 'required')
      @optional_arguments = raw.dig('arguments', 'optional')
      @constraints = raw.fetch('constraints', {})
      @execution_error = raw['execution_error']
      @resource_id_field = raw.dig('success', 'resource_id')
      freeze
    end

    def resource_id(result)
      return nil unless @resource_id_field

      result.key?(@resource_id_field.to_sym) ?
        result[@resource_id_field.to_sym] : result.fetch(@resource_id_field)
    end

    private

    def deep_freeze(value)
      value.each { |key, item| deep_freeze(key); deep_freeze(item) } if value.is_a?(Hash)
      value.each { |item| deep_freeze(item) } if value.is_a?(Array)
      value.freeze
    end
  end

  class CommandCatalog
    SOURCE_PATH = File.expand_path('../src/sketchup_mcp/command_catalog.json', __dir__)
    PACKAGED_PATH = File.join(__dir__, 'command_catalog.json')

    def self.default_path
      return PACKAGED_PATH if File.file?(PACKAGED_PATH)

      SOURCE_PATH
    end

    def initialize(path: self.class.default_path)
      raw = JSON.parse(File.read(path))
      @commands = raw.fetch('commands').to_h do |raw_command|
        command = CommandContract.new(raw_command)
        [command.name, command]
      end
      @executable_aliases = raw.fetch('executable_aliases')
      @failure_semantics = raw.fetch('failure_semantics')
    end

    def command(name)
      canonical_name = @executable_aliases.fetch(name, name)
      @commands.fetch(canonical_name)
    rescue KeyError
      raise UnknownCommand, "Unknown command: #{name}"
    end

    def names
      @commands.keys.freeze
    end

    def validate(command, arguments)
      raise InvalidArguments, 'arguments must be an object' unless arguments.is_a?(Hash)

      required = command.required_arguments
      optional = command.optional_arguments
      missing = required.keys.reject { |name| arguments.key?(name) }
      raise InvalidArguments, "missing required argument: #{missing.first}" unless missing.empty?

      unknown = arguments.keys - required.keys - optional.keys
      raise InvalidArguments, "unknown argument: #{unknown.first}" unless unknown.empty?

      normalized = required.merge(optional).to_h do |name, contract|
        value = if arguments.key?(name)
                  normalize(arguments[name], contract, name)
                else
                  contract['default']
                end
        [name, value]
      end
      validate_command_constraints(command, normalized)
      normalized
    end

    def failure_code(semantic)
      @failure_semantics.fetch(semantic).fetch('jsonrpc_code')
    end

    private

    def normalize(value, contract, name)
      normalized = case contract.fetch('type')
                   when 'entity_id' then entity_id(value, name)
                   when 'number[3]' then vector(value, name)
                   when 'number' then finite_number(value, name)
                   when 'integer'
                     unless value.is_a?(Integer)
                       raise InvalidArguments, "#{name} must be an integer"
                     end
                     value
                   when 'string'
                     raise InvalidArguments, "#{name} must be a string" unless value.is_a?(String)
                     value
                   when 'boolean'
                     unless value == true || value == false
                       raise InvalidArguments, "#{name} must be a boolean"
                     end
                     value
                   else
                     value
                   end
      if contract['enum'] && !contract['enum'].include?(normalized)
        raise InvalidArguments, "#{name} must be one of: #{contract['enum'].join(', ')}"
      end
      if contract['min_length'] && normalized.length < contract['min_length']
        raise InvalidArguments, "#{name} must not be empty"
      end
      conditional_pattern = contract['pattern_if_prefixed']
      if conditional_pattern && normalized.start_with?(conditional_pattern.fetch('prefix'))
        pattern_match = Regexp.new(conditional_pattern.fetch('pattern')).match(normalized)
        unless pattern_match && pattern_match.begin(0).zero? && pattern_match.end(0) == normalized.length
          raise InvalidArguments, "#{name} #{conditional_pattern.fetch('message')}"
        end
      end
      forbidden = contract.fetch('forbidden_substrings', [])
      if forbidden.any? { |fragment| normalized.include?(fragment) }
        raise InvalidArguments, "#{name} contains a forbidden operation-management call"
      end
      if contract['positive']
        positive = if normalized.is_a?(Array)
                     normalized.all?(&:positive?)
                   else
                     normalized.positive?
                   end
        raise InvalidArguments, "#{name} must be positive" unless positive
      end
      if contract.key?('exclusive_minimum') && normalized <= contract['exclusive_minimum']
        raise InvalidArguments, "#{name} must be greater than #{contract['exclusive_minimum']}"
      end
      if contract.key?('exclusive_maximum') && normalized >= contract['exclusive_maximum']
        raise InvalidArguments, "#{name} must be less than #{contract['exclusive_maximum']}"
      end
      normalized
    end

    def entity_id(value, name)
      normalized = if value.is_a?(Integer)
                     value
                   elsif value.is_a?(String) && value.match?(/\A[1-9][0-9]*\z/)
                     value.to_i
                   end
      return normalized if normalized && normalized.positive?

      raise InvalidArguments, "#{name} must be a positive entity ID"
    end

    def vector(value, name)
      valid = value.is_a?(Array) && value.length == 3 && value.all? do |number|
        number.is_a?(Numeric) && (!number.respond_to?(:finite?) || number.finite?)
      end
      raise InvalidArguments, "#{name} must contain exactly three finite numbers" unless valid

      value
    end

    def finite_number(value, name)
      valid = value.is_a?(Numeric) && (!value.respond_to?(:finite?) || value.finite?)
      raise InvalidArguments, "#{name} must be a finite number" unless valid

      value
    end

    def validate_command_constraints(command, arguments)
      command.constraints.fetch('distinct_arguments', []).each do |names|
        next if names.map { |name| arguments.fetch(name) }.uniq.length == names.length

        raise InvalidArguments, "#{names.join(' and ')} must identify different entities"
      end
    end
  end
end
