# AGENT.md

## Project Purpose

`ask-qwen` is a Python CLI for chatting with Qwen (`chat.qwen.ai`) through its
REST API. No API key, no browser, no daemon — pure stdlib (`urllib` +
`http.client`). Primary use case: AI-to-AI consultation (second opinion /
brainstorming), driven by the skill in `skills/ask-qwen/SKILL.md`.

The full reverse-engineering reference (endpoints, auth flow, headers, WAF
behavior, payload shapes, gotchas) lives in `dev-notes/qwen-api-research.md` —
**local only, gitignored, never commit or push it**. Read it before touching
`auth.py`/`api.py` internals.

## Development Rules

- Read `CONTEXT.md` and `dev-notes/qwen-api-research.md` before changing behavior.
- Smallest change that solves the problem; zero runtime dependencies (stdlib only).
- Preserve all CLI subcommands and their output contracts (stdout = data,
  stderr = spinner/errors).
- Every command is a stateless HTTP call; shared state only via
  `~/.ask-qwen/state.json` (`active_chat_id`, `thinking_mode`, `model`,
  `stop_requested`).
- Add a test for every behavior change (mock `api._request` / HTTP layer —
  no network in tests).
- Run `.venv/bin/pytest` before reporting completion.

## API Constraints (hard-won — do not regress)

- **WAF signature**: `/api/v2/chat/completions` only accepts the mobile app
  signature (`User-Agent: QwenChatAndroid/1.0`, `source: qwen_app`). Browser
  UA → `RGV587` captcha. Constant in `config.py` (`CHAT_UA`/`CHAT_SOURCE`).
- **Edge headers**: auth host rejects with 405/`Invalid request header` unless
  the full header set is sent (`Version`, `source: web`, `x-request-origin`,
  `Timezone`, `app_waf` …) — see `auth._request`.
- **Signin rate limit**: ~5 attempts per account, minutes-long lockout. Login
  once, then refresh. Never retry-loop signin.
- **Password hashing**: SHA-256 hex client-side before signin; the server
  rejects plaintext with `INVALID_CRED`.
- **Multi-turn linkage**: new message's `parentId` = `history.currentId` from
  `GET /api/v2/chats/{id}`; first message uses `parentId: ""`. Payload version
  is `"2.1"`; timestamps in **seconds**.
- **Stop**: server-side stop needs the **live** `response_id` from the
  `response.created` SSE event (`currentResponseIds` is stale — never use it).
  Cross-process stop = `stop_requested` flag in `state.json`, polled per chunk.
- **Thinking mode / model**: client-side concepts, sent per-request via
  `feature_config.thinking_mode` / `model` field. No server endpoint sets them.

## Error Handling

- `api.ApiError` / `auth.AuthError` → user-actionable message on stderr, exit 1
  (handled centrally in `cli._run`).
- Token near expiry → `auth.access_token()` auto-refreshes; 401s surface as
  "jalankan `ask-qwen login`".
- Stream silence > `CHAT_IDLE_TIMEOUT_S` → connection dropped, partial answer
  returned (never hang forever).

## State Directory: `~/.ask-qwen/`

- `auth.json` (0600) — access/refresh tokens + exps. Never commit.
- `state.json` (0600) — chat/mode/model/stop flag.
- `cookies.json`, `profile/` — legacy browser era, unused.

## Testing

```bash
.venv/bin/pytest
```

Covers: auth (hash/envelope/refresh), api (pagination, transcript walk,
stream_ask payload/SSE/stop, models), cli dispatch. 37 tests, no network.
