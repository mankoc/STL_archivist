"""
Database session factory.
Swap the DB URL in get_engine() to migrate away from SQLite in the future.
"""
import re
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
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
    from sqlalchemy import inspect
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    # -- Rename model_types → categories and subtypes → subcategories --
    if "model_types" in tables:
        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            try:
                # Rename the category table
                conn.execute(text("ALTER TABLE model_types RENAME TO categories"))
                # Recreate subtypes as subcategories with an updated FK reference to categories
                conn.execute(text("""
                    CREATE TABLE subcategories (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                        name VARCHAR(128) NOT NULL,
                        CONSTRAINT uq_subcategory_per_category UNIQUE (category_id, name)
                    )
                """))
                conn.execute(text(
                    "INSERT INTO subcategories (id, category_id, name) "
                    "SELECT id, model_type_id, name FROM subtypes"
                ))
                conn.execute(text("DROP TABLE subtypes"))
            finally:
                conn.execute(text("PRAGMA foreign_keys=ON"))
        # Re-inspect after table renames
        inspector = inspect(engine)

    # -- models table column renames and additions --
    existing_models = {c["name"] for c in inspector.get_columns("models")}
    with engine.begin() as conn:
        if "type_id" in existing_models:
            conn.execute(text("ALTER TABLE models RENAME COLUMN type_id TO category_id"))
        if "subtype_id" in existing_models:
            conn.execute(text("ALTER TABLE models RENAME COLUMN subtype_id TO subcategory_id"))
        if "release_id" not in existing_models:
            conn.execute(text(
                "ALTER TABLE models ADD COLUMN release_id INTEGER "
                "REFERENCES releases(id) ON DELETE SET NULL"
            ))
        if "release_year" not in existing_models:
            conn.execute(text("ALTER TABLE models ADD COLUMN release_year INTEGER"))
        if "release_month" not in existing_models:
            conn.execute(text("ALTER TABLE models ADD COLUMN release_month INTEGER"))

    # Migrate release_date string → release_year + release_month integers
    if "release_date" in existing_models:
        _migrate_release_date(engine)

    # -- images table --
    existing_images = {c["name"] for c in inspector.get_columns("images")}
    with engine.begin() as conn:
        if "thumbnail_path" not in existing_images:
            conn.execute(text("ALTER TABLE images ADD COLUMN thumbnail_path VARCHAR(512)"))

    # Migrate any existing blobs to files
    _migrate_blobs_to_files(engine)


def _migrate_release_date(engine) -> None:
    """Parse the legacy release_date string column into release_year + release_month."""
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, release_date FROM models "
                 "WHERE release_date IS NOT NULL AND release_year IS NULL")
        ).fetchall()
        for row_id, rd in rows:
            if not rd:
                continue
            m = re.match(r'^(\d{4})[-_](\d{1,2})$', rd.strip())
            if m:
                conn.execute(
                    text("UPDATE models SET release_year = :y, release_month = :mo WHERE id = :id"),
                    {"y": int(m.group(1)), "mo": int(m.group(2)), "id": row_id},
                )
                continue
            m = re.match(r'^(\d{4})$', rd.strip())
            if m:
                conn.execute(
                    text("UPDATE models SET release_year = :y WHERE id = :id"),
                    {"y": int(m.group(1)), "id": row_id},
                )


def _migrate_blobs_to_files(engine) -> None:
    """Write legacy JPEG blobs to disk, set thumbnail_path, then clear the blob."""
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
