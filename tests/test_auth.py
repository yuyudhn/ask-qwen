import base64
import hashlib
import json
import os
import time

import pytest

from ask_qwen import auth


def _jwt(payload: dict) -> str:
    def enc(o: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()

    return f"{enc({'alg': 'HS256'})}.{enc(payload)}.sig"


class TestJwtExp:
    def test_decodes_exp(self):
        tok = _jwt({"exp": 12345, "type": "access_token"})
        assert auth._jwt_exp(tok) == 12345

    def test_garbage_returns_none(self):
        assert auth._jwt_exp("not-a-jwt") is None
        assert auth._jwt_exp("") is None


class TestTimezone:
    def test_format_no_padded_day_and_gmt_offset(self):
        tz = auth._timezone()
        # contoh: "Sun Oct 19 2026 01:02:03 GMT+0200" (tanpa parens, hari tak di-pad)
        assert "( " not in tz and ")" not in tz
        parts = tz.split()
        assert parts[0] in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        assert len(parts[1]) == 3
        assert parts[2].isdigit() and not parts[2].startswith("0")
        assert parts[-1].startswith("GMT")


class TestSignin:
    def test_password_is_sha256_hex_hashed(self, monkeypatch, tmp_path):
        captured = {}

        def fake_request(path, payload, token=None):
            captured.update(path=path, payload=payload)
            return {
                "email": "a@b.c",
                "name": "n",
                "access_token": _jwt({"exp": int(time.time()) + 900}),
                "refresh_token": _jwt({"exp": int(time.time()) + 2_000_000}),
            }

        monkeypatch.setattr(auth, "_request", fake_request)
        monkeypatch.setattr(auth.config, "AUTH_FILE", tmp_path / "auth.json")

        auth.signin("a@b.c", "secret")
        assert captured["path"] == "/api/v2/auths/signin"
        assert captured["payload"]["email"] == "a@b.c"
        assert captured["payload"]["password"] == hashlib.sha256(b"secret").hexdigest()
        assert captured["payload"]["password"] != "secret"

    def test_server_error_wrapped_in_auth_error(self, monkeypatch):
        def fake_request(path, payload, token=None):
            raise auth.AuthError("INVALID_CRED: bad credentials")

        monkeypatch.setattr(auth, "_request", fake_request)
        with pytest.raises(auth.AuthError, match="INVALID_CRED"):
            auth.signin("a@b.c", "x")


class TestStorage:
    def test_save_load_roundtrip_and_perms(self, tmp_path, monkeypatch):
        f = tmp_path / "auth.json"
        monkeypatch.setattr(auth.config, "AUTH_FILE", f)
        auth.save_auth({"email": "a@b.c", "refresh_token": "rt"})
        assert (f.stat().st_mode & 0o777) == 0o600
        assert auth.load_auth()["refresh_token"] == "rt"

    def test_load_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth.config, "AUTH_FILE", tmp_path / "nope.json")
        assert auth.load_auth() is None


class TestAccessToken:
    def test_refreshes_when_expiring_soon(self, tmp_path, monkeypatch):
        f = tmp_path / "auth.json"
        monkeypatch.setattr(auth.config, "AUTH_FILE", f)
        old_rt = _jwt({"exp": int(time.time()) + 2_000_000})
        auth.save_auth({
            "email": "a@b.c",
            "access_token": _jwt({"exp": int(time.time()) + 30}),  # < 60s
            "refresh_token": old_rt,
            "access_exp": int(time.time()) + 30,
        })

        new_at = _jwt({"exp": int(time.time()) + 900})
        calls = []

        def fake_request(path, payload, token=None):
            calls.append(path)
            return {"access_token": new_at, "refresh_token": old_rt}

        monkeypatch.setattr(auth, "_request", fake_request)
        assert auth.access_token() == new_at
        assert calls == ["/api/v2/auths/refresh"]
        assert auth.load_auth()["access_token"] == new_at

    def test_refresh_without_token_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth.config, "AUTH_FILE", tmp_path / "nope.json")
        with pytest.raises(auth.AuthError, match="login"):
            auth.refresh()


class TestSessionOk:
    def _save(self, tmp_path, monkeypatch, exp_delta=900):
        monkeypatch.setattr(auth.config, "AUTH_FILE", tmp_path / "auth.json")
        auth.save_auth({
            "email": "a@b.c",
            "access_token": _jwt({"exp": int(time.time()) + exp_delta}),
            "refresh_token": _jwt({"exp": int(time.time()) + 2_000_000}),
            "access_exp": int(time.time()) + exp_delta,
        })

    def test_true_when_api_accepts_bearer(self, tmp_path, monkeypatch):
        self._save(tmp_path, monkeypatch)
        monkeypatch.setattr(
            auth.transport, "request",
            lambda *a, **k: (200, b'{"success": true}'),
        )
        assert auth.session_ok() is True

    def test_false_without_token(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth.config, "AUTH_FILE", tmp_path / "nope.json")
        assert auth.session_ok() is False

    def test_false_on_api_error(self, tmp_path, monkeypatch):
        self._save(tmp_path, monkeypatch)

        def boom(*a, **k):
            raise auth.transport.HttpError("down")

        monkeypatch.setattr(auth.transport, "request", boom)
        assert auth.session_ok() is False
