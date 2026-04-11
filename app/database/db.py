"""
Database session factory.
Swap the DB URL in get_engine() to migrate away from SQLite in the future.
"""
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from app.database.models import Base

_engine = None
_SessionFactory = None


def init_db(db_path: str = "stl_database.db") -> None:
    """Initialise the engine and create all tables if they don't exist."""
    from pathlib import Path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    global _engine, _SessionFactory
    url = f"sqlite:///{db_path}"
    _engine = create_engine(url, connect_args={"check_same_thread": False})

    # Enable WAL mode and foreign keys for SQLite
    @event.listens_for(_engine, "connect")
    def _set_pragmas(conn, _record):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(_engine)

    # Initialise thumbnail file store alongside the database
    from app.utils.thumbnailer import init_thumbnail_store
    thumb_dir = Path(db_path).parent / "thumbnails"
    init_thumbnail_store(thumb_dir)

    _migrate(_engine)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)


def _migrate(engine) -> None:
    """Apply incremental schema changes for existing databases."""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)

    # -- models table --
    existing_models = {c["name"] for c in inspector.get_columns("models")}
    with engine.begin() as conn:
        if "release_id" not in existing_models:
            conn.execute(text(
                "ALTER TABLE models ADD COLUMN release_id INTEGER "
                "REFERENCES releases(id) ON DELETE SET NULL"
            ))

    # -- images table --
    existing_images = {c["name"] for c in inspector.get_columns("images")}
    with engine.begin() as conn:
        if "thumbnail_path" not in existing_images:
            conn.execute(text("ALTER TABLE images ADD COLUMN thumbnail_path VARCHAR(512)"))

    # Migrate any existing blobs to files
    _migrate_blobs_to_files(engine)


def _migrate_blobs_to_files(engine) -> None:
    """Write legacy JPEG blobs to disk, set thumbnail_path, then clear the blob."""
    from sqlalchemy import text
    from app.utils.thumbnailer import write_raw_thumbnail

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, thumbnail FROM images "
                 "WHERE thumbnail IS NOT NULL AND thumbnail_path IS NULL")
        ).fetchall()

        for image_id, blob in rows:
            rel = write_raw_thumbnail(image_id, bytes(blob))
            if rel:
                conn.execute(
                    text("UPDATE images SET thumbnail_path = :path, thumbnail = NULL "
                         "WHERE id = :id"),
                    {"path": rel, "id": image_id},
                )


def get_session() -> Session:
    if _SessionFactory is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _SessionFactory()


def clear_db() -> None:
    """Delete all rows from every table (keeps the schema intact)."""
    if _engine is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    with _engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
