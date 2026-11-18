from synckey.providers import BUILTIN, match_by_pattern, split_provider_prefix


def test_known_providers_present():
    for pid in ("gemini", "groq", "cerebras", "nvidia", "github", "cohere", "anthropic"):
        assert pid in BUILTIN


def test_explicit_prefix_splits_known_provider():
    pid, bare = split_provider_prefix("groq/llama-3.3-70b-versatile", set(BUILTIN))
    assert pid == "groq"
    assert bare == "llama-3.3-70b-versatile"


def test_org_namespace_is_not_a_provider_prefix():
    # "meta" is an org, not a registered provider -> model passes through intact.
    pid, bare = split_provider_prefix("meta/llama-3.3-70b-instruct", set(BUILTIN))
    assert pid is None
    assert bare == "meta/llama-3.3-70b-instruct"


def test_pattern_detection_unambiguous_names():
    assert match_by_pattern("gemini-2.0-flash", BUILTIN) == "gemini"
    assert match_by_pattern("command-r-plus", BUILTIN) == "cohere"
    assert match_by_pattern("gpt-4o", BUILTIN) == "openai"
    assert match_by_pattern("grok-2", BUILTIN) == "xai"
    assert match_by_pattern("claude-sonnet-4-6", BUILTIN) == "anthropic"


def test_auth_headers():
    assert BUILTIN["groq"].auth_headers("abc") == {"Authorization": "Bearer abc"}
    assert BUILTIN["anthropic"].auth_headers("abc") == {"x-api-key": "abc"}


def test_groq_strips_assistant_reasoning_only():
    body = {
        "messages": [
            {"role": "user", "content": "hi", "reasoning_content": "keep me"},
            {
                "role": "assistant",
                "content": "ok",
                "reasoning_content": "drop me",
                "reasoning": "drop me too",
            },
            {"role": "tool", "content": "done", "reasoning_content": "keep me"},
        ]
    }
    BUILTIN["groq"].sanitize_messages(body)
    assert "reasoning_content" in body["messages"][0]
    assert "reasoning_content" not in body["messages"][1]
    assert "reasoning" not in body["messages"][1]
    assert "reasoning_content" in body["messages"][2]


def test_providers_without_flag_keep_reasoning():
    body = {
        "messages": [
            {
                "role": "assistant",
                "content": "ok",
                "reasoning_content": "keep me",
                "reasoning": "keep me",
            }
        ]
    }
    BUILTIN["openai"].sanitize_messages(body)
    assert body["messages"][0]["reasoning_content"] == "keep me"
    assert body["messages"][0]["reasoning"] == "keep me"
