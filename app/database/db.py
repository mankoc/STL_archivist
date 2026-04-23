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


def init_db(db_path: str = "stl_database.db") -> None:
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

    Base.metadata.create_all(_engine)
    _ensure_columns(_engine)

    # Initialise LMDB thumbnail store alongside the database
    from app.utils.thumbnailer import init_thumbnail_store
    thumb_dir = Path(db_path).parent / "thumbnails.lmdb"
    init_thumbnail_store(thumb_dir)

    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)


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
