module SU_MCP
  class BridgeRuntime
    POLL_INTERVAL = 0.1

    def initialize(listener:, scheduler:, logger: nil)
      @listener = listener
      @scheduler = scheduler
      @logger = logger || ->(_message) {}
      @timer = nil
    end

    def start
      return self if @timer

      @listener.start
      @timer = @scheduler.every(POLL_INTERVAL) { tick }
      self
    rescue StandardError
      stop
      raise
    end

    def stop
      @scheduler.cancel(@timer) if @timer
      @timer = nil
      @listener.stop
      self
    end

    private

    def tick
      @listener.poll(timeout: 0)
      @listener.drain
    rescue StandardError => error
      @logger.call("Timer error: #{error.message}")
    end
  end
end
