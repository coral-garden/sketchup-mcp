require 'json'


module SU_MCP
  class InvalidArguments < StandardError; end
  class UnknownCommand < StandardError; end

  class CommandCatalog
    SOURCE_PATH = File.expand_path('../../src/sketchup_mcp/command_catalog.json', __dir__)
    PACKAGED_PATH = File.join(__dir__, 'command_catalog.json')

    def self.default_path
      return PACKAGED_PATH if File.file?(PACKAGED_PATH)

      SOURCE_PATH
    end

    def initialize(path: self.class.default_path)
      raw = JSON.parse(File.read(path))
      @commands = raw.fetch('commands').to_h { |command| [command.fetch('name'), command] }
      @executable_aliases = raw.fetch('executable_aliases')
    end

    def command(name)
      canonical_name = @executable_aliases.fetch(name, name)
      @commands.fetch(canonical_name)
    rescue KeyError
      raise UnknownCommand, "Unknown command: #{name}"
    end

    def validate(command, arguments)
      raise InvalidArguments, 'arguments must be an object' unless arguments.is_a?(Hash)

      required = command.dig('arguments', 'required')
      optional = command.dig('arguments', 'optional')
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

    def resource_id(command, result)
      field = command.dig('success', 'resource_id')
      return nil unless field

      result.key?(field.to_sym) ? result[field.to_sym] : result.fetch(field)
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
      command.fetch('constraints', {}).fetch('distinct_arguments', []).each do |names|
        next if names.map { |name| arguments.fetch(name) }.uniq.length == names.length

        raise InvalidArguments, "#{names.join(' and ')} must identify different entities"
      end
    end
  end
end
