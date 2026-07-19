module SU_MCP
  class CommandExecutionError < StandardError
    KINDS = %w[
      entity_not_found evaluation_error incompatible_entity_context
      joinery_geometry_error unsupported_entity unsupported_result
    ].freeze

    attr_reader :kind, :details

    def initialize(message, kind:, details: {})
      unless KINDS.include?(kind)
        raise ArgumentError, "Unknown command execution error kind: #{kind}"
      end

      super(message)
      @kind = kind
      @details = details
    end
  end
end
