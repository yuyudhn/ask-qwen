# ask-qwen Context

## Project

`ask-qwen` is a Python CLI tool for chatting with Qwen (`chat.qwen.ai`)
through its REST API — no API key, no browser, no daemon. Pure stdlib
(`urllib` + `http.client`). The chat-completions WAF (RGV587) is bypassed
with the mobile app's request signature (`QwenChatAndroid/1.0` UA +
`source: qwen_app`), which the WAF allowlists (verified live 2026-10-19).

Designed primarily as an AI-to-AI consultation tool: an agent that's stuck
can shell out to `ask-qwen` for a second opinion from Qwen.

## Architecture

```text
CLI (argparse)
  → auth.py   (signin/refresh vs auth.qwen.ai, tokens in auth.json)
  → api.py    (chat endpoints vs chat.qwen.ai: stream_ask/list/transcript/
               search/models/state.json)
```

## File Structure

```text
ask-qwen/
├── README.md
├── CONTEXT.md
├── AGENT.md
├── pyproject.toml          # zero runtime dependencies
├── src/ask_qwen/
│   ├── auth.py             # signin/refresh, auth.json (0600)
│   ├── api.py              # chat REST API + SSE streaming + state.json
│   ├── cli.py              # argparse CLI
│   ├── config.py           # endpoints, app signature, limits
│   └── login.py            # API login helper
├── tests/
│   ├── test_auth.py
│   ├── test_api.py
│   └── test_cli.py
├── skills/ask-qwen/SKILL.md
└── dev-notes/              # gitignored RE research (not pushed)
```

## Key Decisions

- **Pure API:** RE of APK 2.1.0 + fe 0.3.19 proved every command can go over
  HTTP; the browser/daemon/Playwright stack was deleted 2026-10-19.
- **WAF bypass:** mobile UA/source signature; payload shape captured live from
  the web UI (`version: "2.1"`, chatId+chat_id, fid/childrenIds messages).
- **Stop lintas-proses:** `stop_requested` flag in state.json; stream_ask
  polls it per chunk and calls the stop endpoint with the live response_id.
- **Mode/model:** client-side concepts sent per-request via feature_config /
  model field; persisted in state.json.

## State Directory: `~/.ask-qwen/`

- `auth.json` — tokens (0600, never commit)
- `state.json` — active_chat_id, thinking_mode, model, stop_requested
- `profile/`, `cookies.json` — legacy browser era, no longer used
