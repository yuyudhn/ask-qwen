from ask_qwen import api


class _FakeRequest:
    """Gerbang panggilan _request per method+path."""

    def __init__(self, routes: dict):
        self.routes = routes

    def __call__(self, method, path, payload=None):
        key = f"{method} {path}"
        for prefix, resp in self.routes.items():
            if key.startswith(prefix):
                return resp() if callable(resp) else resp
        raise AssertionError(f"unexpected call: {key}")


def _chat(i: int) -> dict:
    return {
        "id": f"id-{i}",
        "title": f"chat {i}",
        "updated_at": 1000 + i,
        "created_at": 1000 + i,
    }


class TestListChats:
    def test_paginates_until_limit(self, monkeypatch):
        def fake(method, path, payload=None):
            assert method == "GET"
            if "page=1" in path:
                return [_chat(i) for i in range(20)]
            if "page=2" in path:
                return [_chat(i) for i in range(20, 30)]
            raise AssertionError(path)

        monkeypatch.setattr(api, "_request", fake)
        items = api.list_chats(25)
        assert len(items) == 25
        assert items[0]["id"] == "id-0"
        assert items[-1]["id"] == "id-24"
        assert items[0]["url"].endswith("/c/id-0")

    def test_stops_when_short_page(self, monkeypatch):
        monkeypatch.setattr(api, "_request", lambda m, p, payload=None: [_chat(0)])
        assert len(api.list_chats(20)) == 1


class TestParseChatId:
    def test_accepts_bare_id_and_url(self):
        cid = "aaaa1111-bbbb-2222-cccc-3333dddd4444"  # id sintetis, bukan data asli
        assert api.parse_chat_id(cid) == cid
        assert api.parse_chat_id(f"https://chat.qwen.ai/c/{cid}") == cid

    def test_rejects_garbage(self):
        import pytest

        with pytest.raises(api.ApiError):
            api.parse_chat_id("not a chat ref")


class TestChatTranscript:
    def _node(self, mid, role, content, parent=None, children=(), ts=0, content_list=None):
        return {
            "id": mid, "role": role, "content": content, "parentId": parent,
            "childrenIds": list(children), "timestamp": ts, "content_list": content_list,
        }

    def test_walks_first_child_branch(self, monkeypatch):
        history = {
            "u1": self._node("u1", "user", "halo", None, ["a1", "a2"], 1),
            "a1": self._node("a1", "assistant", "", "u1", ["u2"], 2, content_list=[
                {"phase": "thinking", "content": "alasan..."},
                {"phase": "answer", "content": "jawaban utama"},
            ]),
            "a2": self._node("a2", "assistant", "cabang diabaikan", "u1", [], 3),
            "u2": self._node("u2", "user", [{"text": "lanjut"}], "a1", [], 4),
        }
        monkeypatch.setattr(
            api, "_request",
            lambda m, p, payload=None: {"chat": {"history": {"messages": history, "currentId": "u2"}}},
        )
        msgs = api.chat_transcript("whatever")
        assert msgs == [
            {"role": "user", "content": "halo"},
            {"role": "assistant", "content": "jawaban utama"},
            {"role": "user", "content": "lanjut"},
        ]

    def test_empty_history(self, monkeypatch):
        monkeypatch.setattr(
            api, "_request",
            lambda m, p, payload=None: {"chat": {"history": {"messages": {}}}},
        )
        assert api.chat_transcript("x") == []
