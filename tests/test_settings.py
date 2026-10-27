from synckey.config import Settings, config_path


def test_save_load_roundtrip(synckey_home):
    s = Settings()
    s.provider_priority = ["groq", "gemini"]
    s.cross_provider_fallback = False
    s.max_retries = 3
    s.tier_overrides = {"my-model.v2": "frontier"}
    s.price_overrides = {"my-model.v2": [1.0, 2.0]}
    s.custom_providers = [
        {
            "id": "acme",
            "name": "Acme",
            "base_url": "https://acme.test/v1",
            "env": ["ACME_KEY"],
            "patterns": ["^acme-"],
            "auth": "bearer",
        }
    ]
    s.save()
    assert config_path().exists()

    s2 = Settings.load()
    assert s2.provider_priority == ["groq", "gemini"]
    assert s2.cross_provider_fallback is False
    assert s2.max_retries == 3
    assert s2.tier_overrides == {"my-model.v2": "frontier"}
    assert s2.price_overrides == {"my-model.v2": [1.0, 2.0]}
    assert s2.custom_providers[0]["id"] == "acme"
    assert s2.custom_providers[0]["env"] == ["ACME_KEY"]


def test_save_preserves_unknown_sections(synckey_home):
    config_path().write_text("[zzz_custom]\nfoo = 1\n", encoding="utf-8")
    Settings().save()
    assert Settings.load() is not None
    assert "zzz_custom" in config_path().read_text(encoding="utf-8")
