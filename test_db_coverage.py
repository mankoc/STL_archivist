import pytest
from sqlalchemy import inspect
from app.database.db import _migrate_schema, _ensure_columns, init_db, clear_db, get_session
from app.database.models import Base

def test_clear_db():
    init_db(":memory:")
    clear_db()
