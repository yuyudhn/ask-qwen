STATE_DIR_NAME = ".ask-qwen"

import pathlib

STATE_DIR = pathlib.Path.home() / STATE_DIR_NAME


BASE_URL = "https://chat.qwen.ai"
AUTH_URL = "https://auth.qwen.ai"

# Qwen Web API (hasil reverse-engineering, lihat dev-notes/qwen-api-research.md)
API_VERSION = "0.3.19"
API_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
# Signature aplikasi mobile (APK com.tongyi.intl 2.1.0): WAF meng-allowlist
# UA/source ini di /api/v2/chat/completions — request ber-UA browser kena
# RGV587 (verified live 2026-10-19).
CHAT_UA = "QwenChatAndroid/1.0"
CHAT_SOURCE = "qwen_app"
# Header statis untuk host auth (edge Alibaba menolak tanpa ini).
APP_WAF_TOKEN = "Z9Tr56YmQpXcO2K_d_3nAbJvRqMLFW8HTNjvRguWHEowM1xY"
AUTH_FILE = STATE_DIR / "auth.json"
STATE_FILE = STATE_DIR / "state.json"  # active_chat_id, thinking_mode, model, stop_requested

# Guard boundaries before large values reach the API.
MAX_MESSAGE_CHARS = 100_000
MAX_HISTORY_ITEMS = 100

# Mode/thinking dikirim per-request via feature_config; label disimpan di state.
MODES = ("auto", "thinking", "fast")

# Batas streaming chat: diam total selama ini -> anggap hang dan putus.
CHAT_IDLE_TIMEOUT_S = 120
