"""PlaywrightBackend: automasi UI chat.qwen.ai via persistent profile."""

import json
import logging
import os
import re
import threading

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from . import config

log = logging.getLogger(__name__)

_CHAT_ID_RE = re.compile(r"[A-Za-z0-9_-]{6,}")


def normalize_chat_ref(ref: str) -> str:
    """Terima URL chat penuh atau chat ID polos (dari `list`), kembalikan URL."""
    ref = (ref or "").strip()
    if ref.startswith(config.BASE_URL + "/c/"):
        return ref
    if _CHAT_ID_RE.fullmatch(ref):
        return f"{config.BASE_URL}/c/{ref}"
    raise ValueError(
        f"Referensi chat tidak valid: {ref!r}. Pakai URL "
        f"{config.BASE_URL}/c/... atau chat ID dari `ask-qwen list`."
    )


def _raise_if_generating(page) -> None:
    """list/search mengklik-klik sidebar dan membunuh stream yang sedang jalan."""
    try:
        generating = page.locator(config.STOP_BTN_SEL).is_visible()
    except Exception:
        return  # status generasi tidak terbaca — jangan blokir
    if generating:
        raise RuntimeError(
            "Generasi sedang berjalan; perintah ini akan mengganggu stream. "
            "Tunggu selesai atau jalankan `ask-qwen stop`."
        )


def _load_auth() -> dict | None:
    """Lazy import agar modul auth (urllib) tidak dipakai di test DOM murni."""
    try:
        from .auth import load_auth

        return load_auth()
    except Exception:
        log.exception("Gagal baca auth.json")
        return None


def _playwright_cookies(raw: object) -> list[dict]:
    """Keep exported cookies compatible with Playwright's cookie schema."""
    if not isinstance(raw, list):
        raise ValueError("cookies.json must contain a list of cookies")
    cookies = []
    for cookie in raw:
        if not isinstance(cookie, dict):
            continue
        item = dict(cookie)
        same_site = item.get("sameSite")
        if isinstance(same_site, str):
            item["sameSite"] = {"strict": "Strict", "lax": "Lax", "none": "None"}.get(
                same_site.lower(), "None"
            )
        elif "sameSite" in item:
            item.pop("sameSite")
        if "partitionKey" in item and not isinstance(item["partitionKey"], str):
            item.pop("partitionKey")
        cookies.append(item)
    return cookies


class AuthExpired(Exception):
    """Cookie/session expired — perlu login manual (headed)."""


class WAFChallenge(Exception):
    """Halaman WAF/punish terdeteksi — bukan masalah login."""


class ProfileLocked(Exception):
    """Persistent profile sedang dipakai instance browser lain."""


class PlaywrightBackend:
    def __init__(self) -> None:
        self._pw = None
        self._ctx = None
        self._page = None
        self.has_active_chat = False
        self._operation_lock = threading.RLock()

    # -- lifecycle ---------------------------------------------------------

    def _launch(self, headless: bool = True) -> None:
        self._close()
        config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        if (config.PROFILE_DIR / "SingletonLock").exists():
            raise ProfileLocked(
                "profile Qwen sedang dipakai proses lain; tutup instance "
                "ask-qwen yang lama lalu coba lagi."
            )
        try:
            self._pw = sync_playwright().start()
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(config.PROFILE_DIR),
                headless=headless,
                viewport={"width": 1280, "height": 900},
            )
            self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
            if config.COOKIES_FILE.exists():
                try:
                    cookies = _playwright_cookies(json.loads(config.COOKIES_FILE.read_text()))
                    self._ctx.add_cookies(cookies)
                    log.info("Seeded %d cookies dari cookies.json", len(cookies))
                except Exception:
                    log.exception("Gagal seed cookies (lanjut dengan profile saja)")
            auth = _load_auth()
            if auth and auth.get("refresh_token"):
                # Seed TERAKHIR agar menang atas refresh_token usang di cookies.json.
                # Sesi web dibangun dari cookie ini oleh app boot (GET auths/refresh);
                # lihat dev-notes/qwen-api-research.md bagian "Web session model".
                self._ctx.add_cookies([{
                    "name": "refresh_token",
                    "value": auth["refresh_token"],
                    "domain": ".qwen.ai",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }])
                log.info("Seeded refresh_token cookie dari auth.json")
            # Marker logout lokal membuat app skip boot refresh walau cookie valid;
            # hapus sebelum script app jalan. dev-notes: "Web session model".
            self._ctx.add_init_script(
                "try { localStorage.removeItem('qwen_token_logged_out_marker'); } catch (e) {}"
            )
        except Exception:
            self._close()
            raise

    def _close(self) -> None:
        # ctx.close() flush storage; pw.stop() matikan driver (close() tidak ada)
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:
            log.exception("Gagal menutup browser context")
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            log.exception("Gagal menghentikan driver Playwright")
        self._ctx = None
        self._pw = None
        self._page = None

    def close(self) -> None:
        with self._operation_lock:
            self._close()
            self.has_active_chat = False

    def _ensure_context(self) -> None:
        if self._ctx is None:
            self._launch(headless=True)

    @property
    def page(self):
        self._ensure_context()
        return self._page

    # -- auth --------------------------------------------------------------

    def _is_logged_in(self) -> bool:
        """Logged-in bila chatbox textarea terlihat DAN tombol Log in tidak ada.

        Qwen sekarang menampilkan textarea bahkan untuk user yang belum login,
        jadi textarea saja tidak cukup — cek juga tidak adanya tombol login.

        Hindari goto ulang bila sudah di chat.qwen.ai: navigasi saat chat
        aktif me-remount DOM dan bisa menempel pada stream SSE.
        """
        page = self.page
        try:
            if "chat.qwen.ai" not in (page.url or ""):
                page.goto(
                    config.BASE_URL,
                    timeout=config.NAV_TIMEOUT_MS,
                    wait_until="domcontentloaded",
                )
                if "_____tmd_____" in page.url or "punish" in page.url:
                    raise WAFChallenge(
                        "WAF challenge terdeteksi (RGV587). Tunggu sebentar lalu "
                        "coba lagi; jika terus terjadi, buka chat.qwen.ai di "
                        "browser biasa sekali lalu ulangi."
                    )
            page.wait_for_selector(
                config.TEXTAREA_SEL, timeout=20_000, state="visible"
            )
            # Qwen shows textarea even when logged out; reject if login button visible.
            # Token refresh via JS can take ~1s, so if login button is present,
            # wait briefly for it to disappear (refresh_token flow).
            login_btn = page.locator(config.LOGIN_BTN_SEL)
            if login_btn.count() > 0 and login_btn.first.is_visible():
                try:
                    login_btn.first.wait_for(state="hidden", timeout=5_000)
                except PWTimeoutError:
                    return False
            return True
        except PWTimeoutError:
            return False

    # -- pembacaan pesan terstruktur ----------------------------------------

    _EXTRACT_MESSAGES_JS = """(cfg) => {
      const items = [...document.querySelectorAll(cfg.item)];
      return items.map(m => {
        const role = m.classList.contains(cfg.userCls) ? 'user'
                   : m.classList.contains(cfg.assistantCls) ? 'assistant' : null;
        const sel = role === 'user' ? cfg.userBody
                  : role === 'assistant' ? cfg.assistantBody : null;
        const body = sel ? m.querySelector(sel) : null;
        return {role: role, text: body ? body.innerText : m.innerText};
      }).filter(m => m.role && m.text && m.text.trim());
    }"""

    def _extract_messages(self, page) -> list[dict]:
        """Pesan chat aktif sebagai [{role, content}] via class .qwen-chat-message."""
        from .parser import parse_messages

        raw = page.evaluate(
            self._EXTRACT_MESSAGES_JS,
            {
                "item": config.MSG_ITEM_SEL,
                "userCls": config.MSG_USER_CLS,
                "assistantCls": config.MSG_ASSISTANT_CLS,
                "userBody": config.MSG_USER_BODY_SEL,
                "assistantBody": config.MSG_ASSISTANT_BODY_SEL,
            },
        )
        return parse_messages([(m["role"], m["text"]) for m in raw])

    # -- tools -------------------------------------------------------------

    def ask(self, message: str, on_delta=None) -> str:
        from .parser import extract_answer

        with self._operation_lock:
            if not self._is_logged_in():
                raise AuthExpired("Sesi login Qwen kadaluarsa (chatbox tidak ditemukan).")

            page = self.page
            if not self.has_active_chat:
                self._new_chat_in_page(page)

            page.wait_for_selector(config.TEXTAREA_SEL, timeout=15_000, state="visible")
            page.fill(config.TEXTAREA_SEL, message)
            page.wait_for_selector(config.SEND_BTN_SEL, timeout=10_000)
            previous_text = page.locator(config.MAIN_SEL).inner_text()
            page.click(config.SEND_BTN_SEL)

            answer = self._wait_response_done(page, previous_text=previous_text, on_delta=on_delta)
            raw = page.locator(config.MAIN_SEL).inner_text()
            self.has_active_chat = True
            return extract_answer(raw, user_message=message) or answer

    def _wait_response_done(self, page, previous_text: str = "", on_delta=None) -> str:
        """Tunggu stream selesai via sinyal tombol Stop (terverifikasi dari
        DOM Qwen): Stop muncul saat generasi, hilang saat selesai.

        Fallback bila selector Stop berubah: teks stabil + masa tenggang.
        on_delta: callback(chunk) dipanggil dengan baris-baris jawaban baru
        (bersih dari noise UI) saat generasi berjalan — untuk streaming CLI.
        """
        import time

        main = page.locator(config.MAIN_SEL)
        stop = page.locator(config.STOP_BTN_SEL)
        sent_at = time.monotonic()
        deadline = sent_at + config.RESPONSE_TIMEOUT_MS / 1000
        start_deadline = sent_at + config.RESPONSE_START_TIMEOUT_MS / 1000
        stop_ever_seen = False
        last_text, stable_since = "", None
        # state streaming: kirim hanya suffix baru; pecahan baris di-buffer
        buf, streamed = "", previous_text

        def emit(current: str) -> None:
            nonlocal buf, streamed
            if not on_delta or current == streamed:
                return
            if current.startswith(streamed):
                buf += current[len(streamed) :]
            else:
                # re-render React mengganti teks di tengah (bukan append):
                # buang pecahan baris yang belum terkirim (sudah tertulis ulang
                # di DOM), lanjutkan dari suffix setelah prefix bersama.
                # shortcut: streaming best-effort, jawaban final tetap dari
                # extract_answer/read yang membaca ulang DOM utuh.
                prefix = os.path.commonprefix([streamed, current])
                buf = current[len(prefix) :]
            streamed = current
            lines = buf.split("\n")
            buf = lines.pop()
            for line in lines:
                stripped = line.strip()
                if stripped and stripped not in config.STRIP_LINES:
                    on_delta(line + "\n")

        def flush() -> None:
            nonlocal buf
            if on_delta and buf.strip() and buf.strip() not in config.STRIP_LINES:
                on_delta(buf)
            buf = ""

        try:
            while time.monotonic() < deadline:
                now = time.monotonic()
                try:
                    stop_visible = stop.is_visible()
                except Exception:
                    stop_visible = False
                if stop_visible:
                    stop_ever_seen = True
                try:
                    current = main.inner_text()
                except Exception:
                    current = last_text
                emit(current)

                if stop_ever_seen and not stop_visible:
                    return current  # generasi selesai

                if current != last_text:
                    last_text, stable_since = current, now
                elif (
                    current
                    and current != previous_text
                    and stable_since
                    and (now - stable_since) * 1000 >= config.RESPONSE_STABLE_MS
                    and not stop_visible
                    and (not stop_ever_seen and now - sent_at >= config.RESPONSE_FALLBACK_GRACE_S)
                ):
                    # ponytail: fallback teks-stabil; risiko salah-deteksi hanya
                    # bila Stop tertunda > masa tenggang, upgrade ke SSE bila perlu
                    return current

                if (
                    not stop_ever_seen
                    and now >= start_deadline
                    and current == previous_text
                ):
                    raise PWTimeoutError(
                        "Respons Qwen tidak dimulai (pesan mungkin gagal terkirim)."
                    )
                time.sleep(config.POLL_INTERVAL_S)

            raise PWTimeoutError("Respons Qwen tidak selesai dalam batas waktu.")
        finally:
            flush()

    def _new_chat_in_page(self, page) -> None:
        try:
            page.click(config.NEW_CHAT_BTN_SEL, timeout=10_000)
            page.wait_for_selector(config.TEXTAREA_SEL, timeout=15_000)
        except PWTimeoutError:
            # di halaman home, chatbox langsung tersedia tanpa tombol New Chat
            page.goto(config.BASE_URL, timeout=config.NAV_TIMEOUT_MS)
            page.wait_for_selector(config.TEXTAREA_SEL, timeout=15_000)

    def new_chat(self) -> str:
        with self._operation_lock:
            self._ensure_context()
            page = self.page
            self._new_chat_in_page(page)
            self.has_active_chat = True
            return "Chat baru siap."

    def stop_generation(self) -> str:
        with self._operation_lock:
            if not self._is_logged_in():
                raise AuthExpired("Sesi login Qwen kadaluarsa.")
            stop = self.page.locator(config.STOP_BTN_SEL)
            if not stop.is_visible():
                return "Tidak ada generasi aktif."
            stop.click()
            return "Generasi dihentikan."

    def get_status(self) -> dict:
        with self._operation_lock:
            # Login status via probe API (Bearer), bukan heuristic DOM —
            # Qwen sering mengubah struktur halaman (lihat dev-notes).
            try:
                from .auth import session_ok

                logged_in = session_ok()
            except Exception:
                log.exception("session_ok gagal")
                logged_in = False
            page = self.page
            try:
                generating = page.locator(config.STOP_BTN_SEL).is_visible()
            except Exception:
                generating = False
            return {
                "logged_in": logged_in,
                "generating": generating,
                "active_chat": self.has_active_chat,
                "url": page.url,
            }

    def read(self) -> list[dict]:
        """Baca pesan chat aktif sebagai [{role: user|assistant, content}]."""
        with self._operation_lock:
            if not self._is_logged_in():
                raise AuthExpired("Sesi login Qwen kadaluarsa.")
            page = self.page
            messages = self._extract_messages(page)
            if messages:
                return messages
            # fallback toleran: struktur DOM berubah → snapshot mentah
            raw = page.locator(config.MAIN_SEL).inner_text()
            cleaned = "\n".join(
                line for line in raw.splitlines()
                if line.strip() and line.strip() != "Auto"
            ).strip()
            return [{"role": "transcript", "content": cleaned}] if cleaned else []

    def list_chats(self, limit: int = 20) -> list[dict]:
        """Daftar chat di sidebar. Item tidak punya href → URL diperoleh
        dengan mengklik tiap item (SPA routing) dan membaca location.href."""
        with self._operation_lock:
            if not self._is_logged_in():
                raise AuthExpired("Sesi login Qwen kadaluarsa.")
            page = self.page
            _raise_if_generating(page)
            try:
                # daftar chat termuat lazily setelah chatbox — tunggu sebentar
                page.wait_for_selector(
                    config.CHAT_HISTORY_SEL, timeout=10_000, state="attached"
                )
            except PWTimeoutError:
                return []  # riwayat kosong atau tidak termuat
            original_url = page.url
            chats: list[dict] = []
            seen_urls: set[str] = set()
            index = 0
            stale_clicks = 0
            while len(chats) < limit and stale_clicks < 3:
                items = page.locator(config.CHAT_HISTORY_SEL)
                if index >= items.count():
                    break
                item = items.nth(index)
                index += 1
                try:
                    title = item.inner_text().strip()
                except Exception:
                    continue
                if not title:
                    continue
                before = page.url
                try:
                    item.click(timeout=5_000)
                    page.wait_for_function(
                        "u => location.href !== u", arg=before, timeout=8_000
                    )
                except PWTimeoutError:
                    # klik tidak mengubah URL (mis. sudah di chat itu)
                    if page.url == before and "/c/" not in before:
                        stale_clicks += 1
                        continue
                url = page.url
                if "/c/" in url and url not in seen_urls:
                    seen_urls.add(url)
                    chats.append({"title": title, "url": url, "id": url.rsplit("/c/", 1)[-1]})
            # kembalikan halaman semula
            if page.url != original_url and "chat.qwen.ai" in original_url:
                try:
                    page.goto(
                        original_url,
                        timeout=config.NAV_TIMEOUT_MS,
                        wait_until="domcontentloaded",
                    )
                except Exception:
                    log.exception("Gagal mengembalikan halaman semula setelah list_chats")
            return chats

    def search_chats(self, query: str) -> list[dict]:
        query = (query or "").strip()
        if not query:
            raise ValueError("query must not be empty")
        with self._operation_lock:
            if not self._is_logged_in():
                raise AuthExpired("Sesi login Qwen kadaluarsa.")
            page = self.page
            search = page.locator(config.SEARCH_INPUT_SEL)
            search.fill(query)
            try:
                page.wait_for_selector(
                    config.CHAT_HISTORY_SEL, timeout=10_000, state="attached"
                )
                return self.list_chats(config.MAX_HISTORY_ITEMS)
            finally:
                search.fill("")

    def read_chat(self, url: str) -> list[dict]:
        """Buka chat lama berdasarkan URL chat.qwen.ai/c/... dan baca
        seluruh isinya sebagai [{role, content}]."""
        if not url.startswith(config.BASE_URL + "/c/"):
            raise ValueError("URL chat harus berasal dari chat.qwen.ai.")
        with self._operation_lock:
            if not self._is_logged_in():
                raise AuthExpired("Sesi login Qwen kadaluarsa.")
            page = self.page
            previous_url = page.url
            page.goto(url, timeout=config.NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(
                    config.MSG_ITEM_SEL, timeout=15_000, state="attached"
                )
            except PWTimeoutError:
                messages = []
            else:
                messages = self._extract_messages(page)
            if previous_url != url and "chat.qwen.ai" in previous_url:
                try:
                    page.goto(
                        previous_url,
                        timeout=config.NAV_TIMEOUT_MS,
                        wait_until="domcontentloaded",
                    )
                    page.wait_for_selector(
                        config.TEXTAREA_SEL, timeout=15_000, state="visible"
                    )
                except Exception:
                    log.exception("Gagal kembali ke halaman semula setelah read_chat")
            return messages

    def open_chat(self, url: str) -> list[dict]:
        """Buka chat lama berdasarkan URL chat.qwen.ai/c/... dan JADIKAN chat
        aktif: ask() berikutnya melanjutkan chat ini (tanpa New Chat)."""
        if not url.startswith(config.BASE_URL + "/c/"):
            raise ValueError("URL chat harus berasal dari chat.qwen.ai.")
        with self._operation_lock:
            if not self._is_logged_in():
                raise AuthExpired("Sesi login Qwen kadaluarsa.")
            page = self.page
            page.goto(url, timeout=config.NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_selector(
                config.TEXTAREA_SEL, timeout=15_000, state="visible"
            )
            self.has_active_chat = True
            try:
                page.wait_for_selector(
                    config.MSG_ITEM_SEL, timeout=10_000, state="attached"
                )
                messages = self._extract_messages(page)
            except PWTimeoutError:
                messages = []
            return messages

    def set_mode(self, mode: str) -> str:
        """Ubah mode/thinking selector di area input.

        Mode terverifikasi dari DOM Qwen: auto, thinking, fast.
        """
        mode = (mode or "").strip().lower()
        if mode not in config.MODES:
            raise ValueError(
                f"Mode tidak dikenal: {mode!r}. Pilihan: {', '.join(config.MODES)}."
            )
        with self._operation_lock:
            if not self._is_logged_in():
                raise AuthExpired("Sesi login Qwen kadaluarsa.")
            page = self.page
            page.locator(config.MODE_TRIGGER_SEL).click(timeout=10_000)
            page.wait_for_selector(
                config.MODE_ITEM_SEL, timeout=5_000, state="visible"
            )
            items = page.locator(config.MODE_ITEM_SEL)
            for index in range(items.count()):
                item = items.nth(index)
                if item.inner_text().strip().lower() == mode:
                    item.click()
                    return f"Mode diatur ke {mode.capitalize()}."
            raise ValueError(
                f"Mode '{mode}' tidak ditemukan di dropdown Qwen "
                f"(opsi berubah? lihat config.MODE_*)."
            )
