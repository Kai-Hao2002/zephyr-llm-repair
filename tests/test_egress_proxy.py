"""Tests for tools/egress_proxy.py: CONNECT to an allowlisted host:port is tunnelled, anything else gets 403.
Run: python tests/test_egress_proxy.py"""
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from egress_proxy import start_proxy


def _echo_server():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.sendall(conn.recv(100).upper())
        conn.close()
    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


def _connect(proxy_port, target):
    s = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
    s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    return s, s.recv(200).decode()


def test_allowed_target_is_tunnelled():
    echo = _echo_server()
    port, stop = start_proxy({f"127.0.0.1:{echo}"})
    s, reply = _connect(port, f"127.0.0.1:{echo}")
    assert reply.startswith("HTTP/1.1 200")
    s.sendall(b"hello")
    assert s.recv(100) == b"HELLO"
    s.close()
    assert ("allowed", f"127.0.0.1:{echo}") in stop()


def test_other_targets_are_denied():
    port, stop = start_proxy({"generativelanguage.googleapis.com:443"})
    s, reply = _connect(port, "github.com:443")
    assert reply.startswith("HTTP/1.1 403")
    s.close()
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")  # plain HTTP proxying is not offered
    assert s.recv(200).decode().startswith("HTTP/1.1 403")
    s.close()
    log = stop()
    assert [k for k, _ in log] == ["denied", "denied"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
