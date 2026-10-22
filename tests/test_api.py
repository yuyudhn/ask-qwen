import json

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


class TestSearchChats:
    def test_url_encodes_query_and_maps_items(self, monkeypatch):
        seen = {}

        def fake(method, path, payload=None):
            seen["path"] = path
            return [{"id": "x" * 36, "title": "a & b", "updated_at": 5, "created_at": 4}]

        monkeypatch.setattr(api, "_request", fake)
        items = api.search_chats("a & b")
        assert "text=a%20%26%20b" in seen["path"]
        assert items[0]["title"] == "a & b"
        assert items[0]["id"] == "x" * 36

    def test_rejects_empty_query(self):
        import pytest

        with pytest.raises(ValueError):
            api.search_chats("  ")


class TestStreamAsk:
    """stream_ask dengan HTTP layer palsu: payload, parse SSE, state update."""

    SSE = (
        'data: {"response.created": {"response_id": "resp-1"}}\n'
        '\n'
        'data: {"choices": [{"delta": {"phase": "answer", "content": "Hel"}}]}\n'
        '\n'
        'data: {"choices": [{"delta": {"phase": "answer", "content": "lo"}}]}\n'
        '\n'
        'data: {"usage": {"output_tokens": 2}}\n'
        '\n'
    )

    def _fake_state(self, monkeypatch):
        box = {"active_chat_id": None}
        monkeypatch.setattr(api, "load_state", lambda: dict(box))
        monkeypatch.setattr(api, "save_state", lambda s: box.update(s))
        monkeypatch.setattr(api, "set_active_chat", lambda cid: box.update(active_chat_id=cid))
        return box

    def _fake_stream(self, monkeypatch, lines, seen):
        """Palsukan transport.stream: catat argumen, yield baris SSE."""
        def fake_stream(method, url, headers, data, idle_timeout_s, total_timeout_s=900):
            seen["method"] = method
            seen["url"] = url
            seen["headers"] = headers
            seen["data"] = data
            seen["idle"] = idle_timeout_s
            return iter(lines)
        monkeypatch.setattr(api.transport, "stream", fake_stream)

    def test_asks_and_collects_answer(self, monkeypatch):
        box = self._fake_state(monkeypatch)
        monkeypatch.setattr(api.auth, "access_token", lambda: "tok")
        monkeypatch.setattr(
            api, "_request", lambda m, p, payload=None: {"id": "chat-new"} if "chats/new" in p else {},
        )

        seen = {}
        self._fake_stream(monkeypatch, [l for l in self.SSE.splitlines() if l], seen)

        deltas = []
        result = api.stream_ask("halo", on_delta=deltas.append)
        assert result["answer"] == "Hello"
        assert deltas == ["Hel", "lo"]
        assert result["response_id"] == "resp-1"
        assert box["active_chat_id"] == "chat-new"
        body = json.loads(seen["data"])
        assert body["chatId"] == "chat-new"
        assert body["messages"][0]["content"] == "halo"
        assert body["messages"][0]["feature_config"]["output_schema"] == "phase"

    def test_rejects_empty_and_oversized(self, monkeypatch):
        self._fake_state(monkeypatch)
        monkeypatch.setattr(api.auth, "access_token", lambda: "tok")
        import pytest

        with pytest.raises(ValueError):
            api.stream_ask("   ")
        monkeypatch.setattr(api.config, "MAX_MESSAGE_CHARS", 3)
        with pytest.raises(ValueError):
            api.stream_ask("toolong")

    def test_chat_mode_research_in_payload(self, monkeypatch):
        self._fake_state(monkeypatch)
        monkeypatch.setattr(api.auth, "access_token", lambda: "tok")
        seen = {}

        def fake_request(method, path, payload=None):
            if "chats/new" in path:
                return {"id": "chat-new"}
            return {}

        monkeypatch.setattr(api, "_request", fake_request)

        self._fake_stream(monkeypatch, [], seen)
        api.stream_ask("riset", chat_mode="deep_research", idle_timeout_s=600)
        assert json.loads(seen["data"])["chat_mode"] == "deep_research"
        assert seen["idle"] == 600

    def test_error_event_raises(self, monkeypatch):
        """SSE error event (high demand, internal error) -> ApiError jelas."""
        self._fake_state(monkeypatch)
        monkeypatch.setattr(api.auth, "access_token", lambda: "tok")
        monkeypatch.setattr(
            api, "_request", lambda m, p, payload=None: {"id": "chat-new"},
        )
        self._fake_stream(
            monkeypatch,
            ['data: {"error": {"message": "high demand, retry later"}}'],
            {},
        )
        import pytest

        with pytest.raises(api.ApiError, match="high demand"):
            api.stream_ask("halo")


class TestLifecycle:
    def test_delete_single_and_batch(self, monkeypatch):
        calls = []

        def fake(method, path, payload=None):
            calls.append((method, path, payload))
            return {}

        monkeypatch.setattr(api, "_request", fake)
        api.delete_chat("c1")
        api.delete_chats(["c1", "c2"])
        assert calls == [
            ("DELETE", "/api/v2/chats/c1", None),
            ("POST", "/api/v2/chats/batch_delete", {"ids": ["c1", "c2"]}),
        ]

    def test_set_pinned_idempotent(self, monkeypatch):
        calls = []
        state = {"pinned": False}

        def fake(method, path, payload=None):
            calls.append(path)
            if method == "GET":
                return dict(state)
            state["pinned"] = not state["pinned"]
            return {}

        monkeypatch.setattr(api, "_request", fake)
        assert api.set_pinned("c1", True) is True
        assert api.set_pinned("c1", True) is True  # sudah pinned — tak toggle lagi
        assert calls.count("/api/v2/chats/c1/pin") == 1
        api.set_pinned("c1", False)
        assert calls.count("/api/v2/chats/c1/pin") == 2

    def test_clone_and_share(self, monkeypatch):
        def fake(method, path, payload=None):
            if path.endswith("/clone"):
                return {"id": "new-chat"}
            return {"url": "https://chat.qwen.ai/s/abc"}

        monkeypatch.setattr(api, "_request", fake)
        assert api.clone_chat("c1") == "new-chat"
        assert api.share_chat("c1") == "https://chat.qwen.ai/s/abc"


class TestUploadFile:
    def _fake_request(self, monkeypatch, tmp_path):
        calls = {}
        f = tmp_path / "notes.txt"
        f.write_text("hello qwen")

        def fake(method, path, payload=None):
            if "getstsToken" in path:
                return {
                    "access_key_id": "AK", "access_key_secret": "SK",
                    "security_token": "TOK", "bucketname": "bucket",
                    "endpoint": "oss.example.com",
                    "file_id": "fid-1", "file_path": "u/fid-1_notes.txt",
                    "file_url": "https://cdn.example.com/u/fid-1_notes.txt",
                }
            if path.endswith("/files/parse"):
                return {"file_id": "fid-1"}
            if "parse/status" in path:
                return [{"file_id": "fid-1", "status": "success"}]
            raise AssertionError(path)

        monkeypatch.setattr(api, "_request", fake)

        class FakeResp:
            status = 200

            def read(self):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(api.transport, "request", lambda *a, **k: (200, b""))
        monkeypatch.setattr(api, "_jwt_user_id", lambda: "uid-1")
        return f

    def test_upload_returns_web_shaped_item(self, monkeypatch, tmp_path):
        f = self._fake_request(monkeypatch, tmp_path)
        item = api.upload_file(f)
        assert item["id"] == "fid-1"
        assert item["url"].startswith("https://cdn.")
        assert item["status"] == "uploaded"
        assert item["collection_name"] == ""
        assert item["file"]["id"] == "fid-1"
        assert item["file"]["meta"]["parse_meta"]["parse_status"] == "success"
        assert item["size"] == 10

    def test_upload_missing_file(self, monkeypatch, tmp_path):
        import pytest

        with pytest.raises(api.ApiError):
            api.upload_file(tmp_path / "nope.txt")


class TestIsGenerating:
    def _chat(self, done):
        return {"chat": {"history": {"messages": {
            "u1": {"role": "user", "content": "hi", "timestamp": 1},
            "a1": {"role": "assistant", "content": "", "timestamp": 2, "done": done,
                   "content_list": [{"phase": "answer", "content": "x"}]},
        }}}}

    def test_true_when_latest_assistant_not_done(self, monkeypatch):
        monkeypatch.setattr(api, "_request", lambda m, p, payload=None: self._chat(False))
        assert api.is_generating("c1") is True

    def test_false_when_done_or_empty(self, monkeypatch):
        monkeypatch.setattr(api, "_request", lambda m, p, payload=None: self._chat(True))
        assert api.is_generating("c1") is False
        monkeypatch.setattr(api, "_request", lambda m, p, payload=None: {"chat": {"history": {"messages": {}}}})
        assert api.is_generating("c1") is False


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
