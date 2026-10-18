# ask-qwen

CLI tool to chat with [Qwen Web](https://chat.qwen.ai) via Playwright browser automation — no API key required.

Designed as a **second-opinion tool for AI agents**: when your main AI gets stuck or needs brainstorming, it can shell out to `ask-qwen` and consult Qwen for a fresh perspective.

## How It Works

`ask-qwen` drives a real headless Chromium browser pointed at `chat.qwen.ai`. A background daemon keeps the browser alive between calls, so the first invocation takes ~10s (cold start) but subsequent ones connect instantly (~0.1s overhead). Conversations persist across calls until you explicitly start a new chat.

```text
CLI (ask-qwen)
  → daemon (Unix socket, auto-started)
    → QwenWebClient (chat state, auth retry)
      → PlaywrightBackend (headless Chromium)
        → chat.qwen.ai
```

## Install

```bash
git clone git@github.com:yuyudhn/ask-qwen.git
cd ask-qwen
pip install .
playwright install chromium
```

## First-Time Login

```bash
ask-qwen login
```

Signs in via the Qwen auth API (no browser involved) — you'll be prompted for
email and password (or pass `--email`). Tokens are stored in
`~/.ask-qwen/auth.json` (`0600`); the 30-day refresh cookie is seeded into the
browser profile automatically, so the daemon's web session self-renews. Run
this again when the refresh token expires (~monthly) or after a password
change. See `dev-notes/qwen-api-research.md` (local only, not committed) for
the reverse-engineered API details.

**Alternative:** export cookies from your browser (including httpOnly, e.g. via CDP `Network.getCookies`) to `~/.ask-qwen/cookies.json` — they'll be seeded automatically on startup.

## Usage

```bash
# Ask a question
ask-qwen ask "Explain the CAP theorem in 2 sentences"

# Interactive chat REPL
ask-qwen chat

# Continue the conversation (daemon keeps state)
ask-qwen ask "Give me a real-world example"

# Start a fresh chat
ask-qwen new

# Read the current chat transcript
ask-qwen read

# List recent chats from sidebar
ask-qwen list
ask-qwen list -n 5

# Read an old chat by URL or chat ID
ask-qwen read-chat https://chat.qwen.ai/c/...
ask-qwen read-chat <chat-id-from-list>

# Open an old chat and make it active
ask-qwen open <chat-id-from-list>

# Switch thinking mode: auto, thinking, fast
ask-qwen mode thinking

# Stop active generation
ask-qwen stop

# Check login/chat/generation status
ask-qwen status

# Search chats by text
ask-qwen search "kubernetes"

# Stop the background daemon
ask-qwen quit
```

### Interactive Mode

`ask-qwen chat` starts a REPL. Special commands inside the REPL:

- `/new` — start a new chat
- `/mode auto|thinking|fast` — switch mode
- `/read` — show transcript
- `/status` — show status
- `exit` or `Ctrl+D` — quit

When stdout is a TTY (interactive terminal), `ask` and the REPL stream the
answer line-by-line as Qwen generates it. Piped/redirected stdout always
gets one clean answer at the end, safe for agent consumption. `list` output
includes an `id` field per chat — pass it directly to `open`/`read-chat`
instead of the full URL.

## Commands

| Command | Description |
|---|---|
| `ask <message>` | Send a message to Qwen and print the answer. |
| `chat` | Interactive REPL mode. |
| `read` | Print the active chat transcript (JSON). |
| `new` | Start a new chat (reset conversation). |
| `list [-n N]` | List recent chats from the sidebar. |
| `read-chat <url\|id>` | Read a chat transcript by its `chat.qwen.ai/c/...` URL or bare chat ID. |
| `open <url\|id>` | Open an old chat (URL or chat ID) and make it active for future `ask` calls. |
| `mode <auto\|thinking\|fast>` | Set Qwen's thinking mode. |
| `stop` | Stop active generation (safe to call when idle). |
| `status` | Show login, active chat, URL, and generation state. |
| `search <query>` | Search chats by text via the sidebar search. |
| `login` | Open a headed browser for manual login. |
| `quit` | Stop the background browser daemon. |

## Background Daemon

The first CLI call automatically starts a background daemon that keeps the browser alive. This eliminates cold-start latency and token-refresh races on every call.

- **Auto-start:** any command starts the daemon if it isn't running.
- **Idle timeout:** daemon shuts down after 30 minutes of inactivity (configurable via `ASK_QWEN_IDLE_TIMEOUT` env var, in seconds).
- **Manual stop:** `ask-qwen quit` stops the daemon immediately.
- **Socket:** `~/.ask-qwen/daemon.sock` (owner-only, `0600`)
- **Logs:** `~/.ask-qwen/ask-qwen.log`

## Agentic Usage (AI-to-AI Consultation)

This tool is built for AI agents that need a second opinion. See [`skills/ask-qwen/SKILL.md`](skills/ask-qwen/SKILL.md) for the full agent skill definition, or use it directly:

```bash
# From your AI agent's tool/shell access:
ask-qwen new
ask-qwen ask "I'm debugging a race condition in a Go channel pipeline. Here's the code: ..."
ask-qwen ask "What if I add a mutex around the send? Would that deadlock?"
ask-qwen quit  # optional — daemon auto-stops after 30 min idle
```

The agent starts a new chat, asks Qwen for help, and reads the answer from stdout. Multi-turn conversations work naturally — each `ask` continues the same chat.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ASK_QWEN_IDLE_TIMEOUT` | `1800` | Daemon idle timeout in seconds. |
| `ASK_QWEN_REQUEST_TIMEOUT` | `600` | Per-request daemon response timeout in seconds. |
| `QWEN_MAX_MESSAGE_CHARS` | `100000` | Max input message length. |
| `QWEN_MAX_TRANSCRIPT_CHARS` | `200000` | Max transcript length returned by `read`. |
| `QWEN_MAX_HISTORY_ITEMS` | `100` | Max chat history items from `list`. |

## Limitations & Risks

- Automating a web chat may violate Qwen's Terms of Service — use for personal, low-volume purposes only.
- Alibaba WAF (`RGV587`) blocks direct API calls to the chat-completions endpoint (captcha challenge); that's why `ask`/`stop` still drive a real browser. Auth, chat listing, reading, and search work fine over plain HTTP.
- Sessions can expire at any time — run `ask-qwen login` to re-authenticate.
- Don't run two instances with the same profile simultaneously.
- UI selectors may change when Qwen updates their frontend — see `src/ask_qwen/config.py`.

## Development

```bash
pip install -e . --group dev
pytest
```

**Never commit** `~/.ask-qwen/cookies.json`, `profile/`, or `.venv/`.

## License

MIT
