import wave
from io import BytesIO

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from synckey.context import merged_providers
from synckey.server import create_app

AUTH = {"Authorization": "Bearer sk-synckey-test"}
GROQ_STT = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENAI_STT = "https://api.openai.com/v1/audio/transcriptions"
MISTRAL_STT = "https://api.mistral.ai/v1/audio/transcriptions"
OPENROUTER_STT = "https://openrouter.ai/api/v1/audio/transcriptions"
AUDIO = b"RIFF\x00\x01fake-audio-bytes\xff\xfe"


def _add(ctx, n=2):
    return [ctx.db.add_key("groq", f"k{i}", ctx.box.seal(f"secret-{i}")) for i in range(n)]


def _post(client, headers=AUTH, **data):
    return client.post(
        "/v1/audio/transcriptions",
        headers=headers,
        data={"model": "whisper-large-v3", "language": "en", **data},
        files={"file": ("a.wav", AUDIO, "audio/wav")},
    )


def test_audio_needs_the_unified_key(ctx):
    with TestClient(create_app(ctx)) as client:
        assert _post(client, headers={}).status_code == 401


@respx.mock
def test_audio_is_passed_through_unchanged(ctx):
    _add(ctx)
    route = respx.post(GROQ_STT).mock(return_value=httpx.Response(200, text="hello"))
    with TestClient(create_app(ctx)) as client:
        request = client.build_request(
            "POST",
            "/v1/audio/transcriptions",
            headers=AUTH,
            data={"model": "whisper-large-v3", "prompt": ""},
            files={"file": ("a.wav", AUDIO, "audio/wav")},
        )
        original = request.read()
        r = client.send(request)
    assert r.status_code == 200 and r.text == "hello"
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    sent = route.calls[0].request
    assert sent.content == original
    assert sent.headers["content-type"] == request.headers["content-type"]
    assert sent.headers["authorization"].startswith("Bearer secret-")


@respx.mock
def test_a_rate_limited_key_fails_over(ctx):
    _add(ctx)
    route = respx.post(GROQ_STT).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "30"}, json={"error": "slow down"}),
            httpx.Response(200, json={"text": "second key"}),
        ]
    )
    with TestClient(create_app(ctx)) as client:
        r = _post(client)
    assert r.json() == {"text": "second key"} and route.call_count == 2


@respx.mock
def test_a_bad_request_is_returned_as_is(ctx):
    _add(ctx)
    route = respx.post(GROQ_STT).mock(return_value=httpx.Response(400, json={"error": "bad file"}))
    with TestClient(create_app(ctx)) as client:
        r = _post(client)
    assert r.status_code == 400 and route.call_count == 1


def test_no_keys_and_wrong_body(ctx):
    with TestClient(create_app(ctx)) as client:
        assert _post(client).status_code == 503
        r = client.post("/v1/audio/transcriptions", headers=AUTH, json={"model": "whisper-large-v3"})
    assert r.status_code == 400


@pytest.mark.parametrize("files", [
    [("file", ("a.wav", b'Content-Disposition: form-data; name="model"\r\nfake\r\n'))],
    [("model", (None, "whisper-large-v3"))],
    [("model", (None, "one")), ("model", (None, "two")), ("file", ("a.wav", AUDIO))],
])
def test_invalid_parts_are_rejected(ctx, files):
    with TestClient(create_app(ctx)) as client:
        response = client.post("/v1/audio/transcriptions", headers=AUTH, files=files)
    assert response.status_code == 400


async def test_chunked_upload_stops_at_limit(ctx, monkeypatch):
    monkeypatch.setattr("synckey.audio.MAX_BYTES", 8)

    async def chunks():
        yield b"12345"
        yield b"67890"
        pytest.fail("Upload continued after reaching the size limit")

    transport = httpx.ASGITransport(app=create_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/audio/transcriptions",
            headers={**AUTH, "Content-Type": "multipart/form-data; boundary=audio"},
            content=chunks(),
        )
    assert response.status_code == 413


@respx.mock
def test_transcription_uses_capability_metadata_over_conflicting_model_index(ctx):
    _add(ctx, n=1)
    ctx.db.add_key("openai", "other", ctx.box.seal("other-secret"))
    ctx.router.merge_provider_models("openai", ["whisper-large-v3"])
    route = respx.post(GROQ_STT).mock(return_value=httpx.Response(200, json={"text": "hello"}))
    with TestClient(create_app(ctx)) as client:
        response = _post(client)
    assert response.status_code == 200 and route.call_count == 1


@pytest.mark.parametrize(("provider", "model", "url"), [
    ("openai", "gpt-transcribe", OPENAI_STT),
    ("mistral", "voxtral-mini-transcribe-3", MISTRAL_STT),
])
@respx.mock
def test_transcription_routes_to_builtin_provider_by_model(ctx, provider, model, url):
    ctx.db.add_key(provider, "audio", ctx.box.seal("provider-secret"))
    route = respx.post(url).mock(return_value=httpx.Response(200, json={"text": "hello"}))
    with TestClient(create_app(ctx)) as client:
        response = _post(client, model=model)
    assert response.json() == {"text": "hello"}
    assert route.calls[0].request.headers["authorization"] == "Bearer provider-secret"
    assert ctx.db.recent_usage()[0]["provider"] == provider


@respx.mock
def test_explicit_provider_prefix_only_rewrites_model_part(ctx):
    ctx.db.add_key("openai", "audio", ctx.box.seal("secret"))
    route = respx.post(OPENAI_STT).mock(return_value=httpx.Response(200, json={"text": "hello"}))
    model = b"openai/whisper-1"
    with TestClient(create_app(ctx)) as client:
        request = client.build_request(
            "POST", "/v1/audio/transcriptions", headers=AUTH,
            data={"model": model.decode(), "prompt": model.decode()},
            files={"file": ("sample.wav", AUDIO + model, "audio/wav")},
        )
        original = request.read()
        response = client.send(request)
    assert response.status_code == 200
    assert route.calls[0].request.content == original.replace(model, b"whisper-1", 1)


@respx.mock
def test_provider_header_preserves_upstream_namespace_and_reported_usage(ctx):
    ctx.db.add_key("openrouter", "audio", ctx.box.seal("router-secret"))
    route = respx.post(OPENROUTER_STT).mock(
        return_value=httpx.Response(200, json={"text": "hello", "usage": {"seconds": 7.5}})
    )
    with TestClient(create_app(ctx)) as client:
        response = _post(client, headers={**AUTH, "X-Provider": "openrouter"}, model="openai/whisper-1")
    assert response.status_code == 200
    assert b"openai/whisper-1" in route.calls[0].request.content
    assert ctx.db.recent_usage()[0]["audio_seconds"] == 7.5


def test_transcription_rejects_unknown_model_and_unsupported_provider(ctx):
    with TestClient(create_app(ctx)) as client:
        assert _post(client, model="gpt-4o").status_code == 404
        assert _post(client, model="gemini/whisper-1").status_code == 400


@respx.mock
def test_custom_transcription_provider_can_fail_over_to_another_provider(ctx):
    ctx.settings.custom_providers = [{
        "id": "speech", "base_url": "https://speech.test/v1", "auth": "x-api-key",
        "transcription_path": "/transcribe", "transcription_models": ["whisper-1"],
    }]
    ctx.providers.update(merged_providers(ctx.settings))
    ctx.settings.provider_priority = ["speech", "openai"]
    ctx.db.add_key("speech", "custom", ctx.box.seal("custom-secret"))
    ctx.db.add_key("openai", "fallback", ctx.box.seal("openai-secret"))
    custom = respx.post("https://speech.test/v1/transcribe").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "30"})
    )
    fallback = respx.post(OPENAI_STT).mock(return_value=httpx.Response(200, json={"text": "fallback"}))
    with TestClient(create_app(ctx)) as client:
        response = _post(client, model="whisper-1")
        pinned = _post(client, model="speech/whisper-1")
    assert response.json() == {"text": "fallback"}
    assert custom.calls[0].request.headers["x-api-key"] == "custom-secret"
    assert custom.calls[0].request.content == fallback.calls[0].request.content
    assert pinned.status_code == 429
    assert fallback.call_count == 1


@respx.mock
def test_audio_upstream_error_body_is_excluded_from_usage_and_exhaustion(ctx):
    _add(ctx, n=1)
    respx.post(GROQ_STT).mock(return_value=httpx.Response(503, text="private upstream detail"))
    with TestClient(create_app(ctx)) as client:
        response = _post(client)
    assert response.status_code == 503
    assert "private upstream detail" not in response.text
    assert ctx.db.recent_usage()[0]["error"] == "HTTP 503 from groq."


@respx.mock
def test_transcription_exhaustion_and_cooling_include_retry_after(ctx):
    _add(ctx, n=1)
    route = respx.post(GROQ_STT).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "30"})
    )
    with TestClient(create_app(ctx)) as client:
        first = _post(client)
        second = _post(client)
    for response in (first, second):
        assert response.status_code == 429
        assert 1 <= int(response.headers["Retry-After"]) <= 30
    assert route.call_count == 1


@respx.mock
def test_transcription_duration_from_response_and_file(ctx):
    _add(ctx, n=1)
    respx.post(GROQ_STT).mock(side_effect=[
        httpx.Response(200, json={"text": "provider duration", "duration": 2.25}),
        httpx.Response(200, text="file duration"),
        httpx.Response(200, json={"text": "unknown duration", "duration": True}),
    ])
    audio_file = BytesIO()
    with wave.open(audio_file, "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0" * 8000)
    app = create_app(ctx)
    with TestClient(app) as client:
        assert _post(client, response_format="verbose_json").json()["duration"] == 2.25
        plain = client.post(
            "/v1/audio/transcriptions", headers=AUTH,
            data={"model": "whisper-large-v3", "response_format": "text"},
            files={"file": ("test.wav", audio_file.getvalue(), "audio/wav")},
        )
        assert plain.text == "file duration"
        assert _post(client).status_code == 200

    rows = ctx.db.recent_usage()
    assert [row["audio_seconds"] for row in rows] == [None, 0.5, 2.25]
    assert all(row["total_tokens"] == 0 for row in rows)
    with TestClient(app) as client:
        assert client.get("/v1/usage", headers=AUTH).json()["audio_seconds"] == 2.75
