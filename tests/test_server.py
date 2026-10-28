import httpx
import respx
from fastapi.testclient import TestClient

from synckey.server import create_app

AUTH = {"Authorization": "Bearer sk-synckey-test"}
GROQ_CHAT = "https://api.groq.com/openai/v1/chat/completions"
OPENAI_CHAT = "https://api.openai.com/v1/chat/completions"
OPENAI_EMBEDDINGS = "https://api.openai.com/v1/embeddings"
CEREBRAS_CHAT = "https://api.cerebras.ai/v1/chat/completions"


def _add(ctx, provider="groq", n=2):
    return [ctx.db.add_key(provider, f"k{i}", ctx.box.seal(f"secret-{i}")) for i in range(n)]


def test_auth_required(ctx):
    with TestClient(create_app(ctx)) as client:
        r = client.post("/v1/chat/completions", json={"model": "groq/x"})
        assert r.status_code == 401


def test_unroutable_model_404(ctx):
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/chat/completions", headers=AUTH, json={"model": "no-such-model-anywhere"}
        )
        assert r.status_code == 404


@respx.mock
def test_success_records_usage_and_cost(ctx):
    _add(ctx)
    respx.post(GROQ_CHAT).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )
    )
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "groq/llama-3.3-70b-versatile", "messages": []},
        )
    assert r.status_code == 200
    # Usage written to DB (flush may be async; check after brief settle)
    import time; time.sleep(0.7)
    totals = ctx.db.usage_totals()
    assert totals["requests"] == 1
    assert totals["total_tokens"] == 15


@respx.mock
def test_429_eaten_failover_to_second_key(ctx):
    _add(ctx, n=2)
    route = respx.post(GROQ_CHAT)
    route.side_effect = [
        httpx.Response(429, headers={"retry-after": "30"}, json={"error": "slow down"}),
        httpx.Response(
            200,
            json={"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
        ),
    ]
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "groq/llama-3.3-70b-versatile", "messages": []},
        )
    assert r.status_code == 200
    assert route.call_count == 2


@respx.mock
def test_dead_key_after_401(ctx):
    ids = _add(ctx, n=1)
    respx.post(GROQ_CHAT).mock(return_value=httpx.Response(401, json={"error": "invalid key"}))
    with TestClient(create_app(ctx)) as client:
        client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "groq/llama-3.3-70b-versatile", "messages": []},
        )
    from synckey.state import Health
    assert ctx.states.get(ids[0]).health == Health.DEAD


@respx.mock
def test_401_marks_dead_and_event_logged(ctx):
    ids = _add(ctx, n=1)
    respx.post(GROQ_CHAT).mock(return_value=httpx.Response(401, json={"error": "bad"}))
    with TestClient(create_app(ctx)) as client:
        client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "groq/llama-3.3-70b-versatile", "messages": []},
        )
    events = ctx.db.recent_events(10, "key_dead")
    assert len(events) >= 1


@respx.mock
def test_4xx_not_retried(ctx):
    _add(ctx, n=2)
    route = respx.post(GROQ_CHAT).mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad request"}})
    )
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "groq/llama-3.3-70b-versatile", "messages": []},
        )
    assert r.status_code == 400
    assert route.call_count == 1


def test_models_endpoint(ctx):
    ctx.router._index = {"gemini-2.0-flash": {"gemini"}}
    with TestClient(create_app(ctx)) as client:
        r = client.get("/v1/models", headers=AUTH)
    assert r.status_code == 200
    data = r.json()["data"]
    assert any(m["id"] == "gemini-2.0-flash" for m in data)
    # Tier should be annotated
    entry = next(m for m in data if m["id"] == "gemini-2.0-flash")
    assert entry["synckey_tier"] == "mid"


def test_quality_floor_header_parsed(ctx):
    _add(ctx, n=1)
    ctx.router._index = {"claude-opus-4-8": {"groq"}}
    ctx.router._rebuild_tier_index()
    # Just verify the server doesn't crash when the header is present.
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/chat/completions",
            headers={**AUTH, "X-Quality-Floor": "high"},
            json={"model": "claude-opus-4-8", "messages": []},
        )
    # No real upstream; key is tried, connection refused -> 502. Should not 500.
    assert r.status_code in (200, 401, 404, 502, 503)


@respx.mock
def test_provider_field_stripped_from_upstream_body(ctx):
    import json as _json

    _add(ctx, n=1)
    route = respx.post(GROQ_CHAT).mock(
        return_value=httpx.Response(200, json={"choices": [], "usage": {}})
    )
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "groq/llama-3.3-70b-versatile", "provider": "groq", "messages": []},
        )
    assert r.status_code == 200
    sent = _json.loads(route.calls.last.request.content)
    assert "provider" not in sent  # synckey-only hint must not leak upstream


@respx.mock
def test_default_model_used_when_model_omitted(ctx):
    _add(ctx, n=1)
    ctx.prefs.set_global_default("llama-3.3-70b-versatile")  # matches groq pattern
    ctx.router.reload_prefs()
    route = respx.post(GROQ_CHAT).mock(
        return_value=httpx.Response(200, json={"choices": [], "usage": {}})
    )
    with TestClient(create_app(ctx)) as client:
        r = client.post("/v1/chat/completions", headers=AUTH, json={"messages": []})
    assert r.status_code == 200
    assert route.call_count == 1


def test_no_model_no_default_returns_400(ctx):
    with TestClient(create_app(ctx)) as client:
        r = client.post("/v1/chat/completions", headers=AUTH, json={"messages": []})
    assert r.status_code == 400


def test_healthz_includes_key_counts(ctx):
    _add(ctx, n=2)
    ctx.pool.on_rate_limit(1, retry_after=60)
    ctx.pool.on_dead(2, "test")
    with TestClient(create_app(ctx)) as client:
        r = client.get("/healthz")
    body = r.json()
    assert "keys" in body


def test_healthz_counts_fresh_keys_as_live(ctx):
    a, b = _add(ctx, n=2)
    with TestClient(create_app(ctx)) as client:
        assert client.get("/healthz").json()["keys"] == {"live": 2, "cooling": 0, "dead": 0}
    ctx.pool.on_rate_limit(a, retry_after=60)
    ctx.pool.on_dead(b, "test")
    with TestClient(create_app(ctx)) as client:
        assert client.get("/healthz").json()["keys"] == {"live": 0, "cooling": 1, "dead": 1}


def test_non_object_body_returns_400(ctx):
    with TestClient(create_app(ctx)) as client:
        r = client.post("/v1/chat/completions", headers=AUTH, json=[])
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"


@respx.mock
def test_stream_4xx_returns_upstream_without_retry(ctx):
    _add(ctx, n=2)
    route = respx.post(GROQ_CHAT).mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad request"}})
    )
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "groq/llama-3.3-70b-versatile", "messages": [], "stream": True},
        )
    assert r.status_code == 400
    assert route.call_count == 1
    assert r.json()["error"]["message"] == "bad request"


def test_cross_provider_fallback_off_stays_on_first_provider(ctx):
    from synckey.server import plan_attempts

    ctx.db.add_key("groq", "g", ctx.box.seal("s"))
    ctx.db.add_key("cerebras", "c", ctx.box.seal("s"))
    ctx.router._index = {"llama-3.3-70b": {"groq", "cerebras"}}
    ctx.router._rebuild_tier_index()
    res = ctx.router.resolve("llama-3.3-70b")
    ctx.settings.cross_provider_fallback = False
    attempts = plan_attempts(ctx, res, 6)
    assert attempts and {a.provider_id for a in attempts} == {res.providers[0]}
    ctx.settings.cross_provider_fallback = True
    attempts = plan_attempts(ctx, res, 6)
    assert {a.provider_id for a in attempts} == {"groq", "cerebras"}


@respx.mock
def test_tpm_limit_enforced(ctx):
    ctx.db.add_key("groq", "k", ctx.box.seal("s"), tpm_limit=5)
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "groq/llama-3.3-70b-versatile", "messages": []},
        )
    assert r.status_code == 202
    assert ctx.db.count_deferred("queued") == 1


@respx.mock
def test_tpm_tokens_consumed_on_success(ctx):
    kid = ctx.db.add_key("groq", "k", ctx.box.seal("s"), tpm_limit=100000)
    respx.post(GROQ_CHAT).mock(
        return_value=httpx.Response(200, json={"choices": [], "usage": {}})
    )
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "groq/llama-3.3-70b-versatile", "messages": []},
        )
    assert r.status_code == 200
    assert ctx.pool._buckets.get(kid)._tok_tokens < 100000


@respx.mock
def test_embeddings_endpoint_passthrough(ctx):
    _add(ctx, provider="openai", n=1)
    route = respx.post(OPENAI_EMBEDDINGS).mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2]}], "usage": {"prompt_tokens": 2, "total_tokens": 2}},
        )
    )
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/embeddings",
            headers=AUTH,
            json={"model": "text-embedding-3-small", "input": "hi"},
        )
    assert r.status_code == 200
    assert route.call_count == 1
    assert r.json()["data"][0]["embedding"] == [0.1, 0.2]


@respx.mock
def test_completions_endpoint_passthrough(ctx):
    _add(ctx, provider="groq", n=1)
    route = respx.post("https://api.groq.com/openai/v1/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"text": "yo"}], "usage": {}})
    )
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/completions",
            headers=AUTH,
            json={"model": "groq/llama-3.3-70b-versatile", "prompt": "say yo"},
        )
    assert r.status_code == 200
    assert route.call_count == 1


@respx.mock
def test_usage_endpoint_reports_totals(ctx):
    _add(ctx, n=1)
    respx.post(GROQ_CHAT).mock(
        return_value=httpx.Response(
            200,
            json={"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}},
        )
    )
    with TestClient(create_app(ctx)) as client:
        client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "groq/llama-3.3-70b-versatile", "messages": []},
        )
        import time; time.sleep(0.7)  # usage writer flushes every 0.5s
        r = client.get("/v1/usage", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["requests"] == 1
    assert body["total_tokens"] == 6
    assert body["prompt_tokens"] == 4


@respx.mock
def test_events_endpoint_reports_rate_limits(ctx):
    ids = _add(ctx, n=1)
    respx.post(GROQ_CHAT).mock(
        return_value=httpx.Response(429, headers={"retry-after": "10"}, json={"error": "slow"})
    )
    with TestClient(create_app(ctx)) as client:
        client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "groq/llama-3.3-70b-versatile", "messages": []},
        )
        r = client.get("/v1/events", headers=AUTH)
    assert r.status_code == 200
    events = r.json()["events"]
    assert any(e["type"] == "rate_limited" and e["key_id"] == ids[0] for e in events)


@respx.mock
def test_x_provider_header_reorders_candidates(ctx):
    ctx.db.add_key("groq", "g", ctx.box.seal("s"))
    ctx.db.add_key("cerebras", "c", ctx.box.seal("s"))
    ctx.router._index = {"llama-3.3-70b": {"groq", "cerebras"}}
    ctx.router._rebuild_tier_index()
    ctx.settings.provider_priority = ["cerebras", "groq"]
    groq_route = respx.post(GROQ_CHAT).mock(
        return_value=httpx.Response(200, json={"choices": [], "usage": {}})
    )
    cerebras_route = respx.post(CEREBRAS_CHAT).mock(
        return_value=httpx.Response(200, json={"choices": [], "usage": {}})
    )
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/chat/completions",
            headers={**AUTH, "X-Provider": "groq"},
            json={"model": "llama-3.3-70b", "messages": []},
        )
    assert r.status_code == 200
    assert groq_route.call_count == 1
    assert cerebras_route.call_count == 0


@respx.mock
def test_stream_success_passthrough_and_usage(ctx):
    _add(ctx, n=1)
    sse = (
        'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
        'data: {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post(GROQ_CHAT).mock(
        return_value=httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})
    )
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "groq/llama-3.3-70b-versatile", "messages": [], "stream": True},
        )
        body = r.read()
    assert r.status_code == 200
    assert b'data: {"choices": [{"delta": {"content": "Hello"}}]}' in body
    assert b"data: [DONE]" in body
    import time; time.sleep(0.7)
    assert ctx.db.usage_totals()["total_tokens"] == 8


@respx.mock
def test_tier_fallback_on_deep_cooling(ctx):
    """Primary model's keys all deep-cooling -> same-tier alternative is used."""
    anthropic_id = ctx.db.add_key("anthropic", "a", ctx.box.seal("s"))
    ctx.db.add_key("openai", "o", ctx.box.seal("s"))
    ctx.router._index = {"claude-opus-4-8": {"anthropic"}, "gpt-5": {"openai"}}
    ctx.router._rebuild_tier_index()
    # Deep-cool the anthropic key (beyond the 60s threshold).
    ctx.pool.on_rate_limit(anthropic_id, retry_after=120)

    openai_route = respx.post(OPENAI_CHAT).mock(
        return_value=httpx.Response(200, json={"choices": [], "usage": {}})
    )
    with TestClient(create_app(ctx)) as client:
        r = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "claude-opus-4-8", "messages": []},
        )
    assert r.status_code == 200
    assert openai_route.call_count == 1
    import json as _json
    assert _json.loads(openai_route.calls.last.request.content)["model"] == "gpt-5"
    assert any(e["type"] == "tier_fallback" for e in ctx.db.recent_events(10))
