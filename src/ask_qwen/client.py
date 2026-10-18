"""QwenWebClient: state chat + retry auth, delegasi ke backend."""

from concurrent.futures import ThreadPoolExecutor

from .browser import AuthExpired, PlaywrightBackend
from . import config


class QwenWebClient:
    def __init__(self, backend: PlaywrightBackend | None = None) -> None:
        self.backend = backend or PlaywrightBackend()
        self.chat_active = False  # True setelah ask() pertama / new_chat()
        self._browser_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="qwen-browser"
        )

    def _run(self, operation, *args):
        return self._browser_executor.submit(operation, *args).result()

    def close(self) -> None:
        """Close Playwright on its owning thread, then stop the executor."""
        try:
            self._run(self.backend.close)
        finally:
            self._browser_executor.shutdown(wait=True, cancel_futures=True)

    def ask(self, message: str, on_delta=None) -> str:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must not be empty")
        if len(message) > config.MAX_MESSAGE_CHARS:
            raise ValueError(
                f"message exceeds maximum length of {config.MAX_MESSAGE_CHARS} characters"
            )
        return self._run(self._ask_with_auth, message, on_delta)

    def _ask_with_auth(self, message: str, on_delta=None) -> str:
        try:
            return self._ask_once(message, on_delta)
        except AuthExpired:
            # Sesi web kadaluarsa → refresh token via API, relaunch browser
            # (refresh cookie di-seed ulang di _launch), lalu coba sekali lagi.
            self.chat_active = False
            if self._refresh_and_relaunch():
                return self._ask_once(message, on_delta)
            return (
                "Login Qwen kadaluarsa dan refresh otomatis gagal. "
                "Jalankan `ask-qwen login` lalu ulangi."
            )

    def _refresh_and_relaunch(self) -> bool:
        try:
            from .auth import refresh

            refresh()
        except Exception:
            return False
        self.backend.close()  # _ensure_context akan relaunch dengan cookie baru
        return True

    def _ask_once(self, message: str, on_delta=None) -> str:
        from . import api

        # State dari perintah API: id → buka chat itu; None → paksa chat baru;
        # tanpa key → perilaku lama (chat aktif daemon atau new_chat UI).
        # Catatan: chat kosong hasil API tidak diadopsi UI saat send — pesan
        # akan membuat chat baru di server — jadi `new` hanya menandai null.
        sid = api.load_state().get("active_chat_id", "unset")
        if sid != "unset" or not self.chat_active:
            if sid not in ("unset", None):
                self.backend.goto_chat(sid)
            else:
                self.backend.new_chat()
            self.chat_active = True
        result = self.backend.ask(message, on_delta)
        # catat chat aktif agar perintah CLI lain (read via API) tahu chat id
        self._record_active_chat()
        return result

    def read(self) -> list[dict]:
        return self._run(self._read_once)

    @staticmethod
    def _limit_transcript(messages: list[dict]) -> list[dict]:
        remaining = config.MAX_TRANSCRIPT_CHARS
        limited = []
        for message in messages:
            content = str(message.get("content", ""))
            if len(content) > remaining:
                limited.append({**message, "content": content[:remaining]})
                limited.append({
                    "role": "system",
                    "content": "Transcript truncated at the configured response limit.",
                })
                break
            limited.append(message)
            remaining -= len(content)
            if remaining <= 0:
                break
        return limited

    def _read_once(self) -> list[dict]:
        try:
            return self._limit_transcript(self.backend.read())
        except AuthExpired:
            return [{"role": "error", "content": "Login Qwen kadaluarsa. Jalankan `ask-qwen login`."}]

    def new_chat(self) -> str:
        return self._run(self._new_chat_once)

    def _new_chat_once(self) -> str:
        result = self.backend.new_chat()
        self.chat_active = True
        self._record_active_chat()
        return result

    def _record_active_chat(self) -> None:
        """Simpan chat id aktif (dari URL browser) ke state.json untuk API CLI."""
        try:
            url = self.backend.page.url
            if "/c/" in url:
                from . import api

                api.set_active_chat(url.rsplit("/c/", 1)[-1])
        except Exception:
            pass

    def list_chats(self, limit: int = 20) -> list[dict]:
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        return self._run(self._list_chats_once, min(limit, config.MAX_HISTORY_ITEMS))

    def _list_chats_once(self, limit: int) -> list[dict]:
        try:
            return self.backend.list_chats(limit)
        except AuthExpired:
            return [{"role": "error", "content": "Login Qwen kadaluarsa. Jalankan `ask-qwen login`."}]

    def read_chat(self, url: str) -> list[dict]:
        return self._run(self._read_chat_once, url)

    def _read_chat_once(self, url: str) -> list[dict]:
        try:
            return self.backend.read_chat(url)
        except AuthExpired:
            return [{"role": "error", "content": "Login Qwen kadaluarsa. Jalankan `ask-qwen login`."}]

    def open_chat(self, url: str) -> list[dict]:
        return self._run(self._open_chat_once, url)

    def _open_chat_once(self, url: str) -> list[dict]:
        try:
            messages = self.backend.open_chat(url)
        except AuthExpired:
            return [{"role": "error", "content": "Login Qwen kadaluarsa. Jalankan `ask-qwen login`."}]
        self.chat_active = True
        return messages

    def set_mode(self, mode: str) -> str:
        return self._run(self._set_mode_once, mode)

    def _set_mode_once(self, mode: str) -> str:
        try:
            return self.backend.set_mode(mode)
        except AuthExpired:
            return "Login Qwen kadaluarsa. Jalankan `ask-qwen login`."

    def stop_generation(self) -> str:
        return self._run(self.backend.stop_generation)

    def get_status(self) -> dict:
        return self._run(self.backend.get_status)

    def search_chats(self, query: str) -> list[dict]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be empty")
        return self._run(self.backend.search_chats, query)
