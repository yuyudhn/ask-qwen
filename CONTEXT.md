# ask-qwen Context

## Project

`ask-qwen` is a Python CLI tool for chatting with Qwen (`chat.qwen.ai`)
through its **REST API** — no API key, no browser, no daemon, zero runtime
dependencies (stdlib `urllib` + `http.client` only).

Designed primarily as an AI-to-AI consultation tool: an agent that's stuck or
needs brainstorming can shell out to `ask-qwen` for a second opinion from Qwen.
Agent-facing usage contract: `skills/ask-qwen/SKILL.md`.

## How it works

Every command is a stateless HTTP call against two hosts:

- `auth.qwen.ai` — signin / refresh (`auth.py`), issues 15-min access +
  30-day refresh JWTs (+ httpOnly refresh cookie).
- `chat.qwen.ai` — everything else (`api.py`): chat streaming, list, read,
  search, models.

```text
CLI (argparse, cli.py)
  → auth.py   (signin/refresh, Bearer tokens, ~/.ask-qwen/auth.json)
  → api.py    (chat REST + SSE streaming, shared state ~/.ask-qwen/state.json)
```

The WAF (Alibaba Baxia/x5sec) captcha-guards `/api/v2/chat/completions` for
browser-shaped traffic, but **allowlists the mobile app's request signature**
(`QwenChatAndroid/1.0` UA + `source: qwen_app`) — so `ask` streams directly
over HTTP. Full reference: `dev-notes/qwen-api-research.md` (gitignored).

## File Structure

```text
ask-qwen/
├── README.md               # user documentation
├── CONTEXT.md              # this file
├── AGENT.md                # development rules + API constraints
├── pyproject.toml          # zero runtime deps; dev: pytest
├── src/ask_qwen/
│   ├── cli.py              # argparse entry, all subcommands, spinner
│   ├── auth.py             # signin/refresh vs auth.qwen.ai; auth.json (0600)
│   ├── api.py              # chat endpoints: stream_ask, list, transcript,
│   │                       #   search, models, state.json helpers
│   ├── config.py           # hosts, app signature, limits (constants)
│   └── login.py            # `python -m`-style login helper (API, no browser)
├── tests/
│   ├── test_auth.py        # hashing, envelope, refresh, session probe
│   ├── test_api.py         # pagination, transcript walk, stream_ask (fake HTTP)
│   └── test_cli.py         # arg parsing + dispatch for every command
├── skills/ask-qwen/SKILL.md# agent skill definition
└── dev-notes/              # gitignored RE research — never push
```

## Commands → implementation map

| Command | Transport | Key function |
|---|---|---|
| `login` | `POST auth.qwen.ai/auths/signin` | `auth.signin` |
| `ask [--json]` | `POST chat/completions` (SSE) | `api.stream_ask` |
| `stop` | flag → `POST completions/stop` | `api.request_stop` / in-stream |
| `read` / `read-chat` | `GET chats/{id}` | `api.chat_transcript` |
| `list` / `search` | `GET chats/?page=` / `chats/search` | `api.list_chats` / `search_chats` |
| `open` | `GET chats/{id}` (validate) + state | `api.chat_exists` + `set_active_chat` |
| `new` | state marker `active_chat_id=null` | `api.set_active_chat(None)` |
| `mode` / `model` | state → `feature_config` on next ask | `api.set_thinking_mode` / `set_model` |
| `status` | `users/status` + `chats/{id}` done flag | `auth.session_ok` + `api.is_generating` |

## Key Decisions

- **Pure API** (2026-10-19): reversing APK `com.tongyi.intl` 2.1.0 (jadx via
  REA) + web bundle 0.3.19 proved every command can go over HTTP. The previous
  Playwright + daemon stack was deleted entirely. WAF bypass uses the mobile
  signature the WAF allowlists (verified live: same request, browser UA →
  RGV587, app UA → full answer).
- **Multi-turn**: parent linkage from `history.currentId`; payload shape
  captured live from the web UI (`version "2.1"`, `chatId`+`chat_id`,
  `fid`/`childrenIds` message nodes, `feature_config`, seconds timestamps).
- **Stop**: server-side stop works with the live `response_id` from the
  `response.created` SSE event. Cross-process: `stop_requested` in
  `state.json`, polled per chunk by the streaming loop.
- **State**: `~/.ask-qwen/state.json` is the single source of truth shared by
  all commands (each invocation is a fresh process).
- **Limits are constants** in `config.py` (no env vars): message 100k chars,
  history 100 items, stream idle 120s.

## State Directory: `~/.ask-qwen/`

- `auth.json` (0600) — tokens; never commit.
- `state.json` (0600) — `active_chat_id`, `thinking_mode`, `model`,
  `stop_requested`.
- `cookies.json`, `profile/` — legacy browser era, unused.

## Gotchas (see dev-notes for the full list)

- Empty API-created chats are **not adopted by the web UI** on send (orphan)
  — irrelevant for the CLI now, but don't add a web-UI integration assuming it.
- `/models/` envelope is double-wrapped (`data.data`); signin needs the full
  edge header set or you get misleading 405/`Invalid request header`.
- Assistant text lives in `content_list` answer phases (`content` is empty);
  thinking phases are skipped like the old DOM parser did.
- `currentResponseIds` is stale after completion — `done` flag is the
  reliable in-flight signal.

## Entry Points

```text
ask-qwen   # console script → ask_qwen.cli:main
```

## Development

```bash
pip install -e . --group dev
pytest          # 37 tests, no network
```
