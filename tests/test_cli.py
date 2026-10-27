from typer.testing import CliRunner

from synckey.cli import app
from synckey.config import Settings
from synckey.context import Context

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

    r = runner.invoke(app, ["spend"])
    assert r.exit_code == 0, r.output
    assert r.output.count("Spend breakdown") == 1
