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
