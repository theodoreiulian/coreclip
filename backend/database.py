import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import Engine
from sqlalchemy import event
from sqlalchemy.pool import QueuePool

# Base directory is one level up from backend/
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "db.sqlite3")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=QueuePool,
    pool_size=5,
)

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=-64000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA mmap_size=268435456")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _ensure_columns():
    """Lightweight, idempotent 'migration' for SQLite.

    `Base.metadata.create_all` creates missing *tables* but never adds new
    *columns* to a table that already exists. Since the DB is created once on
    startup and there are no Alembic migrations, we reconcile each mapped
    table against its model here by ALTER-ing in any columns that are missing.
    All new columns must be nullable or carry a constant default — which is
    the only thing SQLite's ADD COLUMN supports.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        for table in Base.metadata.tables.values():
            rows = conn.execute(text(f'PRAGMA table_info("{table.name}")')).fetchall()
            if not rows:
                continue  # table doesn't exist yet; create_all handled it
            existing = {r[1] for r in rows}  # row[1] is the column name
            for column in table.columns:
                if column.name in existing:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'
                conn.execute(text(ddl))


def _reconcile_indexes():
    """Idempotently drop legacy DB-level UNIQUE constraints that the application
    now governs in code.

    `jobs.youtube_video_id` was historically a UNIQUE index, but job uniqueness
    is enforced in application logic (create_job, for non-failed jobs only).
    A hard UNIQUE index wrongly rejected resubmitting a URL whose previous job
    had failed. Convert any legacy UNIQUE index into a plain (non-unique) one.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='ix_jobs_youtube_video_id'"
        )).fetchone()
        if row and row[0] and "UNIQUE" in row[0].upper():
            conn.execute(text('DROP INDEX ix_jobs_youtube_video_id'))
            conn.execute(text(
                'CREATE INDEX ix_jobs_youtube_video_id ON jobs (youtube_video_id)'
            ))


def init_db():
    import models
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    _reconcile_indexes()
