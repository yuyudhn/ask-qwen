"""Tests for the CLI layer: argument parsing and error handling."""

import json
import pytest

from ask_qwen import cli, daemon


class FakeClient:
    """Stub that records calls and returns canned responses."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def _maybe_raise(self):
        if self.error:
            raise self.error

    def ask(self, message):
        self.calls.append(("ask", message))
        self._maybe_raise()
        return f"answer:{message}"

    def read(self):
        self.calls.append(("read",))
        self._maybe_raise()
        return [{"role": "assistant", "content": "hello"}]

    def new_chat(self):
        self.calls.append(("new_chat",))
        self._maybe_raise()
        return "Chat baru siap."

    def list_chats(self, limit):
        self.calls.append(("list_chats", limit))
        self._maybe_raise()
        return [{"title": "test", "url": "https://chat.qwen.ai/c/1"}]

    def read_chat(self, url):
        self.calls.append(("read_chat", url))
        self._maybe_raise()
        return [{"role": "user", "content": "hi"}]

    def open_chat(self, url):
        self.calls.append(("open_chat", url))
        self._maybe_raise()
        return [{"role": "user", "content": "hi"}]

    def set_mode(self, mode):
        self.calls.append(("set_mode", mode))
        self._maybe_raise()
        return f"Mode diatur ke {mode.capitalize()}."

    def stop_generation(self):
        self.calls.append(("stop",))
        self._maybe_raise()
        return "Tidak ada generasi aktif."

    def get_status(self):
        self.calls.append(("status",))
        self._maybe_raise()
        return {"logged_in": True, "generating": False}

    def search_chats(self, query):
        self.calls.append(("search", query))
        self._maybe_raise()
        return [{"title": "found", "url": "https://chat.qwen.ai/c/2"}]

    def close(self):
        pass


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "_get_client", lambda: client)
    return client


@pytest.fixture
def parser():
    return cli.build_parser()


class TestArgParsing:
    def test_ask_collects_words(self, parser):
        args = parser.parse_args(["ask", "hello", "world"])
        assert args.message == ["hello", "world"]

    def test_list_default_limit(self, parser):
        args = parser.parse_args(["list"])
        assert args.limit == 20

    def test_list_custom_limit(self, parser):
        args = parser.parse_args(["list", "-n", "5"])
        assert args.limit == 5

    def test_mode_rejects_invalid(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["mode", "invalid"])

    def test_no_command_exits(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["ask-qwen"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1

    def test_quit_subcommand_exists(self, parser):
        args = parser.parse_args(["quit"])
        assert args.command == "quit"


class TestCommandDispatch:
    def test_ask(self, fake_client, parser, capsys):
        args = parser.parse_args(["ask", "ping"])
        args.func(args)
        assert fake_client.calls == [("ask", "ping")]
        assert "answer:ping" in capsys.readouterr().out

    def test_read(self, fake_client, parser, capsys):
        args = parser.parse_args(["read"])
        args.func(args)
        assert fake_client.calls == [("read",)]
        out = json.loads(capsys.readouterr().out)
        assert out[0]["role"] == "assistant"

    def test_new_chat(self, fake_client, parser, capsys):
        args = parser.parse_args(["new"])
        args.func(args)
        assert fake_client.calls == [("new_chat",)]

    def test_status(self, fake_client, parser, capsys):
        args = parser.parse_args(["status"])
        args.func(args)
        out = json.loads(capsys.readouterr().out)
        assert out["logged_in"] is True

    def test_search(self, fake_client, parser, capsys):
        args = parser.parse_args(["search", "topic"])
        args.func(args)
        assert fake_client.calls == [("search", "topic")]


class TestErrorHandling:
    def test_ask_error_exits_1(self, monkeypatch, parser):
        client = FakeClient(error=RuntimeError("boom"))
        monkeypatch.setattr(cli, "_get_client", lambda: client)
        args = parser.parse_args(["ask", "hello"])
        with pytest.raises(SystemExit) as exc:
            args.func(args)
        assert exc.value.code == 1
