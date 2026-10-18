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
        # Mode berikutnya: state selalu menang (CLI adalah sumber kebenaran).
        mode = api.load_state().get("thinking_mode")
        if mode and self.backend.applied_mode != mode:
            self.backend.apply_thinking_mode(mode)
        model = api.load_state().get("model")
        if model and self.backend.applied_model != model:
            self.backend.apply_model(model)
        result = self.backend.ask(message, on_delta)
        # catat chat aktif agar perintah CLI lain (read via API) tahu chat id
        self._record_active_chat()
        return result

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

                cid = url.rsplit("/c/", 1)[-1].split("/")[0].split("?")[0]
                api.set_active_chat(cid)
        except Exception:
            pass

    def set_mode(self, mode: str) -> str:
        """Validasi + persist mode; daemon menerapkannya ke store browser."""
        mode = (mode or "").strip().lower()
        if mode not in config.MODES:
            raise ValueError(
                f"Mode tidak dikenal: {mode!r}. Pilihan: {', '.join(config.MODES)}."
            )
        from . import api

        api.set_thinking_mode(mode.capitalize())
        return self._run(self._set_mode_once, mode.capitalize())

    def _set_mode_once(self, label: str) -> str:
        try:
            return self.backend.apply_thinking_mode(label)
        except AuthExpired:
            return "Login Qwen kadaluarsa. Jalankan `ask-qwen login`."

    def set_model(self, model_id: str) -> str:
        """Persist model LLM; daemon menerapkannya via URL ?model=."""
        from . import api

        api.set_model(model_id)
        return self._run(self._set_model_once, model_id)

    def _set_model_once(self, model_id: str) -> str:
        try:
            return self.backend.apply_model(model_id)
        except AuthExpired:
            return "Login Qwen kadaluarsa. Jalankan `ask-qwen login`."

    def stop_generation(self) -> str:
        # Langsung, BUKAN via executor single-thread: request hanya men-set flag
        # yang dibaca poll loop ask di thread browser — aman lintas thread dan
        # tidak mengantri di belakang ask yang sedang berjalan.
        self.backend.request_stop()
        return "Stop request diterima."

    def warmup(self) -> None:
        """Pre-launch browser (via executor, thread yang sama dengan semua
        operasi browser) agar request pertama tidak cold-start."""
        self._run(self._ensure_context)

    def _ensure_context(self) -> None:
        self.backend._ensure_context()
