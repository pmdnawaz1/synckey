# Using Sync-Key with OpenCode — and why requests fail with `All N attempts failed … Last: unknown`

Written 2026-11-15 after debugging OpenCode 1.18.35 against a local Sync-Key gateway. Everything marked **verified** was reproduced with a minimal request; anything else is called out as an assumption.

## TL;DR

OpenCode through Sync-Key failed for **three independent reasons**:

| # | Problem | Where | Status |
|---|---|---|---|
| A | OpenCode's default request is **~82,000 tokens**; Groq's `on_demand` tier rejects requests over **8,000 tokens per minute (TPM)** with HTTP 413 | client ↔ upstream limit | Worked around with a lean OpenCode agent (~3.5k tokens). Not a Sync-Key bug. |
| B | The **running gateway is v0.1.0** (installed in `site-packages`); on streaming requests it retries *every* non-200 across all keys and hides the error as `Last: unknown`. The repo (v0.2.0) already fixes this | gateway | **Fix exists in the repo, not deployed** — reinstall + restart (see below) |
| C | Groq rejects **`reasoning_content`** on assistant messages (HTTP 400). Clients that replay a model's reasoning (OpenCode / AI SDK) break on the *second* turn of any tool-using conversation | gateway ↔ upstream | **Unresolved.** Recommended gateway fix: strip it before forwarding |

Because of A and C, OpenCode currently completes the first tool call but fails on the follow-up turn. B is why all of this looked like one mysterious error.

---

## 1. The symptom

```
Error: All 7 attempts failed for 'openai/gpt-oss-120b'. Last: unknown
```

HTTP 502, `type: upstream_exhausted`, `providers_tried: ["groq"]`. The number is how many keys the gateway tried (7 Groq keys here). `unknown` is the initial value of `last_err` in `server.py` — it means **no upstream error text was captured**, not that the cause is unknown.

## 2. Root cause A — request size (verified)

Direct requests with a padded system prompt (non-streaming, so the real upstream error is visible):

| Request size | `gpt-oss-120b` | `gpt-oss-20b` |
|---|---|---|
| ~1k tokens | 200 | 200 |
| ~4k tokens | 200 | 200 |
| ~6k tokens | 200 | 200 |
| ~10k tokens | **413** | **413** |
| ~17k / ~30k tokens | **413** | **413** |

The 413 body, verbatim shape:

```
Request too large for model `openai/gpt-oss-120b` in organization `org_…` service tier `on_demand`
on tokens per minute (TPM): Limit 8000, Requested 73430, please reduce your message size …
```

What OpenCode actually sends, measured by pointing it at a mock OpenAI server that records request sizes:

| Part | Default agent (`build`) | Lean agent |
|---|---|---|
| System prompt | ~7,000 tokens | ~380 tokens |
| Tool schemas | **239 tools ≈ 77,000 tokens** | 6 tools ≈ 3,100 tokens |
| **Total** | **~82,000 tokens** | **~3,500 tokens** |

Almost all of the 239 tools come from the `rea` MCP server (200+ reverse-engineering tools; the largest single schema is ~3.8k tokens). OpenCode's built-in `bash` tool alone is ~1.3k tokens.

> **Consequence:** the limit applies to the *whole request*, including conversation history and file contents. With a ~3.5k-token floor there are only ~4k tokens of headroom. Long sessions will hit 413 eventually. That is the upstream free-tier limit, not a gateway bug.

## 3. Root cause B — the running gateway is older than the repo (verified)

- `pip show synckey` → **Version 0.1.0**, location `…\Python\pythoncore-3.14-64\Lib\site-packages`.
- The process listening on `127.0.0.1:8787` is `python.exe …\site-packages\synckey\_serve_temp.py` — the **installed** package, not `D:\pmdnawaz1\sync-key`.
- `pyproject.toml` in the repo says **0.2.0**.

Difference in `try_stream()` (`src/synckey/server.py`):

```python
# installed 0.1.0 — the classification result is thrown away:
if resp.status_code != 200:
    ...
    classify_failure(ctx, writer, attempt, resp, resolution, body["model"], latency)
    ...                      # falls through to "return None" => caller tries the next key

# repo 0.2.0 — respects it:
    retriable, _, _ = classify_failure(...)
    if not retriable:
        return Response(content=content, media_type="application/json", status_code=status_code)
    return None
```

Effects in 0.1.0, all observed:

| Request | Result |
|---|---|
| 413 or 400, **non-streaming** | upstream error returned unchanged (good) |
| 413 or 400, **streaming** | retried on all 7 keys (≈ 0.05 s apart), then `502 … Last: unknown` |

Retrying a 400/413 is pointless — every key rejects it — and it burns rate-limit budget on all keys. OpenCode (and most agents) always stream, so this is the path they hit.

**Fix (not yet applied — restarting changes the service other tools use):**

```powershell
cd D:\pmdnawaz1\sync-key
pip install .          # or: pip install -e .   (editable, picks up future repo changes)
synckey stop           # or kill the process on port 8787
synckey serve
```

Then re-run the checks in section 7. After the upgrade a streaming 413 should return HTTP 413 with the real upstream message instead of a 502.

## 4. Root cause C — `reasoning_content` is rejected (verified)

Minimal reproduction (3 messages: user → assistant with a tool call → tool result):

| Assistant message contains | Result |
|---|---|
| no `reasoning_content` | **200** |
| `"reasoning_content": "I should run echo."` | **400** `'messages.1' : for 'role:assistant' … property 'reasoning_content' is unsupported` |
| same, `stream: true` (gateway 0.1.0) | **502** `All 7 attempts failed … Last: unknown` |

`gpt-oss-*` models return reasoning in a `reasoning`/`reasoning_content` field, and OpenAI-compatible clients that preserve it in history send it back on the next turn. Groq's schema does not accept it on input. Sync-Key's usage DB shows the pattern: of the last 40 failed attempts, 32 were this 400 and 8 were the 413.

Observed end-to-end: asked OpenCode to create a file. The `write` tool call succeeded and `hello.txt` was created, then the follow-up model turn failed with the 502 above (and the usage DB shows seven 400s within ~0.3 s at the same moment). So **multi-step tool use through this gateway is currently broken for clients that replay reasoning**.

**Recommended fix (gateway side):** before forwarding to a provider that rejects it, delete `reasoning_content` (and `reasoning`) from every `role: assistant` message in `body["messages"]`. This is a request sanitizer, a few lines in the router before the `client.post/send`; make it a per-provider flag in `providers.py` (e.g. `strip_assistant_reasoning=True` for Groq) so providers that *do* accept it keep working. I have not implemented this; it is an assumption that a client-side OpenCode setting could not do the same (not tested).

## 5. Working OpenCode setup (verified up to the first tool call)

`C:\Users\pmdna\.config\opencode\opencode.jsonc`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "model": "local/openai/gpt-oss-120b",
  "small_model": "local/openai/gpt-oss-20b",   // session titles etc.
  "default_agent": "lean",

  "provider": {
    "local": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "synckey",
      "options": {
        "baseURL": "http://127.0.0.1:8787/v1",  // no trailing space or slash
        "apiKey": "<your sk-synckey-… key>"
      },
      "models": {
        "openai/gpt-oss-120b": { "name": "gpt-oss 120B (synckey)" },
        "openai/gpt-oss-20b":  { "name": "gpt-oss 20B (synckey)" }
      }
    }
  },

  "agent": {
    "lean": {
      "description": "Small-context coding agent for the local gateway",
      "mode": "primary",
      "model": "local/openai/gpt-oss-120b",
      "prompt": "{file:./lean-prompt.md}",
      "tools": {
        "rea_*": false, "task": false, "todowrite": false, "todoread": false,
        "webfetch": false, "websearch": false, "codesearch": false, "skill": false, "list": false
      }
    }
  }
}
```

`lean-prompt.md` (same folder) is one short paragraph telling the model to be a concise coding agent. Leave `edit`/`write` enabled — for GPT-family models OpenCode edits through those tools; turning off its patch tool removed file editing entirely in testing (the first lean attempt had only `bash/read/grep/glob`).

Pitfalls found in the original config:

- `"model": "local/llama3"` referenced a model that was not defined → OpenCode could not start a session.
- `"baseURL": "http://127.0.0.1:8787/v1 "` had a **trailing space**.
- `llama-3.3-70b-versatile` appears in `GET /v1/models` but **returns 404** ("does not exist or you do not have access"), so do not register it.
- The model reference includes the provider prefix: provider `local` + model id `openai/gpt-oss-120b` = `local/openai/gpt-oss-120b`.

## 6. Suggested gateway improvements (priority order)

1. **Deploy 0.2.0** (section 3). Removes the masking and the pointless retries.
2. **Strip `reasoning_content`/`reasoning` from assistant messages** for providers that reject it (section 4). Without this, tool-using agents die on turn two.
3. **Always put the upstream error in the exhausted message.** `last_err` should be updated from the streaming path too, and 4xx bodies should be returned as-is (0.2.0 does the latter).
4. **Pre-flight size check.** Estimate request tokens (the code already has an `estimated` value for the bucket) and, if it exceeds the model's known per-request limit, return a clear local `413 request_too_large: ~82k tokens, limit 8k for groq/on_demand` — or route to a provider/model with a bigger window — instead of burning a request on every key.
5. **Make `GET /v1/models` truthful.** It lists models the keys cannot call (`llama-3.3-70b-versatile` → 404). Filter or mark them from a probe.
6. **Record the `stream` flag for failed attempts.** Failed rows in `usage` show `stream = 0` even for streaming requests, which makes this class of bug harder to see.

## 7. Diagnostics cheat-sheet

**Which gateway is actually running?**

```powershell
$p = Get-NetTCPConnection -LocalPort 8787 -State Listen | Select -First 1
Get-CimInstance Win32_Process -Filter "ProcessId=$($p.OwningProcess)" | Select ProcessId, CommandLine | Format-List
pip show synckey          # compare Version with pyproject.toml; Location shows installed vs editable
```

**What failed, and with what status?** (read-only)

```python
import sqlite3, os, collections
c = sqlite3.connect('file:' + os.path.expanduser('~/.synckey/synckey.db') + '?mode=ro', uri=True)
rows = c.execute("select status_code, substr(error,1,120) from usage where status_code >= 400 order by id desc limit 40").fetchall()
print(collections.Counter(r[0] for r in rows))
for r in rows[:3]: print(r)
```

**Is it request size?** Send the same request non-streaming (so the real error is returned) with growing padding:

```bash
curl -s -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  --data-binary @big.json http://127.0.0.1:8787/v1/chat/completions   # 413 + "Limit 8000, Requested N"
```

**Is it `reasoning_content`?** Send a user → assistant(tool_call) → tool conversation with and without `reasoning_content` on the assistant message (section 4). 200 vs 400 confirms it.

**How big is a client's request?** Point the client at a 40-line mock server that logs `len(body)`, message count and tool count and replies `PONG`. For OpenCode, an override file passed via `OPENCODE_CONFIG=<path>` that only changes `provider.local.options.baseURL` is enough. This is how the 82k vs 3.5k numbers above were obtained.

## 8. What was and was not verified

Verified: the 413 threshold (6k ok, 10k fail) on both gpt-oss models; OpenCode request sizes (default vs lean); installed vs repo version and which process serves 8787; the 0.1.0 `try_stream` difference by reading both files; the `reasoning_content` 400 with a minimal request; streaming returning 502 for it; first-tool-call success with the lean agent.

Not verified: that the repo's 0.2.0 actually returns the real 413/400 for streaming after redeploy (read from source only); that stripping `reasoning_content` in the gateway fixes OpenCode end to end (not implemented); behaviour of other upstream providers (only Groq is configured here); whether Groq's 8k TPM limit varies by account tier (the 413 text says `on_demand`).
