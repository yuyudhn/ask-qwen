"""Login & token management via Qwen auth API (tanpa browser).

Password di-SHA-256 hex client-side (seperti web app) sebelum dikirim.
Hasil signin disimpan di ~/.ask-qwen/auth.json (0600) bersama refresh cookie;
browser memakai cookie itu untuk membangun sesi web sendiri (lihat dev-notes).
"""

import base64
import hashlib
import json
import time
import uuid

from . import config, transport


class AuthError(Exception):
    """Login/refresh gagal; pesan dari server (rate limit, kredensial, dsb)."""


def _timezone() -> str:
    """Format seperti Date().toString() web tanpa bagian "(Time Zone)"."""
    t = time.localtime()
    return time.strftime("%a %b ", t) + f"{t.tm_mday} " + time.strftime("%Y %H:%M:%S GMT%z", t)


def _jwt_exp(token: str) -> int | None:
    """Ambil claim exp dari JWT tanpa verifikasi signature."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return None


def _request(path: str, payload: dict, token: str | None = None) -> dict:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": config.API_VERSION,
        "source": "web",
        "X-Request-Id": str(uuid.uuid4()),
        "Timezone": _timezone(),
        "User-Agent": config.API_UA,
        "Origin": config.BASE_URL,
        "Referer": config.BASE_URL + "/",
        "x-request-origin": config.BASE_URL,
        "app_waf": config.APP_WAF_TOKEN,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        _, raw = transport.request(
            "POST", config.AUTH_URL + path, headers, json.dumps(payload).encode(), timeout=30,
        )
        body = json.loads(raw)
    except transport.HttpError as e:
        raise AuthError(str(e)) from e
    except ValueError:
        raise AuthError(f"respons bukan JSON: {raw[:120]!r}")
    if not body.get("success"):
        data = body.get("data") or {}
        raise AuthError(f"{data.get('code', 'error')}: {data.get('details', body)}")
    return body["data"]


def load_auth() -> dict | None:
    if not config.AUTH_FILE.exists():
        return None
    try:
        return json.loads(config.AUTH_FILE.read_text())
    except Exception:
        return None


def save_auth(auth: dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    config.AUTH_FILE.write_text(json.dumps(auth, indent=2))
    config.AUTH_FILE.chmod(0o600)


def signin(email: str, password: str) -> dict:
    """Signin via API; simpan token pair + refresh cookie ke auth.json."""
    data = _request(
        "/api/v2/auths/signin",
        {"email": email, "password": hashlib.sha256(password.encode()).hexdigest()},
    )
    auth = {
        "email": data["email"],
        "name": data.get("name"),
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "access_exp": _jwt_exp(data["access_token"]),
        "refresh_exp": _jwt_exp(data["refresh_token"]),
    }
    save_auth(auth)
    return auth


def refresh() -> dict:
    """Tukar refresh_token menjadi token pair baru; auth.json diperbarui."""
    old = load_auth()
    if not old or not old.get("refresh_token"):
        raise AuthError("tidak ada refresh token — jalankan: ask-qwen login")
    data = _request("/api/v2/auths/refresh", {"refresh_token": old["refresh_token"]})
    auth = {
        **old,
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", old["refresh_token"]),
        "access_exp": _jwt_exp(data["access_token"]),
        "refresh_exp": _jwt_exp(data.get("refresh_token", "")) or old.get("refresh_exp"),
    }
    save_auth(auth)
    return auth


def access_token() -> str | None:
    """Token akses yang masih berlaku (refresh otomatis bila <60s lagi expire)."""
    auth = load_auth()
    if not auth:
        return None
    if (auth.get("access_exp") or 0) - time.time() < 60:
        auth = refresh()
    return auth["access_token"]


def session_ok() -> bool:
    """True bila sesi API valid: Bearer accepted oleh chat host.

    Menggantikan heuristic DOM (textarea/tombol Log in) yang rapuh; dipakai
    untuk status login. Probe ringan: GET /api/v2/users/status.
    """
    token = access_token()
    if not token:
        return False
    try:
        _, raw = transport.request(
            "GET",
            config.BASE_URL + "/api/v2/users/status",
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Version": config.API_VERSION,
                "source": "web",
                "X-Request-Id": str(uuid.uuid4()),
                "Timezone": _timezone(),
                "User-Agent": config.API_UA,
                "app_waf": config.APP_WAF_TOKEN,
            },
            b"",
            timeout=15,
        )
        return bool(json.loads(raw).get("success"))
    except Exception:
        return False
