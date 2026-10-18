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
        self.applied_mode = None
        self.applied_model = None
        self.stop_flags = 0

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




    def set_mode(self, mode: str) -> str:
        self.calls.append(f"set_mode:{mode}")
        return f"Mode diatur ke {mode.capitalize()}."

    def apply_thinking_mode(self, label: str) -> str:
        self.calls.append(f"apply_thinking_mode:{label}")
        self.applied_mode = label
        return f"Mode diatur ke {label}."

    def apply_model(self, model_id: str) -> str:
        self.calls.append(f"apply_model:{model_id}")
        self.applied_model = model_id
        return f"Model diatur ke {model_id}."

    def stop_generation(self) -> str:
        self.calls.append("stop_generation")
        return "Generasi dihentikan."

    def request_stop(self) -> None:
        self.stop_flags += 1



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




    def test_set_mode_persists_and_applies(self, monkeypatch):
        from ask_qwen import api

        seen = {}
        monkeypatch.setattr(api, "set_thinking_mode", lambda m: seen.update(mode=m))
        b = FakeBackend()
        c = QwenWebClient(backend=b)

        assert c.set_mode("thinking") == "Mode diatur ke Thinking."
        assert seen["mode"] == "Thinking"
        assert b.calls == ["apply_thinking_mode:Thinking"]

    def test_set_mode_rejects_unknown(self, monkeypatch):
        from ask_qwen import api

        monkeypatch.setattr(api, "set_thinking_mode", lambda m: None)
        c = QwenWebClient(backend=FakeBackend())
        try:
            c.set_mode("turbo")
            assert False, "unknown mode accepted"
        except ValueError:
            pass

    def test_ask_applies_pending_thinking_mode(self, monkeypatch):
        from ask_qwen import api

        monkeypatch.setattr("ask_qwen.api.load_state", lambda: {"thinking_mode": "Thinking"})
        b = FakeBackend()
        c = QwenWebClient(backend=b)
        c.ask("halo")
        assert b.calls == ["new_chat", "apply_thinking_mode:Thinking", "ask:halo"]

    def test_ask_applies_pending_model(self, monkeypatch):
        from ask_qwen import api

        monkeypatch.setattr("ask_qwen.api.load_state", lambda: {"model": "qwen3.6-plus"})
        b = FakeBackend()
        c = QwenWebClient(backend=b)
        c.ask("halo")
        assert b.calls == ["new_chat", "apply_model:qwen3.6-plus", "ask:halo"]

    def test_stop_generation_sets_flag_directly(self):
        b = FakeBackend()
        c = QwenWebClient(backend=b)

        assert c.stop_generation() == "Stop request diterima."
        assert b.stop_flags == 1  # langsung, tanpa lewat executor/browser thread
        assert b.calls == []


