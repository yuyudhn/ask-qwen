STATE_DIR_NAME = ".ask-qwen"

import pathlib
import os

STATE_DIR = pathlib.Path.home() / STATE_DIR_NAME
PROFILE_DIR = STATE_DIR / "profile"
COOKIES_FILE = STATE_DIR / "cookies.json"
LOG_FILE = STATE_DIR / "ask-qwen.log"

BASE_URL = "https://chat.qwen.ai"
AUTH_URL = "https://auth.qwen.ai"

# Qwen Web API (hasil reverse-engineering, lihat dev-notes/qwen-api-research.md)
API_VERSION = "0.3.19"
API_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
# Header statis dari APK com.tongyi.intl 2.1.0 (class e0.g); edge Alibaba menolak
# tanpa ini (405 / "Invalid request header"). Bisa berubah di versi app baru.
APP_WAF_TOKEN = "Z9Tr56YmQpXcO2K_d_3nAbJvRqMLFW8HTNjvRguWHEowM1xY"
AUTH_FILE = STATE_DIR / "auth.json"
STATE_FILE = STATE_DIR / "state.json"  # active_chat_id, dibagi CLI <-> daemon

# Selectors (hasil verifikasi 2026-10-19; Qwen bisa mengubah kapan pun)
# Placeholder berubah dari "Ask Qwen" → "How can I help you today?" (fe 0.3.19);
# pakai class yang lebih stabil.
TEXTAREA_SEL = "textarea.message-input-textarea"
SEND_BTN_SEL = 'button[aria-label="Send"]'
MAIN_SEL = "main"
NEW_CHAT_BTN_SEL = 'button:has-text("New Chat")'
# Item chat di sidebar: <a> TANPA href (routing client-side SPA);
# URL /c/<uuid> baru ada SETELAH item diklik. Jangan pakai a[href*="/c/"].
CHAT_HISTORY_SEL = 'a[aria-label="chat-item"]'
SEARCH_INPUT_SEL = 'input[placeholder="Search Chats"]'
LOGIN_BTN_SEL = 'button:has-text("Log in")'

# Pesan di dalam <main>: klasifikasi peran via class container
MSG_ITEM_SEL = ".qwen-chat-message"
MSG_USER_CLS = "qwen-chat-message-user"
MSG_ASSISTANT_CLS = "qwen-chat-message-assistant"
MSG_USER_BODY_SEL = ".chat-user-message"
MSG_ASSISTANT_BODY_SEL = ".chat-response-message"

# Reasoning marker yang muncul di atas jawaban (strip saat parsing)
THINKING_MARKER = "Thinking completed"
STRIP_LINES = (
    THINKING_MARKER,
    "Auto",
    "AI-generated content may not be accurate.",
    # noise dari A/B comparison UI (parser._FEEDBACK_NOISE)
    "This feedback will help us evaluate and improve Qwen's performance.",
    "Which response do you prefer? Select one to continue.",
    "I prefer this response",
)

NAV_TIMEOUT_MS = 30_000
RESPONSE_STABLE_MS = 3_000  # teks respons dianggap selesai bila stabil selama ini
RESPONSE_TIMEOUT_MS = 300_000  # batas tunggu jawaban penuh
RESPONSE_START_TIMEOUT_MS = 30_000  # batas tunggu generasi dimulai
RESPONSE_FALLBACK_GRACE_S = 10.0  # tanpa sinyal Stop, tunggu ini sebelum fallback teks
POLL_INTERVAL_S = 0.2  # interval polling state tombol/teks

# Sinyal completion terverifikasi dari DOM Qwen (2026-09-13 via browser-bridge):
# saat generasi berjalan tombol Send hilang dari DOM dan muncul button[aria-label="Stop"];
# saat selesai Stop hilang dan Send kembali.
STOP_BTN_SEL = 'button[aria-label="Stop"]'

# Mode/thinking tidak lagi di-set lewat klik dropdown (frontend membaca
# localStorage 'qwen-thinking_mode' saat boot — lihat browser.apply_thinking_mode).
MODES = ("auto", "thinking", "fast")

# Guard boundaries before large values reach the browser or response.
MAX_MESSAGE_CHARS = int(os.getenv("QWEN_MAX_MESSAGE_CHARS", "100000"))
MAX_TRANSCRIPT_CHARS = int(os.getenv("QWEN_MAX_TRANSCRIPT_CHARS", "200000"))
MAX_HISTORY_ITEMS = int(os.getenv("QWEN_MAX_HISTORY_ITEMS", "100"))

# Daemon
DAEMON_SOCKET = STATE_DIR / "daemon.sock"
DAEMON_PID_FILE = STATE_DIR / "daemon.pid"
DAEMON_IDLE_TIMEOUT_S = int(os.getenv("ASK_QWEN_IDLE_TIMEOUT", "1800"))  # 30 min
# Batas tunggu respons daemon per request; browser ops boleh memakan waktu
# ber menit-menit, tapi tanpa batas CLI bisa menggantung selamanya.
DAEMON_REQUEST_TIMEOUT_S = int(os.getenv("ASK_QWEN_REQUEST_TIMEOUT", "600"))
