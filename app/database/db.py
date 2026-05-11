"""
Database session factory.
Swap the DB URL in get_engine() to migrate away from SQLite in the future.
"""
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, Session

from app.database.models import Base

_engine = None
_SessionFactory = None


def init_db(db_path: str = "stlLibrary.db") -> None:
    """Initialise the engine and create all tables if they don't exist."""
    from pathlib import Path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    global _engine, _SessionFactory
    url = f"sqlite:///{db_path}"
    _engine = create_engine(url, connect_args={"check_same_thread": False})

    # Enable WAL mode and foreign keys for SQLite.
    # The connect event passes the raw DBAPI connection, so we need an explicit
    # cursor — conn.execute() on a sqlite3.Connection silently no-ops for pragmas.
    @event.listens_for(_engine, "connect")
    def _set_pragmas(conn, _record):
        cursor = conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    _migrate_schema(_engine)
    Base.metadata.create_all(_engine)
    _ensure_columns(_engine)

    # Initialise LMDB thumbnail store alongside the database
    from app.utils.thumbnailer import init_thumbnail_store
    thumb_dir = Path(db_path).parent / "thumbnails.lmdb"
    init_thumbnail_store(thumb_dir)

    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)


def _migrate_schema(engine) -> None:
    """Rename tables and columns from old schema to new schema.

    Handles the one-time migration:
      categories  → collections
      subcategories → subcollections
      subcollections.category_id → collection_id
      models.author → creator
      models.category_id → collection_id
      models.subcategory_id → subcollection_id
    Safe to call repeatedly — checks current state before each operation.
    """
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        try:
            def _tables():
                return {r[0] for r in conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )).fetchall()}

            def _cols(table):
                return {r[1] for r in conn.execute(text(
                    f"PRAGMA table_info({table})"
                )).fetchall()}

            tables = _tables()

            if "categories" in tables and "collections" not in tables:
                conn.execute(text("ALTER TABLE categories RENAME TO collections"))
                tables.discard("categories")
                tables.add("collections")

            if "subcategories" in tables and "subcollections" not in tables:
                conn.execute(text("ALTER TABLE subcategories RENAME TO subcollections"))
                tables.discard("subcategories")
                tables.add("subcollections")

            if "subcollections" in tables:
                cols = _cols("subcollections")
                if "category_id" in cols and "collection_id" not in cols:
                    conn.execute(text(
                        "ALTER TABLE subcollections RENAME COLUMN category_id TO collection_id"
                    ))

            if "models" in tables:
                cols = _cols("models")
                if "author" in cols and "creator" not in cols:
                    conn.execute(text("ALTER TABLE models RENAME COLUMN author TO creator"))
                cols = _cols("models")
                if "category_id" in cols and "collection_id" not in cols:
                    conn.execute(text(
                        "ALTER TABLE models RENAME COLUMN category_id TO collection_id"
                    ))
                cols = _cols("models")
                if "subcategory_id" in cols and "subcollection_id" not in cols:
                    conn.execute(text(
                        "ALTER TABLE models RENAME COLUMN subcategory_id TO subcollection_id"
                    ))
                # Rename warning string: "Archive inside archive" → "Nested archives"
                conn.execute(text(
                    "UPDATE models SET warning = REPLACE(warning, "
                    "'Archive inside archive', 'Nested archives') "
                    "WHERE warning LIKE '%Archive inside archive%'"
                ))
        finally:
            conn.execute(text("PRAGMA foreign_keys=ON"))


def _ensure_columns(engine) -> None:
    """Add any columns defined in the ORM models that are missing from the DB.

    Uses ALTER TABLE … ADD COLUMN for each gap found. Safe to call on every
    startup — it is a no-op when the schema is already up to date.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        try:
            for table in Base.metadata.sorted_tables:
                if table.name not in inspector.get_table_names():
                    continue  # create_all already handles missing tables
                existing = {col["name"] for col in inspector.get_columns(table.name)}
                for col in table.columns:
                    if col.name in existing:
                        continue
                    # Build a minimal column definition for SQLite ALTER TABLE
                    col_type = col.type.compile(engine.dialect)
                    nullable = "" if col.nullable else " NOT NULL"
                    if col.server_default is not None:
                        default = f" DEFAULT {col.server_default.arg}"
                    elif not col.nullable and col.default is not None:
                        # Scalar Python default — convert to SQL literal
                        val = col.default.arg
                        default = f" DEFAULT {int(val) if isinstance(val, bool) else repr(val)}"
                    else:
                        default = ""
                    ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}{nullable}{default}'
                    conn.execute(text(ddl))
        finally:
            conn.execute(text("PRAGMA foreign_keys=ON"))


def get_session() -> Session:
    """Return a new session from the factory.

    Raises:
        RuntimeError: If ``init_db()`` has not been called yet.
    """
    if _SessionFactory is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _SessionFactory()


def clear_db() -> None:
    """Delete all rows from every table (keeps the schema intact).

    Raises:
        RuntimeError: If ``init_db()`` has not been called yet.
    """
    if _engine is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    with _engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@contextmanager
def session_scope():
    """Provide a transactional scope around a series of database operations.

    Commits on success, rolls back on any exception, and always closes the
    session.  Intended to be used as a ``with`` statement::

        with session_scope() as session:
            session.add(model)

    Yields:
        An open ``Session`` instance.
    """
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
