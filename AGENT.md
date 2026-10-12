# AGENT.md

## Project Purpose

`ask-qwen` is a Python CLI tool for chatting with Qwen Web via Playwright
browser automation. A background daemon keeps the browser alive between calls.
Primary use case: AI-to-AI consultation (second opinion / brainstorming).

## Development Rules

- Read `CONTEXT.md` before changing behavior.
- Smallest change that solves the problem.
- Preserve all CLI subcommands and the daemon protocol.
- Keep UI selectors in `src/ask_qwen/config.py`.
- Add a test for every behavior change.
- Run `.venv/bin/pytest` before reporting completion.

## Browser Constraints

- Persistent profile at `~/.ask-qwen/profile/`.
- Seed `~/.ask-qwen/cookies.json` when present.
- Never bypass the WAF with direct API calls.
- Playwright Sync API — all browser calls on one thread via ThreadPoolExecutor.
- Serialize operations via the backend RLock.
- If `SingletonLock` exists, report `ProfileLocked` — don't kill processes.

## Daemon

- Auto-starts on first CLI call.
- Unix socket at `~/.ask-qwen/daemon.sock`.
- Idle timeout (default 30 min) shuts down automatically.
- `ask-qwen quit` for manual shutdown.
- `ask-qwen login` stops the daemon first, runs headed login, daemon restarts on next call.

## Error Handling

- Distinguish `AuthExpired`, `WAFChallenge`, `ProfileLocked`.
- Return user-actionable error messages, not raw tracebacks.
- Login recovery is headed/manual with 5-minute timeout.

## Testing

```bash
.venv/bin/pytest
```

Tests cover: parser, client state, browser lifecycle, CLI dispatch, daemon protocol.
