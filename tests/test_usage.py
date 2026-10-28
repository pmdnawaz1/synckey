import sqlite3
import time

from synckey.usage import UsageWriter


def _count(path):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
    finally:
        conn.close()


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
