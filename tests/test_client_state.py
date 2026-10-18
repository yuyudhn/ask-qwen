from ask_qwen.browser import AuthExpired
from ask_qwen.client import QwenWebClient

import pytest


@pytest.fixture(autouse=True)
def _no_real_state(monkeypatch):
    """Jangan sentuh state.json asli saat test client."""
    monkeypatch.setattr("ask_qwen.api.load_state", lambda: {})
    monkeypatch.setattr("ask_qwen.api.set_active_chat", lambda cid: None)


class FakeBackend:
    def __init__(self, fail_auth_times: int = 0):
        self.fail_auth_times = fail_auth_times
        self.login_ok = True
        self.calls: list[str] = []
        self.active = False

    def ask(self, message: str, on_delta=None) -> str:
        self.calls.append(f"ask:{message}")
        if on_delta:
            on_delta("streamed\n")
        if self.fail_auth_times > 0:
            self.fail_auth_times -= 1
            raise AuthExpired("expired")
        self.active = True
        return f"answer:{message}"

    def new_chat(self) -> str:
        self.calls.append("new_chat")
        self.active = True
        return "Chat baru siap."

    def goto_chat(self, chat_id: str) -> str:
        self.calls.append(f"goto_chat:{chat_id}")
        self.active = True
        return f"Chat dibuka: {chat_id}"

    @property
    def page(self):
        import types

        return types.SimpleNamespace(url="https://chat.qwen.ai/c/fake-1")

    def read(self) -> list[dict]:
        self.calls.append("read")
        if self.fail_auth_times > 0:
            raise AuthExpired("expired")
        return [{"role": "transcript", "content": "chat isi"}]

    def list_chats(self, limit: int) -> list[dict]:
        self.calls.append(f"list_chats:{limit}")
        return [{"title": "chat", "url": "https://chat.qwen.ai/c/1"}]

    def read_chat(self, url: str) -> list[dict]:
        self.calls.append(f"read_chat:{url}")
        return [{"role": "user", "content": "chat isi"}]

    def open_chat(self, url: str) -> list[dict]:
        self.calls.append(f"open_chat:{url}")
        if self.fail_auth_times > 0:
            self.fail_auth_times -= 1
            raise AuthExpired("expired")
        self.active = True
        return [{"role": "user", "content": "chat lama"}]

    def set_mode(self, mode: str) -> str:
        self.calls.append(f"set_mode:{mode}")
        return f"Mode diatur ke {mode.capitalize()}."

    def stop_generation(self) -> str:
        self.calls.append("stop_generation")
        return "Generasi dihentikan."

    def get_status(self) -> dict:
        self.calls.append("get_status")
        return {"logged_in": True, "generating": False}

    def search_chats(self, query: str) -> list[dict]:
        self.calls.append(f"search_chats:{query}")
        return [{"title": query, "url": "https://chat.qwen.ai/c/1"}]

    def close(self) -> None:
        self.calls.append("close")


class TestClientState:
    def test_first_ask_creates_new_chat_then_asks(self):
        b = FakeBackend()
        c = QwenWebClient(backend=b)
        assert c.ask("halo") == "answer:halo"
        assert b.calls == ["new_chat", "ask:halo"]

    def test_subsequent_ask_no_new_chat(self):
        b = FakeBackend()
        c = QwenWebClient(backend=b)
        c.ask("satu")
        c.ask("dua")
        assert b.calls == ["new_chat", "ask:satu", "ask:dua"]

    def test_new_chat_resets_state(self):
        b = FakeBackend()
        c = QwenWebClient(backend=b)
        c.new_chat()
        c.ask("halo")
        assert b.calls == ["new_chat", "ask:halo"]  # tidak ada new_chat ganda

    def test_ask_opens_chat_from_state(self, monkeypatch):
        monkeypatch.setattr("ask_qwen.api.load_state", lambda: {"active_chat_id": "cid-9"})
        b = FakeBackend()
        c = QwenWebClient(backend=b)
        result = c.ask("halo")
        assert result == "answer:halo"
        assert b.calls == ["goto_chat:cid-9", "ask:halo"]  # tanpa new_chat

    def test_ask_state_null_forces_new_chat(self, monkeypatch):
        monkeypatch.setattr("ask_qwen.api.load_state", lambda: {"active_chat_id": None})
        b = FakeBackend()
        c = QwenWebClient(backend=b)
        c.chat_active = True  # daemon menganggap masih aktif — null tetap menang
        c.ask("halo")
        assert b.calls == ["new_chat", "ask:halo"]

    def test_auth_expired_retries_after_refresh(self, monkeypatch):
        monkeypatch.setattr("ask_qwen.auth.refresh", lambda: {"access_token": "x"})
        b = FakeBackend(fail_auth_times=1)
        c = QwenWebClient(backend=b)
        result = c.ask("halo")
        assert result == "answer:halo"
        # refresh via API, browser relaunch (close), lalu ask ulang dari chat baru
        assert b.calls == ["new_chat", "ask:halo", "close", "new_chat", "ask:halo"]

    def test_auth_expired_refresh_fails_returns_hint(self, monkeypatch):
        from ask_qwen.auth import AuthError

        def _fail():
            raise AuthError("refresh token hilang")

        monkeypatch.setattr("ask_qwen.auth.refresh", _fail)
        b = FakeBackend(fail_auth_times=5)
        c = QwenWebClient(backend=b)
        result = c.ask("halo")
        assert "ask-qwen login" in result

    def test_read_error_returns_hint(self):
        b = FakeBackend(fail_auth_times=5)
        c = QwenWebClient(backend=b)
        result = c.read()
        assert result[0]["role"] == "error"
        assert "ask-qwen login" in result[0]["content"]

    def test_ask_rejects_oversized_message_before_browser_call(self, monkeypatch):
        b = FakeBackend()
        c = QwenWebClient(backend=b)
        monkeypatch.setattr("ask_qwen.config.MAX_MESSAGE_CHARS", 3)

        try:
            c.ask("toolong")
        except ValueError as error:
            assert "maximum" in str(error)
        else:
            assert False, "oversized message was accepted"
        assert b.calls == []

    def test_list_chats_clamps_limit(self, monkeypatch):
        b = FakeBackend()
        c = QwenWebClient(backend=b)
        monkeypatch.setattr("ask_qwen.config.MAX_HISTORY_ITEMS", 2)

        assert c.list_chats(99) == [{"title": "chat", "url": "https://chat.qwen.ai/c/1"}]
        assert b.calls == ["list_chats:2"]

    def test_open_chat_then_ask_continues_same_chat(self):
        b = FakeBackend()
        c = QwenWebClient(backend=b)
        url = "https://chat.qwen.ai/c/abc"

        messages = c.open_chat(url)
        result = c.ask("lanjut")

        assert messages == [{"role": "user", "content": "chat lama"}]
        assert result == "answer:lanjut"
        # tidak ada new_chat: ask melanjutkan chat yang dibuka
        assert b.calls == ["open_chat:https://chat.qwen.ai/c/abc", "ask:lanjut"]

    def test_open_chat_error_returns_hint(self):
        b = FakeBackend(fail_auth_times=5)
        c = QwenWebClient(backend=b)
        result = c.open_chat("https://chat.qwen.ai/c/abc")
        assert result[0]["role"] == "error"
        assert "ask-qwen login" in result[0]["content"]

    def test_set_mode_delegates_to_backend(self):
        b = FakeBackend()
        c = QwenWebClient(backend=b)

        assert c.set_mode("thinking") == "Mode diatur ke Thinking."
        assert b.calls == ["set_mode:thinking"]

    def test_stop_generation_delegates_to_backend(self):
        b = FakeBackend()
        c = QwenWebClient(backend=b)

        assert c.stop_generation() == "Generasi dihentikan."
        assert b.calls == ["stop_generation"]

    def test_get_status_delegates_to_backend(self):
        b = FakeBackend()
        c = QwenWebClient(backend=b)

        assert c.get_status()["logged_in"] is True
        assert b.calls == ["get_status"]

    def test_search_chats_rejects_empty_query(self):
        c = QwenWebClient(backend=FakeBackend())

        try:
            c.search_chats(" ")
        except ValueError as error:
            assert "query" in str(error)
        else:
            assert False, "empty query was accepted"
