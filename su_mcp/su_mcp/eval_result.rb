require_relative 'command_execution_error'


module SU_MCP
  class EvalResult
    def self.normalize(value)
      new.normalize(value)
    end

    def normalize(value)
      {
        result: normalize_value(value, {}),
        result_type: value.class.name
      }
    end

    private

    def normalize_value(value, ancestors)
      case value
      when NilClass, TrueClass, FalseClass, String, Integer
        value
      when Float
        unsupported unless value.finite?
        value
      when Array
        normalize_collection(value, ancestors) do
          value.map { |item| normalize_value(item, ancestors) }
        end
      when Hash
        normalize_collection(value, ancestors) do
          value.each_with_object({}) do |(key, item), normalized|
            normalized_key = normalize_key(key)
            unsupported if normalized.key?(normalized_key)
            normalized[normalized_key] = normalize_value(item, ancestors)
          end
        end
      else
        unsupported
      end
    end

    def normalize_collection(value, ancestors)
      unsupported if ancestors.key?(value.object_id)

      ancestors[value.object_id] = true
      yield
    ensure
      ancestors.delete(value.object_id)
    end

    def normalize_key(key)
      case key
      when String, Symbol, Integer
        key.to_s
      when Float
        unsupported unless key.finite?
        key.to_s
      else
        unsupported
      end
    end

    def unsupported
      raise CommandExecutionError.new(
        'Ruby evaluation returned an unsupported result',
        kind: 'unsupported_result'
      )
    end
  end
end
