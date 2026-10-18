# ask-qwen — AI-to-AI Consultation Skill

Consult Qwen Web (chat.qwen.ai) as a second-opinion AI when you're stuck, need brainstorming, want to cross-validate your reasoning, or need a fresh perspective on a problem.

## When to Use

- **You're stuck** on a problem and your own reasoning is going in circles.
- **Brainstorming** — you need alternative approaches, architectures, or framings.
- **Cross-validation** — you have a solution but want an independent check before committing.
- **Knowledge gap** — the topic is outside your strongest areas and a second model might have better training data.
- **Debugging** — you've tried multiple fixes and need a fresh pair of eyes on the code or logic.
- **Translation / localization** — Qwen has strong multilingual and CJK capabilities.
- **The user explicitly asks** you to "ask Qwen", "consult Qwen", "get a second opinion", or "brainstorm with Qwen".

## When NOT to Use

- Simple factual lookups — use web search instead.
- Tasks you can confidently handle yourself — don't add latency for no reason.
- Anything involving secrets, credentials, or private data — the query goes to chat.qwen.ai over their API.
- High-frequency polling — keep it low-volume.

## Prerequisites

`ask-qwen` must be installed and logged in:

```bash
pip install /path/to/ask-qwen  # or wherever it's installed
ask-qwen login                  # first-time only — signs in via Qwen API
```

Verify with `ask-qwen status` — it should show `"logged_in": true`.

## How to Use

### Basic: Single Question

```bash
ask-qwen ask "What are the tradeoffs between event sourcing and CRUD for a small team?"
```

The answer prints to stdout. Every call is a direct API request (~0.2s warm).

### Multi-Turn: Deep Dive

```bash
# Start a fresh conversation
ask-qwen new

# Ask the initial question
ask-qwen ask "I have a Python FastAPI app that's hitting 500ms p99 latency on a simple DB query. The query itself is 2ms in psql. What could cause this?"

# Follow up with specifics
ask-qwen ask "We're using SQLAlchemy async with asyncpg. Connection pool is default size. Could that be it?"

# Push for a concrete answer
ask-qwen ask "Give me the exact SQLAlchemy pool config I should try, with values."
```

Each `ask` continues the same conversation. Qwen remembers context from earlier messages.

### Read Back the Conversation

```bash
ask-qwen read
```

Returns the full transcript as JSON — useful for extracting structured info or logging what Qwen said.

### Switch Thinking Mode

```bash
ask-qwen mode thinking   # deep reasoning (slower, more thorough)
ask-qwen mode fast        # quick answers
ask-qwen mode auto        # let Qwen decide
```

Use `thinking` for complex reasoning tasks. Use `fast` when you just need a quick factual answer.

### Clean Up

```bash

```

## Consultation Patterns

### Pattern 1: Brainstorm Alternatives

You have an approach but want options before committing:

```bash
ask-qwen new
ask-qwen ask "I need to implement real-time notifications in a Django app. I'm considering WebSockets via Django Channels. What are 3 alternative approaches with their tradeoffs?"
```

### Pattern 2: Debug Review

You've been debugging something and want a fresh perspective:

```bash
ask-qwen new
ask-qwen ask "Here's a Go function that deadlocks intermittently. I've been staring at it for an hour. What am I missing?

func process(ch chan int, done chan struct{}) {
    for v := range ch {
        if v > 100 {
            done <- struct{}{}
            return
        }
        ch <- v * 2
    }
}"
```

### Pattern 3: Cross-Validate a Plan

You have a plan and want Qwen to poke holes in it:

```bash
ask-qwen new
ask-qwen ask "I'm planning to migrate from PostgreSQL to CockroachDB for our multi-region app. Here's my migration plan: [plan]. What could go wrong? What am I underestimating?"
```

### Pattern 4: Knowledge Supplement

The topic is specialized and you want to check your understanding:

```bash
ask-qwen new
ask-qwen ask "Explain the difference between PBKDF2, bcrypt, scrypt, and Argon2 for password hashing. Which should I choose for a new project in 2025 and why?"
```

## Integration Tips

- **Always start with `ask-qwen new`** when switching topics — leftover context from a previous conversation will confuse the response.
- **Be specific in your prompts** — include code snippets, error messages, constraints. Qwen can't see your files.
- **Use multi-turn for complex problems** — start broad, then narrow down with follow-ups.
- **Check `ask-qwen status`** before a critical consultation to confirm the session is active.
- **Parse stdout** — the answer is plain text on stdout. JSON output (from `read`, `list`, `status`) goes to stdout too.
- **Errors go to stderr** — the spinner and error messages don't pollute stdout.

## Limitations

- **Latency:** Each `ask` takes 5–30 seconds depending on Qwen's thinking time and server load.
- **No file access:** Qwen can't see your filesystem. Paste relevant code/context into the prompt.
- **Session expiry:** If you get auth errors, run `ask-qwen login` to re-authenticate.
- **Rate limits:** Free service — don't hammer it; a few queries per session is the sweet spot.
- **Qwen overload:** During high demand, Qwen may return "service experiencing high demand" errors. Wait a few seconds and retry.
