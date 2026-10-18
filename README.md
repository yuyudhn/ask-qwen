# ask-qwen

CLI tool to chat with Qwen (`chat.qwen.ai`) through its **REST API** — no API key, no browser. Pure Python stdlib (`urllib`/`http.client`).

Designed as a **second-opinion tool for AI agents**: when your main AI gets stuck or needs brainstorming, it can shell out to `ask-qwen` and consult Qwen for a fresh perspective.

## How It Works

Every command calls the Qwen API directly with Bearer tokens. The chat-completions endpoint's WAF (RGV587) is bypassed using the mobile app's request signature (`QwenChatAndroid/1.0` UA + `source: qwen_app` header — the WAF allowlists it). See `dev-notes/qwen-api-research.md` (local only, gitignored) for the full reverse-engineered reference: endpoints, auth flow, headers, payload shapes.

```text
CLI (argparse) → auth.py (signin/refresh) + api.py (chat endpoints) → chat.qwen.ai / auth.qwen.ai
```

## Install

```bash
# from PyPI-style git URL
uv tool install git+https://github.com/yuyudhn/ask-qwen.git

# or from a local clone
git clone git@github.com:yuyudhn/ask-qwen.git
cd ask-qwen
uv tool install .
```

Update later with `uv tool upgrade ask-qwen`.

No browser, no Playwright, no daemon — the tool is pure stdlib, so the
installed footprint is tiny.

## First-Time Login

```bash
ask-qwen login               # prompts email + password (--email to skip the email prompt)
```

Signs in via `auth.qwen.ai`. The password is SHA-256 hashed client-side before it leaves the machine. Tokens are stored in `~/.ask-qwen/auth.json` (`0600`); the 30-day refresh token renews automatically. Re-run when it expires (~monthly) or after a password change.

## Usage

```bash
ask-qwen ask "Explain the CAP theorem in 2 sentences"
ask-qwen ask --json "Explain the CAP theorem"   # machine-readable: answer, chat_id, usage, model
ask-qwen ask -v "..."                           # TTY: stream answer live; show think/code phases on stderr
ask-qwen ask "Give me a real-world example"     # continues the same chat

ask-qwen new                                        # next ask starts a fresh chat
ask-qwen read                                       # transcript of the active chat (JSON)
ask-qwen list [-n 5]                                # recent chats (id, title, timestamps)
ask-qwen read-chat <url|id>                         # any chat's transcript
ask-qwen open <url|id>                              # make an old chat active
ask-qwen search "kubernetes"                        # search chats

ask-qwen mode thinking                              # thinking mode: auto | thinking | fast
ask-qwen model                                      # list models (* = active)
ask-qwen model qwen3.6-plus                         # set model for future asks

ask-qwen stop                                       # stop a generation in flight (from another shell)
ask-qwen status                                     # login/generation/active-chat summary
```

Chat state (`active_chat_id`, `thinking_mode`, `model`) lives in `~/.ask-qwen/state.json`; every command is a stateless HTTP call (~0.2s warm).

## Agentic Usage (AI-to-AI Consultation)

```bash
ask-qwen new
ask-qwen ask "I'm debugging a race condition in a Go channel pipeline. Here's the code: ..."
ask-qwen ask "What if I add a mutex around the send? Would that deadlock?"
```

## Commands

| Command | Description |
|---|---|
| `ask <message>` | Send a message and print the answer. |
| `read` | Print the active chat transcript (JSON). |
| `new` | Start a new chat (reset conversation). |
| `list [-n N]` | List recent chats. |
| `read-chat <url\|id>` | Read a chat transcript by URL or chat ID. |
| `open <url\|id>` | Open an old chat and make it active. |
| `mode <auto\|thinking\|fast>` | Set thinking mode. |
| `model [id]` | List models, or set the active model. |
| `stop` | Stop active generation (works from another shell). |
| `status` | Show login, active chat, and generation state. |
| `search <query>` | Search chats by text. |
| `login` | Sign in via Qwen API. |

## Limitations & Risks

- Automating a web chat may violate Qwen's Terms of Service — use for personal, low-volume purposes only.
- The `app_waf` edge header and the mobile request signature are pinned from app v2.1.0; if requests start failing at the edge/WAF, re-extract from a newer APK (see dev-notes).
- Endpoint and payload shapes can drift when Qwen ships new frontend/app versions — the research notes show how to re-capture them.

## Development

```bash
git clone git@github.com:yuyudhn/ask-qwen.git
cd ask-qwen
uv venv && source .venv/bin/activate
uv pip install -e . --group dev
pytest
```

**Never commit** `~/.ask-qwen/auth.json`, `state.json`, or `dev-notes/`.

## License

MIT
