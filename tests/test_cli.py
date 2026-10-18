"""Tests for the CLI layer: argument parsing and error handling."""

import json
import sys

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

    def ask_stream(self, message):
        self.calls.append(("ask_stream", message))
        self._maybe_raise()
        yield "partial\n"
        yield "answer\n"

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
        return "Stop request diterima."

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

    def test_read(self, fake_client, parser, capsys, monkeypatch):
        from ask_qwen import api

        monkeypatch.setattr(api, "load_state", lambda: {"active_chat_id": "chat-1"})
        monkeypatch.setattr(
            api, "chat_transcript",
            lambda cid: [{"role": "assistant", "content": f"hello-{cid}"}],
        )
        args = parser.parse_args(["read"])
        args.func(args)
        out = json.loads(capsys.readouterr().out)
        assert out[0] == {"role": "assistant", "content": "hello-chat-1"}
        assert fake_client.calls == []  # read tidak lagi lewat daemon

    def test_read_without_active_chat_errors(self, fake_client, parser, capsys, monkeypatch):
        from ask_qwen import api

        monkeypatch.setattr(api, "load_state", lambda: {})
        args = parser.parse_args(["read"])
        with pytest.raises(SystemExit):
            args.func(args)
        assert "tidak ada chat aktif" in capsys.readouterr().err.lower()

    def test_new_chat(self, fake_client, parser, capsys, monkeypatch):
        from ask_qwen import api

        seen = {}
        monkeypatch.setattr(api, "set_active_chat", lambda cid: seen.update(cid=cid))
        args = parser.parse_args(["new"])
        args.func(args)
        assert seen["cid"] is None  # marker: ask berikutnya buat chat baru
        assert "chat baru" in capsys.readouterr().out.lower()
        assert fake_client.calls == []  # new tidak lagi lewat daemon

    def test_open_chat(self, fake_client, parser, capsys, monkeypatch):
        from ask_qwen import api

        monkeypatch.setattr(api, "chat_exists", lambda cid: cid == "cid-1")
        monkeypatch.setattr(api, "parse_chat_id", lambda ref: "cid-1")
        monkeypatch.setattr(
            api, "chat_transcript", lambda cid: [{"role": "user", "content": "hi"}],
        )
        seen = {}
        monkeypatch.setattr(api, "set_active_chat", lambda cid: seen.update(cid=cid))
        args = parser.parse_args(["open", "https://chat.qwen.ai/c/cid-1"])
        args.func(args)
        assert seen["cid"] == "cid-1"
        out = json.loads(capsys.readouterr().out)
        assert out[0]["content"] == "hi"
        assert fake_client.calls == []  # open tidak lagi lewat daemon

    def test_stop_warm_daemon_sends_request(self, fake_client, parser, capsys, monkeypatch):
        monkeypatch.setattr(cli.daemon, "is_running", lambda: True)
        args = parser.parse_args(["stop"])
        args.func(args)
        assert fake_client.calls == [("stop",)]
        assert "stop request" in capsys.readouterr().out.lower()

    def test_stop_cold_daemon_reports_idle(self, fake_client, parser, capsys, monkeypatch):
        monkeypatch.setattr(cli.daemon, "is_running", lambda: False)
        args = parser.parse_args(["stop"])
        args.func(args)
        assert "tidak ada" in capsys.readouterr().out.lower()
        assert fake_client.calls == []

    def test_status(self, fake_client, parser, capsys):
        args = parser.parse_args(["status"])
        args.func(args)
        out = json.loads(capsys.readouterr().out)
        assert out["logged_in"] is True

    def test_search(self, fake_client, parser, capsys, monkeypatch):
        from ask_qwen import api

        monkeypatch.setattr(
            api, "search_chats",
            lambda q: [{"title": f"found-{q}", "url": "https://chat.qwen.ai/c/2", "id": "2"}],
        )
        args = parser.parse_args(["search", "topic"])
        args.func(args)
        out = json.loads(capsys.readouterr().out)
        assert out[0]["title"] == "found-topic"
        assert fake_client.calls == []  # search tidak lagi lewat daemon

    def test_ask_streams_on_tty(self, fake_client, parser, capsys, monkeypatch):
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        args = parser.parse_args(["ask", "ping"])
        args.func(args)
        assert ("ask_stream", "ping") in fake_client.calls
        assert "ask" not in [call[0] for call in fake_client.calls]
        assert capsys.readouterr().out == "partial\nanswer\n\n"

    def test_ask_uses_plain_request_when_piped(self, fake_client, parser, capsys):
        args = parser.parse_args(["ask", "ping"])
        args.func(args)
        assert fake_client.calls == [("ask", "ping")]
        assert capsys.readouterr().out == "answer:ping\n"

    def test_ask_tty_falls_back_to_final_answer_without_deltas(
        self, fake_client, parser, capsys, monkeypatch
    ):
        # daemon lama / stream kosong: CLI harus tetap menampilkan jawaban
        def no_deltas(message):
            if False:
                yield  # pragma: no cover - menjadikan ini generator
            return f"answer:{message}"

        fake_client.ask_stream = no_deltas
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        args = parser.parse_args(["ask", "ping"])
        args.func(args)
        assert capsys.readouterr().out == "answer:ping\n"


class TestErrorHandling:
    def test_ask_error_exits_1(self, monkeypatch, parser):
        client = FakeClient(error=RuntimeError("boom"))
        monkeypatch.setattr(cli, "_get_client", lambda: client)
        args = parser.parse_args(["ask", "hello"])
        with pytest.raises(SystemExit) as exc:
            args.func(args)
        assert exc.value.code == 1
