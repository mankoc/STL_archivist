"""
Database session factory.
Swap the DB URL in get_engine() to migrate away from SQLite in the future.
"""
import logging
import time
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, Session

from app.database.models import Base

logger = logging.getLogger(__name__)

_engine = None
_SessionFactory = None


def init_db(db_path: str = "stlLibrary.db") -> None:
    """Initialise the engine and create all tables if they don't exist."""
    from pathlib import Path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    global _engine, _SessionFactory
    url = f"sqlite:///{db_path}"
    _engine = create_engine(url, connect_args={"check_same_thread": False})

    # Enable WAL mode, foreign keys, and performance pragmas for SQLite.
    # The connect event passes the raw DBAPI connection, so we need an explicit
    # cursor — conn.execute() on a sqlite3.Connection silently no-ops for pragmas.
    @event.listens_for(_engine, "connect")
    def _set_pragmas(conn, _record):
        cursor = conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            # Safe with WAL; fsyncs only at checkpoint, not every commit (2-5x faster writes).
            cursor.execute("PRAGMA synchronous=NORMAL")
            # 64 MB page cache (negative = KiB). Default is ~2 MB.
            cursor.execute("PRAGMA cache_size=-65536")
            # Sort buffers / temp tables held in RAM instead of /tmp.
            cursor.execute("PRAGMA temp_store=MEMORY")
            # 256 MB memory-mapped I/O window for read-heavy scans.
            cursor.execute("PRAGMA mmap_size=268435456")
            # Wait up to 5 s for a held lock before raising OperationalError.
            cursor.execute("PRAGMA busy_timeout=5000")
            # Auto-checkpoint when the WAL grows past ~1000 pages (~4 MB).
            cursor.execute("PRAGMA wal_autocheckpoint=1000")
        finally:
            cursor.close()

    _migrate_schema(_engine)
    Base.metadata.create_all(_engine)
    _ensure_columns(_engine)
    _attach_query_profiler(_engine)

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

    _ensure_indexes(engine)


def _ensure_indexes(engine) -> None:
    """Create any missing indexes on existing databases."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_models_archive_hash "
            "ON models (archive_hash)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_models_release_date "
            "ON models (release_year, release_month)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_images_model_id "
            "ON images (model_id)"
        ))
        # Composite indexes covering the browser's hot filter combinations.
        # SQLite will pick the prefix that matches the WHERE clause, so a single
        # index on (creator, source) also accelerates plain creator-only filters.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_models_creator_source "
            "ON models (creator, source)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_models_sculptor_collection "
            "ON models (sculptor_id, collection_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_models_collection_subcollection "
            "ON models (collection_id, subcollection_id)"
        ))
        # Partial index for the missing-files reconciliation path — only the
        # tiny minority of rows where is_missing=1 are indexed.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_models_is_missing "
            "ON models (is_missing) WHERE is_missing = 1"
        ))
        # Helps Image.dhash duplicate scans (find_content_duplicates_dialog).
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_images_dhash "
            "ON images (dhash) WHERE dhash IS NOT NULL"
        ))
        # ANALYZE updates SQLite's stats once after adding indexes so the
        # planner picks the new ones on the very first query.
        try:
            conn.execute(text("ANALYZE"))
        except Exception:
            pass


def _query_profiling_enabled() -> bool:
    """Read the SQL profiling toggle from QSettings, default off.

    Imported lazily because QSettings depends on QCoreApplication being created,
    which may not be true during early init or tests.
    """
    try:
        from PyQt6.QtCore import QSettings
        return bool(QSettings().value("debug/sql_profiling", False, type=bool))
    except Exception:
        return False


def _attach_query_profiler(engine) -> None:
    """Install before/after_cursor_execute hooks that time each statement.

    When the ``debug/sql_profiling`` QSetting is on, every SQL statement is
    logged at INFO level with its wall-clock duration. Statements taking longer
    than 100 ms are also logged at WARNING level for easy grep.
    The check happens per-statement so the flag can be toggled at runtime
    without restarting the engine.
    """
    @event.listens_for(engine, "before_cursor_execute")
    def _before(_conn, _cursor, _statement, _params, context, _executemany):
        if _query_profiling_enabled():
            context._stl_query_start = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(_conn, _cursor, statement, _params, context, _executemany):
        start = getattr(context, "_stl_query_start", None)
        if start is None:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000
        # First line of the statement only — full SQL goes to DEBUG.
        snippet = statement.strip().split("\n", 1)[0][:200]
        if elapsed_ms >= 100:
            logger.warning("SQL %7.1f ms  %s", elapsed_ms, snippet)
        else:
            logger.info("SQL %7.1f ms  %s", elapsed_ms, snippet)
        logger.debug("SQL full statement:\n%s", statement)


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
