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


# Thinking mode bersifat client-side: frontend menaruhnya di localStorage
# 'qwen-thinking_mode' dan mengirimnya per-request (feature_config). Tidak ada
# endpoint server — diterapkan ke store browser, bukan "via API".


def set_thinking_mode(label: str) -> None:
    """Simpan mode ('Auto'|'Thinking'|'Fast') untuk diterapkan ke browser."""
    state = load_state()
    state["thinking_mode"] = label
    save_state(state)


def get_thinking_mode() -> str | None:
    return load_state().get("thinking_mode")


# Model LLM bersifat per-request (field `model` di completions) dan store
# frontend tidak di-persist (modelsStore persist disabled) — dipilih dari URL
# ?model= saat boot atau dari chat.models bila ada. Tidak ada endpoint untuk
# men-setnya server-side (semua bentuk updateChatById {status:true} tapi tak
# ter-persist — verified live).


def list_models() -> list[dict]:
    """Model yang tersedia: GET /api/v2/models/ → [{id, name}, ...].

    Envelope dobel: body.data berisi {data: [...]} (verified live)."""
    data = _request("GET", "/api/v2/models/")
    if isinstance(data, dict):
        data = data.get("data") or data.get("models") or []
    items = data if isinstance(data, list) else []
    return [{"id": m["id"], "name": m.get("name") or m.get("modelName") or m["id"]} for m in items if isinstance(m, dict)]


def set_model(model_id: str) -> None:
    state = load_state()
    state["model"] = model_id
    save_state(state)


def get_model() -> str | None:
    return load_state().get("model")


def is_generating(chat_id: str) -> bool:
    """True bila chat punya generasi yang sedang berjalan.

    Sinyal server: assistant message terakhir (by timestamp) ber-flag
    done=False saat stream aktif, done=True saat selesai (verified live —
    currentResponseIds justru tidak reliable: tetap terisi setelah selesai).
    """
    data = _request("GET", f"/api/v2/chats/{chat_id}")
    nodes = (data.get("chat") or {}).get("history", {}).get("messages") or {}
    assistants = [n for n in nodes.values() if isinstance(n, dict) and n.get("role") == "assistant"]
    if not assistants:
        return False
    latest = max(assistants, key=lambda n: n.get("timestamp") or 0)
    return latest.get("done") is False


# -- streaming chat (direct API, tanpa browser) ------------------------------
#
# WAF /chat/completions di-bypass dengan signature aplikasi mobile
# (User-Agent QwenChatAndroid/1.0 + source qwen_app — verified live).
# Payload mengikuti capture dari web UI (fe 0.3.19); respons SSE
# `data: {...}` dengan chunk di choices[].delta.content (phase "answer").


def _chat_headers(token: str) -> dict:
    return {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "x-request-id": str(uuid.uuid4()),
        "source": config.CHAT_SOURCE,
        "x-device-id": str(uuid.uuid4()),
        "timezone": auth._timezone(),
        "User-Agent": config.CHAT_UA,
    }


def request_stop() -> None:
    """Tandai permintaan stop; stream_ask yang memproses (bisa lintas proses)."""
    state = load_state()
    state["stop_requested"] = True
    save_state(state)


def _feature_config(state: dict) -> tuple[str, dict]:
    label = state.get("thinking_mode") or "Fast"
    model = state.get("model") or "qwen3.7-plus"
    fc = {
        "thinking_enabled": label != "Fast",
        "output_schema": "phase",
        "research_mode": "normal",
        "auto_thinking": label == "Auto",
        "thinking_mode": label,
        "auto_search": True,
    }
    return model, fc


def stream_ask(message: str, on_delta=None, chat_id: str | None = None) -> dict:
    """Kirim pesan ke Qwen dan stream jawabannya — murni HTTP, tanpa browser.

    - on_delta(chunk): callback teks answer-phase saat streaming.
    - Lanjutkan chat dari state.json (active_chat_id) atau chat_id arg;
      buat chat baru bila belum ada.
    - Stop lintas-proses: state.json stop_requested (ditulis cmd stop).
    - Return {"chat_id", "answer", "response_id", "usage"}; active_chat_id
      di state diperbarui ke chat ini.
    """
    import http.client
    import socket
    import time

    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must not be empty")
    if len(message) > config.MAX_MESSAGE_CHARS:
        raise ValueError(
            f"message exceeds maximum length of {config.MAX_MESSAGE_CHARS} characters"
        )

    token = auth.access_token()
    if not token:
        raise ApiError("belum login — jalankan: ask-qwen login")

    state = load_state()
    state.pop("stop_requested", None)  # bersihkan flag siklus sebelumnya
    save_state(state)
    if chat_id is None:
        chat_id = state.get("active_chat_id")

    parent = ""
    if chat_id:
        try:
            data = _request("GET", f"/api/v2/chats/{chat_id}")
            cur = (data.get("chat") or {}).get("history", {}).get("currentId")
            if cur:
                parent = cur
        except ApiError:
            chat_id = None  # chat terhapus/diarsipkan — mulai chat baru
    if not chat_id:
        chat_id = _request("POST", "/api/v2/chats/new", {"chat_mode": "normal", "project_id": None})["id"]

    model, fc = _feature_config(state)
    ts = int(time.time())
    body = {
        "stream": True,
        "version": "2.1",
        "incremental_output": True,
        "chatId": chat_id,
        "parentId": parent,
        "chat_id": chat_id,
        "chat_mode": "normal",
        "model": model,
        "parent_id": parent or None,
        "messages": [{
            "id": None,
            "fid": str(uuid.uuid4()),
            "parentId": parent or None,
            "childrenIds": [str(uuid.uuid4())],
            "role": "user",
            "content": message,
            "user_action": "chat",
            "files": [],
            "timestamp": ts,
            "models": [model],
            "model": "",
            "chat_type": "t2t",
            "sub_chat_type": "t2t",
            "feature_config": fc,
        }],
        "timestamp": ts,
    }

    conn = http.client.HTTPSConnection("chat.qwen.ai", 443, timeout=config.CHAT_IDLE_TIMEOUT_S)
    try:
        conn.request(
            "POST",
            f"/api/v2/chat/completions?chat_id={chat_id}",
            body=json.dumps(body).encode(),
            headers=_chat_headers(token),
        )
        resp = conn.getresponse()
        if resp.status != 200:
            raise ApiError(f"HTTP {resp.status}: {resp.read()[:200]!r}")

        answer_parts: list[str] = []
        usage: dict = {}
        response_id: str | None = None
        while True:
            try:
                line = resp.readline()
            except (socket.timeout, TimeoutError):
                break  # diam lebih dari CHAT_IDLE_TIMEOUT_S — anggap selesai/hang
            if not line:
                break
            line = line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except ValueError:
                continue
            if obj.get("success") is False:
                raise ApiError(str((obj.get("data") or {}).get("details", obj)))
            created = obj.get("response.created") or {}
            if created.get("response_id"):
                response_id = created["response_id"]
            for ch in obj.get("choices") or []:
                delta = (ch.get("delta") or {})
                if delta.get("phase") == "answer" and delta.get("content"):
                    answer_parts.append(delta["content"])
                    if on_delta:
                        on_delta(delta["content"])
            if obj.get("usage"):
                usage = obj["usage"]

            if response_id and load_state().get("stop_requested"):
                try:
                    _request(
                        "POST",
                        f"/api/v2/chat/completions/stop?chat_id={chat_id}",
                        {"chat_id": chat_id, "response_id": response_id},
                    )
                except ApiError:
                    pass
                state = load_state()
                state.pop("stop_requested", None)
                save_state(state)
                break
    finally:
        conn.close()

    answer = "".join(answer_parts)
    set_active_chat(chat_id)
    return {"chat_id": chat_id, "answer": answer, "response_id": response_id, "usage": usage, "model": model}
