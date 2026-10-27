"""Configuration and paths.

State lives under $SYNCKEY_HOME (default ~/.synckey):
    secret.key   Fernet key for sealing provider credentials (0600)
    synckey.db   SQLite: keys, usage, events, key state, metadata
    config.toml  user settings and custom providers

The unified gateway key is generated once at init time; only its SHA-256 hash
is persisted, so the plaintext is shown exactly once.
"""

from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

UNIFIED_KEY_PREFIX = "sk-synckey-"


def home() -> Path:
    return Path(os.environ.get("SYNCKEY_HOME", Path.home() / ".synckey"))


def secret_key_path() -> Path:
    return home() / "secret.key"


def db_path() -> Path:
    return Path(os.environ.get("SYNCKEY_DB", home() / "synckey.db"))


def config_path() -> Path:
    return home() / "config.toml"


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8787
    request_timeout: float = 120.0
    max_connections: int = 600
    max_keepalive: int = 300
    # Fallback: ordered preference when several providers serve the same model.
    provider_priority: list[str] = field(default_factory=list)
    cross_provider_fallback: bool = True
    # Per-request attempt budget across the key pool.
    max_retries: int = 6
    default_cooldown: float = 20.0
    # Tier fallback: allow cross-tier fallback to same-tier alternatives.
    tier_fallback_enabled: bool = True
    # Seconds of cooldown remaining before we skip to tier alternatives entirely.
    deep_cooling_threshold: float = 60.0
    # User-defined model -> tier overrides, e.g. {"my-model": "frontier"}.
    tier_overrides: dict[str, str] = field(default_factory=dict)
    # User-defined model pricing overrides, e.g. {"my-model": [1.0, 3.0]}.
    price_overrides: dict[str, list] = field(default_factory=dict)
    custom_providers: list[dict] = field(default_factory=list)
    # Deferred queue: when all keys are cooling, accept the request, return a
    # request id, run it when capacity frees, and serve the result on a later GET.
    deferred_enabled: bool = True
    deferred_ttl: float = 3600.0          # keep a finished response this long, then purge
    deferred_poll: float = 5.0            # worker scan interval (seconds)
    deferred_max_queue: int = 1000        # reject new deferrals beyond this many queued
    deferred_max_queue_age: float = 86400.0  # drop a job that never ran within this window

    @classmethod
    def load(cls) -> "Settings":
        path = config_path()
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        gw = data.get("gateway", {})
        routing = data.get("routing", {})
        tiers = data.get("tiers", {})
        deferred = data.get("deferred", {})
        return cls(
            host=gw.get("host", cls.host),
            port=gw.get("port", cls.port),
            request_timeout=gw.get("request_timeout", cls.request_timeout),
            max_connections=gw.get("max_connections", cls.max_connections),
            max_keepalive=gw.get("max_keepalive", cls.max_keepalive),
            provider_priority=routing.get("priority", []),
            cross_provider_fallback=routing.get("cross_provider_fallback", True),
            max_retries=routing.get("max_retries", cls.max_retries),
            default_cooldown=routing.get("default_cooldown", cls.default_cooldown),
            tier_fallback_enabled=routing.get("tier_fallback", True),
            deep_cooling_threshold=routing.get("deep_cooling_threshold", cls.deep_cooling_threshold),
            tier_overrides=tiers.get("overrides", {}),
            price_overrides=tiers.get("prices", {}),
            custom_providers=data.get("providers", []),
            deferred_enabled=deferred.get("enabled", True),
            deferred_ttl=deferred.get("ttl", cls.deferred_ttl),
            deferred_poll=deferred.get("poll", cls.deferred_poll),
              deferred_max_queue=deferred.get("max_queue", cls.deferred_max_queue),
              deferred_max_queue_age=deferred.get("max_queue_age", cls.deferred_max_queue_age),
          )

    def save(self) -> None:
        """Write current settings back to config.toml."""
        path = config_path()
        data = {}
        if path.exists():
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        data.setdefault("gateway", {})["host"] = self.host
        data.setdefault("gateway", {})["port"] = self.port
        data.setdefault("gateway", {})["request_timeout"] = self.request_timeout
        data.setdefault("gateway", {})["max_connections"] = self.max_connections
        data.setdefault("gateway", {})["max_keepalive"] = self.max_keepalive
        data.setdefault("routing", {})["priority"] = self.provider_priority
        data.setdefault("routing", {})["cross_provider_fallback"] = self.cross_provider_fallback
        data.setdefault("routing", {})["max_retries"] = self.max_retries
        data.setdefault("routing", {})["default_cooldown"] = self.default_cooldown
        data.setdefault("routing", {})["tier_fallback"] = self.tier_fallback_enabled
        data.setdefault("routing", {})["deep_cooling_threshold"] = self.deep_cooling_threshold
        data.setdefault("tiers", {})["overrides"] = self.tier_overrides
        data.setdefault("tiers", {})["prices"] = self.price_overrides
        data["providers"] = self.custom_providers
        data.setdefault("deferred", {})["enabled"] = self.deferred_enabled
        data.setdefault("deferred", {})["ttl"] = self.deferred_ttl
        data.setdefault("deferred", {})["poll"] = self.deferred_poll
        data.setdefault("deferred", {})["max_queue"] = self.deferred_max_queue
        data.setdefault("deferred", {})["max_queue_age"] = self.deferred_max_queue_age

        path.write_text(dumps_toml(data), encoding="utf-8")


def _toml_key(key: str) -> str:
    if key and all(c.isalnum() or c in "_-" for c in key):
        return key
    return _toml_str(key)


def _toml_str(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _toml_value(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _toml_str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(f"{_toml_key(k)} = {_toml_value(v)}" for k, v in value.items())
        return "{ " + inner + " }" if inner else "{}"
    raise TypeError(f"Cannot encode {type(value).__name__} as TOML")


def dumps_toml(data: dict) -> str:
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"[{key}]")
            for sk, sv in value.items():
                lines.append(f"{_toml_key(sk)} = {_toml_value(sv)}")
            lines.append("")
        elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            for item in value:
                lines.append(f"[[{key}]]")
                for sk, sv in item.items():
                    lines.append(f"{_toml_key(sk)} = {_toml_value(sv)}")
                lines.append("")
        else:
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    return "\n".join(lines).rstrip() + "\n"


def ensure_home() -> Path:
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(h, 0o700)
    except OSError:
        pass
    return h


def generate_unified_key() -> str:
    return UNIFIED_KEY_PREFIX + secrets.token_urlsafe(32)
