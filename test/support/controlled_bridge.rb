require 'thread'


class ControlledBridgeClock
  def initialize
    @now = 0.0
  end

  def now
    @now
  end

  def advance(seconds)
    @now += seconds
  end
end


class ScriptedBridgeClient
  attr_reader :response, :write_attempts

  def initialize(reads:, writes: [], closed: false, close_error: nil)
    @reads = reads
    @writes = writes
    @closed = closed
    @close_error = close_error
    @response = +''
    @write_attempts = 0
  end

  def read_nonblock(_length, exception:)
    value = @reads.shift
    raise value if value.is_a?(Exception)

    value
  end

  def write_nonblock(bytes, exception:)
    @write_attempts += 1
    behavior = @writes.shift
    raise behavior if behavior.is_a?(Exception)
    if behavior.is_a?(Integer)
      @response << bytes.byteslice(0, behavior)
      return behavior
    end
    return behavior if behavior

    @response << bytes
    bytes.bytesize
  end

  def close
    @closed = true
    raise @close_error if @close_error
  end

  def closed?
    @closed
  end
end


class ControlledBridgeListeningSocket
  Address = Struct.new(:ip_port, :ip_address)

  def initialize(clients: [], accept_error: nil, port: 23_456)
    @clients = clients.dup
    @accept_error = accept_error
    @port = port
    @closed = false
  end

  def local_address
    Address.new(@port, '127.0.0.1')
  end

  def accept_nonblock
    raise @accept_error if @accept_error

    @clients.shift
  end

  def close
    @closed = true
  end

  def closed?
    @closed
  end
end


class ControlledBridgeTransport
  attr_reader :listened_on

  def initialize(
    listening_socket:,
    clock: ControlledBridgeClock.new,
    listener_ready: true,
    listen_error: nil,
    client_waits: {},
    block_direction: nil,
    block_timeout: 0.5
  )
    @listening_socket = listening_socket
    @clock = clock
    @listener_ready = listener_ready
    @listen_error = listen_error
    @client_waits = client_waits
    @block_direction = block_direction
    @block_timeout = block_timeout
    @wait_lock = Mutex.new
    @wait_condition = ConditionVariable.new
    @client_wait_entered = false
  end

  def listen(host, port)
    @listened_on = [host, port]
    raise @listen_error if @listen_error

    @listening_socket
  end

  def now
    @clock.now
  end

  def wait(socket, direction, timeout)
    return @listener_ready if socket.equal?(@listening_socket)

    block_client(direction)
    waits = @client_waits.fetch(direction, [true])
    result = waits.length == 1 ? waits.fetch(0) : waits.shift
    @clock.advance(timeout) unless result
    result
  end

  def client_wait_entered?
    @wait_lock.synchronize { @client_wait_entered }
  end

  private

  def block_client(direction)
    return unless direction == @block_direction

    @wait_lock.synchronize do
      @client_wait_entered = true
      @wait_condition.wait(@wait_lock, @block_timeout)
    end
  end
end
