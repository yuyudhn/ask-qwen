"""Bersihkan teks respons mentah dari DOM chat.qwen.ai.

Struktur tipikal inner_text() dari <main>:
    <pesan user>\n
    \nThinking completed\n        (dropdown reasoning summary)
    <jawaban>\n
    \nAuto\n                     (pilihan mode)
    AI-generated content may not be accurate.\n

Qwen kadang menampilkan A/B comparison UI:
    This feedback will help us evaluate and improve Qwen's performance.
    Which response do you prefer? Select one to continue.
    Response 1\n Thinking completed\n <jawaban>\n I prefer this response
    Response 2\n Thinking completed\n <jawaban>\n I prefer this response
"""

import re

from . import config


_FEEDBACK_NOISE = (
    "This feedback will help us evaluate and improve Qwen's performance.",
    "Which response do you prefer? Select one to continue.",
    "I prefer this response",
)


def _strip_comparison_ui(text: str) -> str:
    """If Qwen's A/B comparison UI is present, extract just the first response."""
    # Detect the feedback header
    if "Which response do you prefer?" not in text:
        return text
    # Take everything before "Response 2" (keep only first response)
    m = re.search(r'^Response 2$', text, re.MULTILINE)
    if m:
        text = text[:m.start()]
    # Strip feedback noise lines
    for noise in _FEEDBACK_NOISE:
        text = text.replace(noise, "")
    # Strip "Response 1" label
    text = re.sub(r'^Response 1$', '', text, flags=re.MULTILINE)
    return text


def _strip_noise_lines(text: str) -> str:
    # Label mode di area input ikut ter-capture dalam <main> (Auto/Thinking/Fast);
    # buang semua varian, bukan hanya "Auto".
    mode_labels = {m.capitalize() for m in config.MODES} | {"Auto"}
    lines = [line.strip() for line in text.splitlines()]
    kept = [line for line in lines if line and line not in mode_labels]
    return "\n".join(kept)


def extract_answer(main_text: str, user_message: str | None = None) -> str:
    """Ambil blok jawaban asisten terakhir dari teks <main>.

    - Buang disclaimer & baris mode.
    - Buang pesan user bila diketahui (prefix blok).
    - Buang reasoning summary 'Thinking completed' dan isi dropdown-nya
      (teks antara 'Thinking completed' hingga akhir paragraf reasoning).
    """
    text = main_text
    text = _strip_comparison_ui(text)
    for noise in (
        "AI-generated content may not be accurate.",
    ):
        text = text.replace(noise, "")

    if user_message:
        # pesan user muncul sebagai blok pertama di main
        idx = text.find(user_message.strip())
        if idx != -1:
            text = text[idx + len(user_message.strip()) :]

    # reasoning summary: baris "Thinking completed" (dropdown collapsed);
    # jawaban selalu berada di bawah baris marker TERAKHIR. Cocokkan per baris
    # agar jawaban yang mengutip kalimat marker tidak terpotong.
    lines = text.splitlines()
    last_marker = max(
        (i for i, ln in enumerate(lines) if ln.strip() == "Thinking completed"),
        default=-1,
    )
    if last_marker != -1:
        text = "\n".join(lines[last_marker + 1 :])

    return _strip_noise_lines(text).strip()


def clean_message(role: str, text: str) -> str:
    """Bersihkan satu pesan (user atau assistant) mentah dari DOM.

    Assistant: buang reasoning marker 'Thinking completed' (dropdown
    collapsed: hanya label yang terbaca, isi summary tidak ikut) + disclaimer.
    """
    if role == "assistant":
        text = _strip_comparison_ui(text)
        text = text.replace("AI-generated content may not be accurate.", "")
        lines = text.splitlines()
        last_marker = max(
            (i for i, ln in enumerate(lines) if ln.strip() == "Thinking completed"),
            default=-1,
        )
        if last_marker != -1:
            text = "\n".join(lines[last_marker + 1 :])
    return _strip_noise_lines(text).strip()


def parse_messages(pairs: list[tuple[str, str]]) -> list[dict]:
    """Ubah [(role, raw_text), ...] jadi [{role, content}] yang sudah bersih.

    Pesan yang kosong setelah dibersihkan dibuang.
    """
    messages = []
    for role, raw in pairs:
        content = clean_message(role, raw)
        if content:
            messages.append({"role": role, "content": content})
    return messages
