# synckey

<h1 align="center">
  <img src="https://img.shields.io/github/stars/pmdnawaz1/synckey?style=flat&color=20MDFF" alt="Stars"/>
  <img src="https://img.shields.io/pypi/v/synckey?style=flat&color=20MDFF" alt="PyPI"/>
  <img src="https://img.shields.io/pypi/l/synckey?style=flat&color=20MDFF" alt="License"/>
  <br/>
  One API key. Every AI model. Zero headaches.
</h1>

<p align="center">
  <img src="https://raw.githubusercontent.com/pmdnawaz1/synckey/main/assets/demo.gif" alt="synckey demo: status, keys cooling, tier-fallback events, live dashboard" width="720"/>
</p>

**synckey** lets your app use Gemini, Groq, OpenAI, Anthropic, Cerebras, Mistral, DeepSeek, xAI, and more — through a single OpenAI-compatible API key. No code changes. No rate limit nightmares. Just one endpoint.

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8787/v1",
    api_key="sk-synckey-abc123..."      # ← one key for everything
)

# Just works. synckey routes to whichever provider has the model.
client.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "Write me a poem"}]
)
```

---

## Why synckey?

| Problem | synckey Solution |
|---------|------------------|
| Managing 10 different API keys | **One key** for all providers |
| Rate limits breaking production | **Automatic failover** to the next available provider |
| Expensive models during peak | **Tier-based fallback** → uses cheaper models when others are throttled |
| Switching providers mid-code | **Zero code changes** — just change the model name |
| Key rotation nightmares | **One command** to rotate: `synckey renew` |

---

## Quick Start

```bash
# 1. Install
pip install synckey

# 2. Guided setup (30 seconds)
synckey setup

# 3. Start the gateway (foreground; Ctrl+C to stop)
synckey serve

# …or detached, in the background:
synckey serve --background
```

Your app now uses `http://localhost:8787/v1` with the unified key from setup.

**That's it.** Your OpenAI SDK, LangChain agent, or curl command now routes to 40+ models across all providers automatically.

---

## How to Connect Your App

### Python OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8787/v1",
    api_key="sk-synckey-xxx"
)

# Use the default model configured during setup
client.chat.completions.create(
    model="default",
    messages=[{"role": "user", "content": "Hello!"}]
)

# Specify a model - synckey routes automatically
client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}]
)

# Streaming responses
stream = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Tell me a story"}],
    stream=True
)
for chunk in stream:
    print(chunk.choices[0].delta.content, end="")
```

### Node.js

```javascript
import OpenAI from 'openai';

const client = new OpenAI({
  baseURL: 'http://localhost:8787/v1',
  apiKey: 'sk-synckey-xxx'
});

const response = await client.chat.completions.create({
  model: 'gpt-4o',
  messages: [{ role: 'user', content: 'Hello!' }]
});
```

### Go

```go
package main

import (
    "github.com/sashabaranov/go-openai"
)

func main() {
    client := openai.NewClient("sk-synckey-xxx")
    client.BaseURL = "http://localhost:8787/v1"

    resp, err := client.CreateChatCompletion(
        context.Background(),
        openai.ChatCompletionRequest{
            Model: "gpt-4o",
            Messages: []openai.ChatMessage{
                {Role: "user", Content: "Hello!"},
            },
        },
    )
}
```

### curl

```bash
# Basic request
curl http://localhost:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-synckey-xxx" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'

# Streaming
curl http://localhost:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-synckey-xxx" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}], "stream": true}'

# Use default model (omit model)
curl http://localhost:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-synckey-xxx" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello"}]}'

# Force specific provider
curl http://localhost:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-synckey-xxx" \
  -H "X-Provider: groq" \
  -H "Content-Type: application/json" \
  -d '{"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": "Hello"}]}'
```

### LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8787/v1",
    api_key="sk-synckey-xxx",
    model="gpt-4o"
)

response = llm.invoke("Hello!")
```

---

## API Reference

### Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/v1/chat/completions` | POST | Yes | Chat completions (streaming supported) |
| `/v1/embeddings` | POST | Yes | Embeddings |
| `/v1/completions` | POST | Yes | Legacy completions |
| `/v1/models` | GET | Yes | List available models |
| `/v1/usage` | GET | Yes | Token usage totals |
| `/v1/events` | GET | Yes | Recent routing events |
| `/v1/requests/{id}` | GET | Yes | Poll deferred request status |
| `/healthz` | GET | No | Health check |

### Headers

| Header | Values | Description |
|--------|--------|-------------|
| `Authorization` | `Bearer sk-synckey-xxx` | **Required.** Your unified API key |
| `X-Quality-Floor` | `frontier`, `high`, `mid`, `low` | Minimum tier to use (never drop below this) |
| `X-Provider` | Provider ID (e.g., `groq`, `gemini`) | Hint to prefer this provider first |

### POST /v1/chat/completions

**Request:**
```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "temperature": 0.7,
  "max_tokens": 1000,
  "stream": false
}
```

**Response (non-streaming):**
```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "gpt-4o",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Hello! How can I help you today?"
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 20,
    "completion_tokens": 15,
    "total_tokens": 35
  }
}
```

**Response (deferred/queued - 202):**
```json
{
  "id": "defer_abc123",
  "object": "chat.deferred",
  "status": "queued",
  "result_url": "/v1/requests/defer_abc123",
  "retry_after": 28
}
```

### GET /v1/requests/{id}

Poll for deferred request completion:

```bash
curl http://localhost:8787/v1/requests/defer_abc123 \
  -H "Authorization: Bearer sk-synckey-xxx"
```

**Queued/running response (202):**
```json
{
  "id": "defer_abc123",
  "object": "chat.deferred",
  "status": "queued",
  "retry_after": 15,
  "result_url": "/v1/requests/defer_abc123"
}
```

**Done response (200):** the upstream completion is returned directly — the body is
exactly what the provider sent (a standard `chat.completion` JSON object), so your
client parses it as if it had never been queued.

**Error response (4xx/5xx):** a standard error envelope with the upstream status code:
```json
{"error": {"message": "...", "type": "deferred_error", "request_id": "defer_abc123"}}
```

Deferred requests always run **non-streaming**: the polled result is the complete
JSON response, ready to use.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                           Your Application                             │
│                   (OpenAI SDK, LangChain, curl, any)                   │
└─────────────────────────────────────┬──────────────────────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────┐
                    │       synckey Gateway           │
                    │   http://localhost:8787/v1      │
                    │                                 │
                    │  ┌─────────────────────────┐    │
                    │  │  1. Auth Middleware     │    │
                    │  │     (unified key)       │    │
                    │  └─────────────────────────┘    │
                    │               │                 │
                    │               ▼                 │
                    │  ┌─────────────────────────┐    │
                    │  │  2. Router              │    │
                    │  │  - resolve model        │    │
                    │  │  - check tier floor     │    │
                    │  │  - priority ordering    │    │
                    │  └─────────────────────────┘    │
                    │               │                 │
                    │               ▼                 │
                    │  ┌─────────────────────────┐    │
                    │  │  3. Key Pool            │    │
                    │  │  - per-key buckets      │    │
                    │  │  - RPM/TPM tracking     │    │
                    │  │  - health states        │    │
                    │  └─────────────────────────┘    │
                    │               │                 │
                    │               ▼                 │
                    │  ┌─────────────────────────┐    │
                    │  │  4. Proxy Layer         │    │
                    │  │  - forward to upstream  │    │
                    │  │  - stream passthrough   │    │
                    │  │  - log usage/cost       │    │
                    │  └─────────────────────────┘    │
                    │               │                 │
                    │               ▼                 │
                    │  ┌─────────────────────────┐    │
                    │  │  5. Deferred Queue      │    │
                    │  │  - 202 + poll           │    │
                    │  │  - auto-retry           │    │
                    │  └─────────────────────────┘    │
                    └──────────────────────┬──────────┘
                                           │
               ┌───────────────────────────┼───────────────────────────┐
               ▼                           ▼                           ▼
          ┌─────────┐                ┌─────────┐                ┌─────────┐
          │  Groq   │                │ Gemini  │                │Anthropic│
          └─────────┘                └─────────┘                └─────────┘
```

### Internal Components

| Component | Purpose | Resources |
|-----------|---------|-----------|
| **Auth Middleware** | Validates unified key (SHA-256 hash) | ~1MB RAM |
| **Router** | Resolves model → provider, enforces tier floor | ~5MB RAM |
| **Key Pool** | Tracks per-key state (LIVE/COOLING/DEAD), RPM/TPM buckets | ~10KB per key |
| **Proxy Layer** | Forwards requests, handles streaming, logs responses | Scales with concurrency |
| **Deferred Queue** | SQLite-backed queue for when all keys are cooling | ~1MB RAM + disk |

### Resource Usage

| Metric | Typical | Under Load |
|--------|---------|------------|
| **RAM** | 50-100MB | 200MB max |
| **CPU** | <1% idle | Scales with concurrent requests |
| **Disk** | SQLite DB (~1MB per 10K requests) | Configurable TTL |
| **Latency Overhead** | 2-5ms | Adds ~10ms at 100 concurrent |

synckey is designed for **minimal footprint**. The gateway is single-process async by default (uvicorn with 1 worker), which handles hundreds of concurrent requests. For CPU-bound throughput you can add workers:

```bash
synckey serve --workers 4   # 4 uvicorn worker processes
```

> **Note:** each worker keeps its own in-memory key pool, so per-key rate
> limits apply *per worker* (4 workers × 60 RPM cap ≈ 240 RPM total). Durable
> health state (cooling/dead keys) is still shared via SQLite.

---

## Smart Routing

synckey doesn't just blindly forward requests. It intelligently manages the entire lifecycle:

### Automatic Failover

```python
# You request: claude-opus-4-8
# Groq has it and is fast → routes there
# Groq hits rate limit → automatically tries Anthropic
# Anthropic succeeds → returns response, logs the fallback
```

### Tier-Based Fallback

Every model is classified into a quality tier. If your preferred model is unavailable, synckey finds alternatives at the **same tier first** — never drops you to a cheaper tier unless explicitly configured.

```
FRONTIER: claude-opus-4-8, gpt-5, gemini-2.5-pro
HIGH:     claude-sonnet-4-6, gpt-4o, gemini-1.5-pro
MID:      gpt-4o-mini, llama-3.3-70b, gemini-2.0-flash
LOW:      gemma-2b, llama-3.2-1b
```

### Deferred Requests (Queue)

When **every** key is rate-limited, synckey queues the request instead of failing:

```json
{
  "id": "defer_abc123",
  "status": "queued",
  "result_url": "/v1/requests/defer_abc123",
  "retry_after": 28
}
```

Poll the result URL — synckey runs the request automatically when a key frees up and serves the stored response on your next poll.

---

## Use Cases

### Load Balance Across Providers

```python
# All these route intelligently based on your configured priority
client.chat.completions.create(model="gpt-4o")        # Groq → Gemini → OpenAI
client.chat.completions.create(model="claude-sonnet-4-6")  # Anthropic → Groq → Gemini
client.chat.completions.create(model="llama-3.3-70b")     # Groq → Cerebras → Together
```

### Cost Optimization

```bash
# Set your default to a tier, not a specific model
synckey config set-default mid

# synckey picks the best MID-tier model that's available and affordable
# Falls back to HIGH tier only if MID is completely unavailable
```

### Zero-Downtime Deployments

```python
# When OpenAI has an outage, your app keeps working
# synckey automatically routes to the next available provider
# Your users never see an error
```

---

## Live Dashboard

```bash
synckey dash
```

```
┌──────────────────────────────────────────────────────────────┐
│  synckey dashboard                             [1]overview   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Keys (3)        Requests Today          Events              │
│  ┌──────────┐    1,247                 12 rate limits        │
│  │ ● Groq   │    Avg: 142ms            2 tier fallbacks      │
│  │ ○ Gemini │    Errors: 0.2%           0 key deaths         │
│  │ ● Cohere │                                                │
│  └──────────┘    Last Request                                │
│                  Model: gpt-4o-mini                          │
│                  Provider: Groq                              │
│                  Latency: 89ms                               │
│                                                              │
│  [1]overview  [2]keys  [3]events  [4]routing  [q]quit        │
└──────────────────────────────────────────────────────────────┘
```

---

## Supported Providers

15 providers supported out of the box:

| Category | Providers |
|----------|-----------|
| **Fast/Cheap** | Groq, Cerebras, GitHub Models, DeepSeek |
| **Frontier** | OpenAI, Anthropic, Google Gemini, xAI |
| **Local** | Ollama — plus any custom OpenAI-compatible endpoint (see `[[providers]]` in config) |
| **Aggregators** | OpenRouter, Together AI, SambaNova |
| **Specialty** | Mistral, Cohere, NVIDIA NIM |

Run `synckey providers` to see the full list with signup links.

---

## CLI Reference

```bash
# First time setup
synckey setup                      # guided interactive setup
synckey setup --force              # regenerate unified key

# Gateway
synckey serve                      # start in the foreground (Ctrl+C to stop)
synckey serve --background         # detach; logs to ~/.synckey/gateway.log
synckey serve --port 9000          # custom port
synckey serve --workers 4          # multi-process (see note in Resource Usage)
synckey stop                       # stop a --background gateway

# Key management
synckey keys                       # list all keys
synckey keys add groq --key gsk_...            # add a key non-interactively
synckey keys add groq --key "k1,k2,k3"         # bulk add (comma-separated)
synckey keys add groq --file keys.txt          # bulk import from a file
synckey keys add groq --from-env               # read from $GROQ_API_KEY
synckey keys --rm <id>            # remove a key
synckey keys --disable <id>       # temporarily disable
synckey keys --enable <id>       # re-enable a disabled key
synckey keys --limits <id> --rpm 60 --tpm 100000 --weight 2   # set caps

# Testing
synckey test                       # health check all keys
synckey test groq llama-3.3-70b-versatile 5  # test a route

# Routing
synckey routing priority              # show provider order
synckey routing priority groq gemini  # set preference
synckey routing preset reliable       # never drop tier
synckey routing preset cheap          # prefer cost over quality

# Config
synckey config set-default gemini-2.0-flash    # set default model
synckey config provider-default groq llama-3.3-70b-versatile  # per-provider
synckey config alias fast mid         # create alias

# Tiers
synckey tier set my-model frontier    # override tier
synckey tier list                    # show all overrides

# Monitoring
synckey dash                        # live dashboard
synckey status                      # quick overview
synckey usage                       # totals
synckey usage --spend              # cost breakdown
synckey usage --recent              # last 25 requests
synckey events                      # rate limits, fallbacks, deaths
synckey deferred list               # queued requests

# Key rotation
synckey renew                       # regenerate unified key

# Discovery
synckey providers                   # list supported providers
synckey models --refresh            # fetch models your keys can call
synckey version                     # print version
synckey --guide                     # interactive walkthrough
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNCKEY_HOME` | `~/.synckey/` | Config and state directory |
| `SYNCKEY_DB` | `{SYNCKEY_HOME}/synckey.db` | SQLite database path |
| Provider env vars | — | `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, etc. |

---

## Configuration

Config file: `~/.synckey/config.toml`

```toml
[gateway]
host = "127.0.0.1"
port = 8787
request_timeout = 120
max_connections = 600
max_keepalive = 300

[routing]
priority = ["groq", "gemini"]
tier_fallback = true
cross_provider_fallback = true
max_retries = 6
default_cooldown = 20
deep_cooling_threshold = 60

[tiers]
overrides = { "my-model" = "frontier" }     # manual tier assignments
prices = { "my-model" = [1.0, 3.0] }        # $/M tokens: input, output

[deferred]
enabled = true
ttl = 3600
poll = 5
max_queue = 1000
max_queue_age = 86400

# Custom OpenAI-compatible providers:
# [[providers]]
# id = "lmstudio"
# name = "LM Studio"
# base_url = "http://localhost:1234/v1"
# patterns = ["^lmstudio/"]
# auth = "bearer"
```

---

## Production Deployment

### PM2 (Recommended for all platforms)

`synckey serve` runs in the foreground by default — exactly what a process
supervisor expects. PM2 keeps synckey running 24/7, auto-restarts on crash, and
survives server reboots. (Do **not** use `--background` under PM2/systemd; that
flag is only for running without a supervisor.)

```bash
# Install PM2
npm install -g pm2

# Start synckey (runs in background, daemonized)
pm2 start examples/ecosystem.config.js

# Save current process list (persists across reboots)
pm2 save

# Generate startup script for your OS
pm2 startup

# Other useful commands
pm2 logs synckey        # view logs
pm2 restart synckey       # restart
pm2 stop synckey          # stop
pm2 monit                 # real-time dashboard
```

**ecosystem.config.js** (included in `examples/`):
```javascript
module.exports = {
  apps: [{
    name: 'synckey',
    script: 'python.exe',
    args: '-m synckey serve',
    cwd: 'C:\\path\\to\\synckey',
    interpreter: 'none',
    watch: false,
    autorestart: true,
    restart_delay: 10000,
    max_restarts: 10,
    min_uptime: 5000,
  }]
};
```

### systemd (Linux/WSL)

For servers running systemd:

```bash
# 1. Install synckey
pip install synckey

# 2. Create dedicated user (optional but recommended)
sudo useradd -r -s /bin/false synckey
sudo mkdir /var/lib/synckey /run/synckey
sudo chown synckey:synckey /var/lib/synckey /run/synckey

# 3. Install service file
sudo cp examples/synckey.service /etc/systemd/system/
sudo systemctl daemon-reload

# 4. Enable and start
sudo systemctl enable synckey
sudo systemctl start synckey
sudo systemctl status synckey
```

**synckey.service** (included in `examples/`):
```ini
[Unit]
Description=synckey unified AI gateway
Documentation=https://github.com/pmdnawaz1/synckey
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=synckey
Group=synckey
WorkingDirectory=/opt/synckey
ExecStart=/usr/bin/python3 -m synckey serve
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=synckey

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/synckey /run/synckey
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### Windows Service (NSSM)

```bash
nssm install synckey "C:\path\to\python.exe" "-m synckey serve"
nssm set synckey AppDirectory "C:\path\to\synckey"
nssm set synckey Start SERVICE_AUTO_START
nssm start synckey
```

### Auto-Restart Behavior

| Event | Action |
|-------|--------|
| Server reboot | PM2/systemd auto-starts synckey |
| Crash (OOM, segfault) | Auto-restart after 10s delay |
| All keys cooling | synckey returns 202 + poll URL (no crash) |
| Manual stop (no supervisor) | `synckey stop` (kills the `--background` process) |

### Expose on Domain

```bash
synckey serve --host 0.0.0.0 --port 8787
```

**nginx:**
```nginx
server {
    listen 443 ssl;
    server_name ai.example.com;
    ssl_certificate /etc/ssl/certs/ai.example.com.crt;
    ssl_certificate_key /etc/ssl/private/ai.example.com.key;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
```

**Caddy:**
```caddyfile
ai.example.com {
    reverse_proxy localhost:8787 {
        flush_interval -1
    }
}
```

---

## Security

- Provider secrets are **Fernet-encrypted** at rest
- Unified key stored as **SHA-256 hash** (not reversible)
- Gateway binds to `localhost` by default
- Secrets directory is `0700` permissions

---

## Troubleshooting

### 401 Unauthorized
- Verify your unified key is correct: `synckey status`
- Regenerate if needed: `synckey renew`

### Connection refused
- Ensure synckey is running: `synckey serve`
- Check if port is in use: `netstat -an | grep 8787`

### All requests returning 202 queued
- Keys may be in cooldown: `synckey status`
- Check events: `synckey events`

### Model not found (404)
- Model may not be in index: `synckey models --refresh`
- Provider may not have the model

---

## Compare

| Feature | synckey | LiteLLM | Portkey | Direct |
|---------|---------|---------|---------|--------|
| One API key | ✅ | ✅ | ✅ | ❌ |
| Auto-failover | ✅ | ✅ | ✅ | ❌ |
| Tier fallback | ✅ | ❌ | ❌ | ❌ |
| Deferred queue | ✅ | ❌ | ❌ | ❌ |
| Local gateway | ✅ | ❌ | ❌ | ✅ |
| Zero config | ✅ | ❌ | ❌ | ✅ |

synckey keeps its state in a local SQLite database (`~/.synckey/synckey.db`) — no
external database server required.

---

## License

MIT
