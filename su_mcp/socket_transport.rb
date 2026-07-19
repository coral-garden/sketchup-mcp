require 'socket'


module SU_MCP
  class SocketTransport
    def listen(host, port)
      TCPServer.new(host, port)
    end

    def now
      Process.clock_gettime(Process::CLOCK_MONOTONIC)
    end

    def wait(socket, direction, timeout)
      readers = direction == :read ? [socket] : nil
      writers = direction == :write ? [socket] : nil
      !IO.select(readers, writers, nil, timeout).nil?
    end
  end
end
