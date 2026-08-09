import os
import tempfile

import pytest
from sqlalchemy import text
from sqlalchemy.pool import QueuePool


def test_engine_uses_queue_pool_with_size_five():
    import database

    assert isinstance(database.engine.pool, QueuePool)
    assert database.engine.pool.size() == 5


def test_sqlite_pragmas_on_connect():
    import database

    with database.engine.connect() as conn:
        pragmas = {
            "journal_mode": conn.execute(text("PRAGMA journal_mode")).scalar(),
            "foreign_keys": conn.execute(text("PRAGMA foreign_keys")).scalar(),
            "synchronous": conn.execute(text("PRAGMA synchronous")).scalar(),
            "cache_size": conn.execute(text("PRAGMA cache_size")).scalar(),
            "temp_store": conn.execute(text("PRAGMA temp_store")).scalar(),
            "mmap_size": conn.execute(text("PRAGMA mmap_size")).scalar(),
        }

    assert pragmas["journal_mode"].lower() == "wal"
    assert pragmas["foreign_keys"] == 1
    assert pragmas["synchronous"] == 1  # NORMAL
    assert pragmas["cache_size"] == -64000
    assert pragmas["temp_store"] == 2  # MEMORY
    assert pragmas["mmap_size"] == 268435456


def test_init_db_creates_tables(monkeypatch):
    import database

    with tempfile.TemporaryDirectory() as tmpdir:
        test_db_path = os.path.join(tmpdir, "test.sqlite3")
        test_url = f"sqlite:///{test_db_path}"

        from sqlalchemy import create_engine
        from sqlalchemy.pool import QueuePool

        test_engine = create_engine(
            test_url,
            connect_args={"check_same_thread": False},
            poolclass=QueuePool,
            pool_size=5,
        )

        monkeypatch.setattr(database, "engine", test_engine)
        database.init_db()

        with test_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
            tables = {r[0] for r in rows}

        assert "jobs" in tables
        assert "clips" in tables
        assert "scheduled_posts" in tables
        assert "accounts" in tables
