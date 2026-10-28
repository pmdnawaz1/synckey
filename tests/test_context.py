from synckey.config import Settings
from synckey.context import Context, merged_providers


def test_first_secret_from_stored_key(ctx):
    ctx.db.add_key("groq", "k", ctx.box.seal("stored-secret"))
    assert ctx.first_secret("groq") == "stored-secret"


def test_first_secret_env_fallback(ctx, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "env-secret")
    assert ctx.first_secret("groq") == "env-secret"


def test_first_secret_none_when_nothing_configured(ctx, monkeypatch):
    for var in ("GROQ_API_KEY",):
        monkeypatch.delenv(var, raising=False)
    assert ctx.first_secret("groq") is None


def test_merged_providers_adds_custom():
    s = Settings()
    s.custom_providers = [
        {
            "id": "acme",
            "name": "Acme",
            "base_url": "https://acme.test/v1",
            "patterns": ["^acme-"],
        }
    ]
    providers = merged_providers(s)
    assert "acme" in providers
    assert providers["acme"].matches("acme-model-1")


def test_merged_providers_skips_malformed():
    s = Settings()
    s.custom_providers = [
        {"name": "no id and no base_url"},  # missing required keys -> skipped
        {"id": "ok", "base_url": "https://ok.test/v1"},
    ]
    providers = merged_providers(s)
    assert "ok" in providers
    assert len([p for p in providers if p == "ok"]) == 1


def test_context_close_is_clean(ctx):
    ctx.close()  # second close must not raise (idempotent enough for fixture teardown)
