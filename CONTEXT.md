# ask-qwen Context

## Project

`ask-qwen` is a Python CLI tool for chatting with Qwen Web (`chat.qwen.ai`)
via Playwright browser automation. No API key required — it drives a real
headless Chromium browser. A background daemon keeps the browser warm between
CLI invocations for fast response times.

Designed primarily as an AI-to-AI consultation tool: an agent that's stuck or
needs brainstorming can shell out to `ask-qwen` for a second opinion from Qwen.

## Architecture

```text
CLI (argparse)
  → daemon (Unix socket, auto-start, idle timeout)
    → QwenWebClient (chat state, auth retry, ThreadPoolExecutor)
      → PlaywrightBackend (headless Chromium, persistent profile)
        → chat.qwen.ai
```

## File Structure

```text
ask-qwen/
├── README.md
├── CONTEXT.md
├── AGENT.md
├── pyproject.toml
├── .gitignore
├── src/ask_qwen/
│   ├── __init__.py
│   ├── config.py       # selectors, timeouts, paths
│   ├── parser.py       # DOM text cleaning
│   ├── browser.py      # Playwright automation
│   ├── client.py       # chat state + auth retry
│   ├── daemon.py       # background browser daemon + client proxy
│   ├── cli.py          # argparse CLI
│   └── login.py        # headed login helper
├── tests/
│   ├── test_parser.py
│   ├── test_client_state.py
│   ├── test_browser_lifecycle.py
│   ├── test_cli.py
│   └── test_daemon.py
├── skills/
│   └── ask-qwen/
│       └── SKILL.md    # agent skill for AI-to-AI consultation
└── scripts/
    └── login.py
```

## Key Decisions

- **Daemon architecture:** browser launches once, stays alive, CLI connects via
  Unix socket. Eliminates 10s cold start on every call.
- **UI automation only:** Alibaba WAF blocks all direct API variants. Verified.
- **Persistent profile:** `~/.ask-qwen/profile/` stores browser state/cookies.
- **Token refresh handling:** login button detection with 5s grace for JS token refresh.
- **Response detection:** Stop button signal (primary) with text-stability fallback
  (only when Stop was never seen — avoids premature return during thinking).
- **Comparison UI:** Qwen sometimes shows A/B response comparison; parser extracts
  first response and strips feedback noise.

## Entry Points

```text
ask-qwen   # CLI (all subcommands including daemon auto-start)
```

## State Directory: `~/.ask-qwen/`

- `profile/` — Chromium persistent profile (cookies, storage)
- `cookies.json` — optional cookie seed file
- `daemon.sock` — Unix socket for CLI↔daemon communication
- `daemon.pid` — daemon process ID
- `ask-qwen.log` — daemon log output
