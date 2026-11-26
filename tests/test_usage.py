import sqlite3
import time

from synckey.usage import UsageWriter


def _count(path):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
    finally:
        conn.close()


def test_existing_usage_rows_survive_audio_duration_migration(tmp_path):
    from synckey.db import Database, SCHEMA

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.replace(",\n    audio_seconds     REAL", ""))
    conn.execute("INSERT INTO usage(ts, provider, model, total_tokens) VALUES (1, 'groq', 'm', 12)")
    conn.commit()
    conn.close()

    for _ in range(2):
        db = Database(path)
        try:
            assert db.recent_usage()[0]["audio_seconds"] is None
            assert db.usage_totals()["total_tokens"] == 12
        finally:
            db.close()


def test_records_flushed_to_db(synckey_home):
    from synckey.config import db_path

    # Ensure schema exists.
    from synckey.db import Database

    Database(db_path()).close()

    w = UsageWriter(db_path(), flush_interval=0.1)
    w.start()
    for i in range(3):
        w.record(
            provider="groq", model="m", key_id=1,
            prompt_tokens=1, completion_tokens=1, total_tokens=2,
            status_code=200, latency_ms=10,
        )
    deadline = time.time() + 5
    while _count(db_path()) < 3 and time.time() < deadline:
        time.sleep(0.1)
    w.stop()
    assert _count(db_path()) == 3


def test_stop_flushes_remaining(synckey_home):
    from synckey.config import db_path
    from synckey.db import Database

    Database(db_path()).close()

    w = UsageWriter(db_path(), flush_interval=30.0)  # long interval: only stop() flushes
    w.start()
    w.record(provider="groq", model="m", key_id=1, status_code=200)
    w.stop(timeout=10)
    assert _count(db_path()) == 1


def test_classify_failure_records_stream_flag():
    from types import SimpleNamespace

    import httpx

    from synckey.server import classify_failure

    recorded = {}

    class _Writer:
        def record(self, **kwargs):
            recorded.update(kwargs)

    attempt = SimpleNamespace(provider_id="groq", key_id=1)
    resolution = SimpleNamespace(tier=None)
    resp = httpx.Response(400, text="bad request")

    retriable, err, status = classify_failure(
        None, _Writer(), attempt, resp, resolution, "m", 10, stream=True
    )

    assert retriable is False
    assert status == 400
    assert err == "bad request"
    assert recorded["stream"] is True
    assert recorded["error"] == "bad request"
