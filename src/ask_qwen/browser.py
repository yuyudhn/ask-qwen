"""PlaywrightBackend: automasi UI chat.qwen.ai via persistent profile."""

import json
import logging
import os
import threading

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from . import config

log = logging.getLogger(__name__)



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
        self.applied_mode: str | None = None  # thinking mode terakhir diterapkan
        self.applied_model: str | None = None  # model LLM terakhir diterapkan
        # Permintaan stop lintas-thread: RPC handler men-set; poll loop ask
        # (thread browser) yang mengklik Stop dan mengonsumsi. Semantik: batalkan
        # generasi yang sedang/sedang-akan berjalan; natural completion membersihkan.
        self._stop_pending = False
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
                if self._stop_pending and stop_visible:
                    # stop dari RPC (flag lintas-thread): klik Stop dari thread
                    # browser, loop berikutnya melihat Stop hilang -> return.
                    try:
                        stop.click()
                    except Exception:
                        log.exception("Gagal klik Stop atas request_stop")
                    self._stop_pending = False
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
            # Stop yang tak terkonsumsi (mis. tiba saat idle lalu tak ada
            # generasi) dibersihkan saat ask berakhir natural.
            self._stop_pending = False

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

    def goto_chat(self, chat_id: str) -> str:
        """Buka chat tertentu via URL (sinkronisasi state API → browser).

        Menggantikan klik sidebar — id didapat dari API, navigasi langsung.
        """
        with self._operation_lock:
            url = f"{config.BASE_URL}/c/{chat_id}"
            self.page.goto(url, timeout=config.NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            self.has_active_chat = True
            return f"Chat dibuka: {url}"

    def request_stop(self) -> None:
        """Minta generasi yang sedang/akan berjalan berhenti (aman lintas thread).

        Hanya men-set flag; poll loop di thread browser yang mengklik Stop.
        Dipakai oleh RPC daemon — tidak boleh lewat executor (akan antri di
        belakang ask yang sedang berjalan).
        """
        self._stop_pending = True

    def apply_model(self, model_id: str) -> str:
        """Pilih model LLM via URL ?model= + reload.

        Store model frontend tidak di-persist; boot membaca query ?model=
        (chat.models server-side hanya meng-override bila terisi). Setelah
        reload, store ter-init dengan model yang diminta dan dipakai request
        completions berikutnya.
        """
        with self._operation_lock:
            if self.applied_model == model_id:
                return f"Model sudah {model_id}."
            page = self.page
            base = page.url.split("?")[0] if "/c/" in (page.url or "") else config.BASE_URL
            page.goto(f"{base}?model={model_id}", timeout=config.NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_selector(
                config.TEXTAREA_SEL, timeout=config.NAV_TIMEOUT_MS, state="visible"
            )
            self.applied_model = model_id
            return f"Model diatur ke {model_id}."

    def apply_thinking_mode(self, label: str) -> str:
        """Terapkan thinking mode ke store frontend via localStorage + reload.

        Mode dikirim per-request oleh UI (feature_config.thinking_mode) dan
        tidak ada endpoint server-nya; store membaca 'qwen-thinking_mode' saat
        boot. Reload URL aktif agar store ter-init ulang dengan mode baru.
        """
        with self._operation_lock:
            if self.applied_mode == label:
                return f"Mode sudah {label}."
            page = self.page
            page.evaluate(
                "m => localStorage.setItem('qwen-thinking_mode', m)", label
            )
            url = page.url if "/c/" in (page.url or "") else config.BASE_URL
            page.goto(url, timeout=config.NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            # Tunggu app benar-benar siap (textarea = boot selesai), bukan sleep
            # tetap — SPA boot bisa lebih lambat dari 1.5s dan race dengan ask.
            page.wait_for_selector(
                config.TEXTAREA_SEL, timeout=config.NAV_TIMEOUT_MS, state="visible"
            )
            self.applied_mode = label
            return f"Mode diatur ke {label}."
