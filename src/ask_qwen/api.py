"""Chat API client: read-path endpoints chat.qwen.ai via Bearer (tanpa browser).

Hanya endpoint tanpa WAF-guard — chat/completions tetap lewat browser
(lihat dev-notes/qwen-api-research.md). State chat aktif dibagi CLI ↔ daemon
lewat ~/.ask-qwen/state.json.
"""

import json
import re
import urllib.error
import urllib.request
import uuid

from . import auth, config


class ApiError(Exception):
    """Panggilan API gagal (jaringan, auth, atau envelope success=false)."""


def parse_chat_id(ref: str) -> str:
    """Terima URL chat (.../c/<id>) atau chat id polos, kembalikan id."""
    ref = (ref or "").strip()
    if "/c/" in ref:
        ref = ref.split("/c/", 1)[1].split("/")[0].split("?")[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,}", ref or ""):
        raise ApiError(f"bukan chat id/URL yang valid: {ref!r}")
    return ref


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    token = auth.access_token()
    if not token:
        raise ApiError("belum login — jalankan: ask-qwen login")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        config.BASE_URL + path,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Version": config.API_VERSION,
            "source": "web",
            "X-Request-Id": str(uuid.uuid4()),
            "Timezone": auth._timezone(),
            "User-Agent": config.API_UA,
            "app_waf": config.APP_WAF_TOKEN,
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise ApiError(f"HTTP {e.code}: {e.read()[:200]!r}") from e
    except urllib.error.URLError as e:
        raise ApiError(f"koneksi gagal: {e.reason}") from e
    if not body.get("success"):
        data_ = body.get("data") or {}
        raise ApiError(f"{data_.get('code', 'error')}: {data_.get('details', body)}")
    return body["data"]


# -- chat items -------------------------------------------------------------


def _chat_item(raw: dict) -> dict:
    return {
        "id": raw["id"],
        "title": raw.get("title") or "(untitled)",
        "url": f"{config.BASE_URL}/c/{raw['id']}",
        "updated_at": raw.get("updated_at"),
        "created_at": raw.get("created_at"),
    }


def list_chats(limit: int = 20) -> list[dict]:
    """Chat terbaru dari sidebar, paginasi sampai limit (maks MAX_HISTORY_ITEMS)."""
    limit = max(1, min(limit, config.MAX_HISTORY_ITEMS))
    items: list[dict] = []
    page = 1
    while len(items) < limit:
        data = _request("GET", f"/api/v2/chats/?page={page}&exclude_project=true")
        batch = data if isinstance(data, list) else []
        if not batch:
            break
        items.extend(_chat_item(c) for c in batch)
        if len(batch) < 20:
            break
        page += 1
    return items[:limit]


def search_chats(query: str, limit: int = 20) -> list[dict]:
    if not query or not query.strip():
        raise ValueError("query must not be empty")
    limit = max(1, min(limit, config.MAX_HISTORY_ITEMS))
    items: list[dict] = []
    page = 1
    from urllib.parse import quote

    while len(items) < limit:
        data = _request("GET", f"/api/v2/chats/search?text={quote(query)}&page={page}")
        batch = data if isinstance(data, list) else []
        if not batch:
            break
        items.extend(_chat_item(c) for c in batch)
        if len(batch) < 20:
            break
        page += 1
    return items[:limit]


def chat_exists(chat_id: str) -> bool:
    try:
        _request("GET", f"/api/v2/chats/{chat_id}")
        return True
    except ApiError:
        return False


# -- transcript -------------------------------------------------------------


def _segment_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for seg in content:
            if isinstance(seg, dict):
                parts.append(str(seg.get("text") or seg.get("content") or ""))
            else:
                parts.append(str(seg))
        return "\n".join(p for p in parts if p)
    return "" if content is None else str(content)


def chat_transcript(chat_id: str) -> list[dict]:
    """Transcript linear: [{role, content}] dengan mengikuti childrenIds pertama
    dari root (cabang edit/regenerate diabaikan — sama dengan perilaku parser DOM).
    """
    data = _request("GET", f"/api/v2/chats/{chat_id}")
    history = (data.get("chat") or {}).get("history") or {}
    # Bentuk nyata: {"messages": {id: node}, "currentId": ..., ...}
    nodes = history.get("messages") if isinstance(history, dict) else None
    if not isinstance(nodes, dict):
        nodes = {}
    roots = [n for n in nodes.values() if isinstance(n, dict) and not n.get("parentId")]
    roots.sort(key=lambda n: n.get("timestamp") or 0)
    messages = []
    for root in roots:
        node = root
        while isinstance(node, dict):
            role = node.get("role")
            # Teks asisten ada di content_list (phase segments); field content
            # biasanya kosong. Thinking phase sengaja dilewati — sama dengan
            # perilaku parser DOM yang menyembunyikan reasoning.
            content_list = node.get("content_list")
            if isinstance(content_list, list) and content_list:
                text = "\n".join(
                    str(s.get("content") or "")
                    for s in content_list
                    if isinstance(s, dict) and s.get("phase") == "answer"
                )
            else:
                text = _segment_text(node.get("content"))
            if role in ("user", "assistant") and text.strip():
                messages.append({"role": role, "content": text})
            children = [c for c in (node.get("childrenIds") or []) if c in nodes]
            nxt = nodes.get(children[0]) if children else None
            if nxt is node:  # siklus data rusak — berhenti
                break
            node = nxt
    return messages


# -- state bersama CLI <-> daemon -------------------------------------------


def load_state() -> dict:
    if not config.STATE_FILE.exists():
        return {}
    try:
        return json.loads(config.STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    config.STATE_FILE.write_text(json.dumps(state, indent=2))
    config.STATE_FILE.chmod(0o600)


def set_active_chat(chat_id: str | None) -> None:
    state = load_state()
    state["active_chat_id"] = chat_id
    save_state(state)
