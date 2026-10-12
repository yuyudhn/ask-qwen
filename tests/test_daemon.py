"""Tests for daemon dispatch and DaemonClient protocol."""

import json
import os
import socket
import threading
import pytest

from ask_qwen import daemon, config


class FakeQwenClient:
    """Minimal stub matching QwenWebClient interface."""
    def __init__(self):
        self.calls = []

    def ask(self, message):
        self.calls.append(("ask", message))
        return f"answer:{message}"

    def read(self):
        return [{"role": "assistant", "content": "hi"}]

    def new_chat(self):
        return "Chat baru siap."

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
        return []

    def open_chat(self, url):
        return []

    def close(self):
        pass


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

    @pytest.fixture
    def tmp_daemon(self, tmp_path, monkeypatch):
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
            import time; time.sleep(0.05)
            if os.path.exists(sock_path):
                break

        yield sock_path, server

        server._running = False
        t.join(timeout=10)

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
