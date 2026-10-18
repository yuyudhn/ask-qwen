"""Tests for daemon dispatch and DaemonClient protocol."""

import json
import os
import socket
import threading
import time

import pytest

from ask_qwen import browser, daemon, config


class FakeQwenClient:
    """Minimal stub matching QwenWebClient interface."""
    def __init__(self):
        self.calls = []

    def ask(self, message, on_delta=None):
        self.calls.append(("ask", message))
        if on_delta:
            on_delta("partial\n")
            on_delta("answer\n")
        return f"answer:{message}"

    def read(self):
        return [{"role": "assistant", "content": "hi"}]


    def list_chats(self, limit=20):
        return [{"title": "t", "url": "u"}]

    def get_status(self):
        return {"logged_in": True}

    def stop_generation(self):
        return "idle"

    def search_chats(self, query):
        return []

    def set_mode(self, mode):
        return f"set:{mode}"

    def read_chat(self, url):
        self.calls.append(("read_chat", url))
        return []

    def open_chat(self, url):
        self.calls.append(("open_chat", url))
        return []

    def close(self):
        pass


@pytest.fixture
def tmp_daemon(tmp_path, monkeypatch):
    """Start a real daemon on a temp socket, return (sock_path, server)."""
    sock_path = str(tmp_path / "test.sock")
    pid_path = tmp_path / "test.pid"
    monkeypatch.setattr(config, "DAEMON_SOCKET", tmp_path / "test.sock")
    monkeypatch.setattr(config, "DAEMON_PID_FILE", pid_path)
    monkeypatch.setattr(config, "DAEMON_IDLE_TIMEOUT_S", 30)

    server = daemon.DaemonServer()
    server._client = FakeQwenClient()
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    # Wait for socket
    for _ in range(50):
        time.sleep(0.05)
        if os.path.exists(sock_path):
            break

    yield sock_path, server

    server._running = False
    t.join(timeout=10)


class TestDaemonDispatch:
    def test_dispatch_ask(self):
        server = daemon.DaemonServer()
        server._client = FakeQwenClient()
        result = server._dispatch("ask", {"message": "hello"})
        assert result == "answer:hello"

    def test_dispatch_ping(self):
        server = daemon.DaemonServer()
        server._client = FakeQwenClient()
        assert server._dispatch("ping", {}) == "pong"

    def test_dispatch_quit_stops_server(self):
        server = daemon.DaemonServer()
        server._client = FakeQwenClient()
        result = server._dispatch("quit", {})
        assert result == "bye"
        assert server._running is False

    def test_dispatch_unknown_raises(self):
        server = daemon.DaemonServer()
        server._client = FakeQwenClient()
        with pytest.raises(ValueError, match="unknown"):
            server._dispatch("nope", {})


class TestDaemonProtocol:
    """Start a real daemon on a temp socket, test round-trip."""

    def _request(self, sock_path, method, **params):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(sock_path)
        wf = s.makefile("w")
        rf = s.makefile("r")
        wf.write(json.dumps({"method": method, "params": params}) + "\n")
        wf.flush()
        resp = json.loads(rf.readline())
        s.close()
        return resp

    def test_ping(self, tmp_daemon):
        sock_path, _ = tmp_daemon
        resp = self._request(sock_path, "ping")
        assert resp == {"ok": True, "result": "pong"}

    def test_ask_round_trip(self, tmp_daemon):
        sock_path, _ = tmp_daemon
        resp = self._request(sock_path, "ask", message="test")
        assert resp["ok"] is True
        assert resp["result"] == "answer:test"

    def test_unknown_method_returns_error(self, tmp_daemon):
        sock_path, _ = tmp_daemon
        resp = self._request(sock_path, "bogus")
        assert resp["ok"] is False
        assert "unknown" in resp["error"]

    def test_quit_stops_server(self, tmp_daemon):
        sock_path, server = tmp_daemon
        resp = self._request(sock_path, "quit")
        assert resp["result"] == "bye"
        assert server._running is False

    def test_error_response_includes_type(self, tmp_daemon):
        sock_path, server = tmp_daemon

        def boom(mode):
            raise browser.AuthExpired("sesi habis")

        server._client.set_mode = boom
        resp = self._request(sock_path, "set_mode", mode="auto")
        assert resp["ok"] is False
        assert resp["type"] == "AuthExpired"
        assert "sesi habis" in resp["error"]

    def test_ask_stream_sends_deltas_then_result(self, tmp_daemon):
        sock_path, _ = tmp_daemon
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(sock_path)
        wf = s.makefile("w")
        rf = s.makefile("r")
        wf.write(json.dumps(
            {"method": "ask", "params": {"message": "hi"}, "stream": True}
        ) + "\n")
        wf.flush()
        lines = [json.loads(rf.readline()) for _ in range(3)]
        s.close()
        assert lines[0] == {"event": "delta", "text": "partial\n"}
        assert lines[1] == {"event": "delta", "text": "answer\n"}
        assert lines[2] == {"ok": True, "result": "answer:hi"}

    def test_socket_is_owner_only(self, tmp_daemon):
        sock_path, _ = tmp_daemon
        assert (os.stat(sock_path).st_mode & 0o777) == 0o600


class TestDaemonClient:
    def test_client_raises_typed_error(self, tmp_daemon):
        _, server = tmp_daemon

        def boom(mode):
            raise browser.ProfileLocked("dipakai proses lain")

        server._client.set_mode = boom
        with pytest.raises(browser.ProfileLocked, match="dipakai"):
            daemon.DaemonClient().set_mode("auto")

    def test_client_request_timeout(self, tmp_daemon, monkeypatch):
        _, server = tmp_daemon

        def slow(message):
            time.sleep(1)
            return "late"

        server._client.ask = slow
        monkeypatch.setattr(config, "DAEMON_REQUEST_TIMEOUT_S", 0.2)
        with pytest.raises(RuntimeError, match="ask-qwen quit"):
            daemon.DaemonClient().ask("hi")


    def test_client_ask_stream_yields_deltas(self, tmp_daemon):
        # round-trip penuh: flag stream top-level → daemon memanggil on_delta
        stream = daemon.DaemonClient().ask_stream("hi")
        deltas = []
        while True:
            try:
                deltas.append(next(stream))
            except StopIteration as stop:
                final = stop.value
                break
        assert deltas == ["partial\n", "answer\n"]
        assert final == "answer:hi"
