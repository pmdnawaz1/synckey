from typer.testing import CliRunner

from synckey.cli import app
from synckey.config import Settings
from synckey.context import Context
from synckey.db import sha256

runner = CliRunner()


def test_routing_priority_unquoted_args_persist(synckey_home):
    r = runner.invoke(app, ["routing", "priority", "groq", "gemini"])
    assert r.exit_code == 0, r.output
    assert Settings.load().provider_priority == ["groq", "gemini"]


def test_routing_preset_persists(synckey_home):
    r = runner.invoke(app, ["routing", "preset", "cheap"])
    assert r.exit_code == 0, r.output
    assert Settings.load().tier_fallback_enabled is False


def test_keys_limits_sets_caps(synckey_home):
    c = Context()
    kid = c.db.add_key("groq", "k", c.box.seal("s"))
    c.close()

    r = runner.invoke(
        app, ["keys", "--limits", str(kid), "--rpm", "60", "--tpm", "1000", "--weight", "3"]
    )
    assert r.exit_code == 0, r.output

    c2 = Context()
    rec = [k for k in c2.db.list_keys() if k.id == kid][0]
    c2.close()
    assert rec.rpm_limit == 60
    assert rec.tpm_limit == 1000
    assert rec.weight == 3


def test_spend_prints_once(synckey_home):
    c = Context()
    c.db.conn.execute(
        "INSERT INTO usage(ts, provider, model, total_tokens, cost_usd, status_code)"
        " VALUES(1, 'groq', 'm', 10, 0.01, 200)"
    )
    c.db.conn.commit()
    c.close()

    r = runner.invoke(app, ["usage", "--spend"])
    assert r.exit_code == 0, r.output
    assert r.output.count("Spend breakdown") == 1


def test_renew_rotates_unified_key(synckey_home):
    c = Context()
    c.db.set_meta("unified_key_hash", sha256("old-key"))
    c.close()

    r = runner.invoke(app, ["renew"])
    assert r.exit_code == 0, r.output
    assert "sk-synckey-" in r.output

    c2 = Context()
    new_hash = c2.db.get_meta("unified_key_hash")
    c2.close()
    assert new_hash is not None
    assert new_hash != sha256("old-key")


def test_keys_add_with_key_option(synckey_home):
    r = runner.invoke(app, ["keys", "add", "groq", "--key", "gsk_test_abc123"])
    assert r.exit_code == 0, r.output

    c = Context()
    keys = c.db.list_keys(provider="groq")
    assert len(keys) == 1
    assert c.box.open(keys[0].secret) == "gsk_test_abc123"
    c.close()


def test_keys_add_bulk_comma_separated(synckey_home):
    r = runner.invoke(app, ["keys", "add", "groq", "--key", "k1,k2,k3", "--rpm", "60"])
    assert r.exit_code == 0, r.output

    c = Context()
    keys = c.db.list_keys(provider="groq")
    assert len(keys) == 3
    assert all(k.rpm_limit == 60 for k in keys)
    c.close()


def test_keys_add_from_file(synckey_home, tmp_path):
    f = tmp_path / "keys.txt"
    f.write_text("# comment\nfile-key-1\nfile-key-2, file-key-3\n")
    r = runner.invoke(app, ["keys", "add", "gemini", "--file", str(f)])
    assert r.exit_code == 0, r.output

    c = Context()
    assert len(c.db.list_keys(provider="gemini")) == 3
    c.close()


def test_keys_add_rejects_unknown_provider(synckey_home):
    r = runner.invoke(app, ["keys", "add", "nosuchprovider", "--key", "x"])
    assert r.exit_code == 1


def test_keys_list_still_works_via_callback(synckey_home):
    c = Context()
    c.db.add_key("groq", "k", c.box.seal("s"))
    c.close()
    r = runner.invoke(app, ["keys"])
    assert r.exit_code == 0, r.output
    assert "groq" in r.output


def test_stop_without_pid_file_fails(synckey_home):
    r = runner.invoke(app, ["stop"])
    assert r.exit_code == 1
    assert "No PID file" in r.output


def test_collect_secrets_prompt_with_bulk_paste(ctx, monkeypatch):
    from synckey.cli import _collect_secrets

    blob = ",".join(f"gsk_test_key_{i:02d}_" + "x" * 40 for i in range(18))
    assert len(blob) > 255
    monkeypatch.setattr("synckey.cli.typer.prompt", lambda *a, **k: blob)
    secrets = _collect_secrets(ctx, "groq", None, None, False)
    assert len(secrets) == 18


def test_collect_secrets_prompt_with_file_path(ctx, tmp_path, monkeypatch):
    from synckey.cli import _collect_secrets

    f = tmp_path / "keys.txt"
    f.write_text("file-key-1\nfile-key-2\n")
    monkeypatch.setattr("synckey.cli.typer.prompt", lambda *a, **k: str(f))
    assert _collect_secrets(ctx, "groq", None, None, False) == ["file-key-1", "file-key-2"]


def test_collect_secrets_prompt_with_missing_path_treated_as_key(ctx, monkeypatch):
    from synckey.cli import _collect_secrets

    monkeypatch.setattr("synckey.cli.typer.prompt", lambda *a, **k: "not-a-real-file-xyz")
    assert _collect_secrets(ctx, "groq", None, None, False) == ["not-a-real-file-xyz"]


def test_collect_secrets_prompt_with_null_byte_does_not_crash(ctx, monkeypatch):
    from synckey.cli import _collect_secrets

    monkeypatch.setattr("synckey.cli.typer.prompt", lambda *a, **k: "abc\x00def")
    assert _collect_secrets(ctx, "groq", None, None, False) == ["abc\x00def"]


def test_collect_secrets_prompt_survives_stat_failure(ctx, monkeypatch):
    from synckey.cli import _collect_secrets

    token = "x" * 5000
    monkeypatch.setattr("synckey.cli.typer.prompt", lambda *a, **k: token)

    def _boom(self, *a, **k):
        raise OSError(36, "File name too long")

    monkeypatch.setattr("pathlib.Path.exists", _boom)
    monkeypatch.setattr("pathlib.Path.is_file", _boom)
    assert _collect_secrets(ctx, "groq", None, None, False) == [token]


def test_keys_add_with_binary_file_fails_cleanly(synckey_home, tmp_path):
    f = tmp_path / "keys.bin"
    f.write_bytes(b"\x00\x01\x02\xff\xfe\xfd" * 100)
    r = runner.invoke(app, ["keys", "add", "groq", "--file", str(f)])
    assert r.exit_code == 1
    assert "Cannot read file" in r.output


def test_stop_with_corrupt_pid_file(synckey_home):
    from synckey.cli import _pid_file

    _pid_file().parent.mkdir(parents=True, exist_ok=True)
    _pid_file().write_text("not-a-pid")
    r = runner.invoke(app, ["stop"])
    assert r.exit_code == 1
    assert "Corrupt PID file" in r.output
