"""Tests for the CLI layer: argument parsing and command dispatch."""

import json

import pytest

from ask_qwen import api, auth, cli


@pytest.fixture
def parser():
    return cli.build_parser()


class TestArgParsing:
    def test_ask_collects_words(self, parser):
        args = parser.parse_args(["ask", "hello", "world"])
        assert args.message == ["hello", "world"]

    def test_list_default_limit(self, parser):
        assert parser.parse_args(["list"]).limit == 20

    def test_mode_rejects_invalid(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["mode", "invalid"])

    def test_no_command_exits(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["ask-qwen"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1


class TestCommandDispatch:
    def test_ask(self, parser, capsys, monkeypatch):
        monkeypatch.setattr(
            api, "stream_ask",
            lambda msg, on_delta=None: {"answer": f"answer:{msg}", "chat_id": "c1"},
        )
        args = parser.parse_args(["ask", "ping"])
        args.func(args)
        assert capsys.readouterr().out.strip() == "answer:ping"

    def test_read_uses_active_chat(self, parser, capsys, monkeypatch):
        monkeypatch.setattr(api, "load_state", lambda: {"active_chat_id": "chat-1"})
        monkeypatch.setattr(
            api, "chat_transcript",
            lambda cid: [{"role": "assistant", "content": f"hello-{cid}"}],
        )
        args = parser.parse_args(["read"])
        args.func(args)
        out = json.loads(capsys.readouterr().out)
        assert out[0] == {"role": "assistant", "content": "hello-chat-1"}

    def test_read_without_active_chat_errors(self, parser, capsys, monkeypatch):
        monkeypatch.setattr(api, "load_state", lambda: {})
        args = parser.parse_args(["read"])
        with pytest.raises(SystemExit):
            args.func(args)
        assert "tidak ada chat aktif" in capsys.readouterr().err.lower()

    def test_new_chat_marks_reset(self, parser, capsys, monkeypatch):
        seen = {}
        monkeypatch.setattr(api, "set_active_chat", lambda cid: seen.update(cid=cid))
        args = parser.parse_args(["new"])
        args.func(args)
        assert seen["cid"] is None
        assert "chat baru" in capsys.readouterr().out.lower()

    def test_list(self, parser, capsys, monkeypatch):
        monkeypatch.setattr(api, "list_chats", lambda limit: [{"id": "1", "title": "t"}])
        args = parser.parse_args(["list", "-n", "5"])
        args.func(args)
        assert json.loads(capsys.readouterr().out)[0]["id"] == "1"

    def test_open_chat(self, parser, capsys, monkeypatch):
        monkeypatch.setattr(api, "parse_chat_id", lambda ref: "cid-1")
        monkeypatch.setattr(api, "chat_exists", lambda cid: True)
        monkeypatch.setattr(api, "chat_transcript", lambda cid: [{"role": "user", "content": "hi"}])
        seen = {}
        monkeypatch.setattr(api, "set_active_chat", lambda cid: seen.update(cid=cid))
        args = parser.parse_args(["open", "https://chat.qwen.ai/c/cid-1"])
        args.func(args)
        assert seen["cid"] == "cid-1"
        assert json.loads(capsys.readouterr().out)[0]["content"] == "hi"

    def test_stop_sets_flag(self, parser, capsys, monkeypatch):
        called = []
        monkeypatch.setattr(api, "request_stop", lambda: called.append(1))
        args = parser.parse_args(["stop"])
        args.func(args)
        assert called == [1]
        assert "stop request" in capsys.readouterr().out.lower()

    def test_status(self, parser, capsys, monkeypatch):
        monkeypatch.setattr(auth, "session_ok", lambda: True)
        monkeypatch.setattr(api, "load_state", lambda: {"active_chat_id": "cid-1"})
        monkeypatch.setattr(api, "is_generating", lambda cid: True)
        args = parser.parse_args(["status"])
        args.func(args)
        out = json.loads(capsys.readouterr().out)
        assert out["logged_in"] is True
        assert out["generating"] is True
        assert out["chat_id"] == "cid-1"

    def test_search(self, parser, capsys, monkeypatch):
        monkeypatch.setattr(
            api, "search_chats", lambda q: [{"title": f"found-{q}", "id": "2"}],
        )
        args = parser.parse_args(["search", "topic"])
        args.func(args)
        assert json.loads(capsys.readouterr().out)[0]["title"] == "found-topic"
