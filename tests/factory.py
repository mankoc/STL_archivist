"""Test data at the SQLite level, without the ORM.

Every test gets its own database file (``conftest.library_db``); these
helpers write and read it with plain ``sqlite3`` on short-lived connections,
committing each write, so the code under test (native or Python) sees it.

    mid = factory.add_model(title="Dragon", creator="Sanix")
    factory.link_tags(mid, "dragon", "bust")
    assert factory.row("models", mid)["title"] == "Dragon"
"""
from __future__ import annotations

import itertools
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

_counter = itertools.count(1)


def db_path() -> str:
    from app.database.db import database_path
    path = database_path()
    assert path is not None, "the test database is in memory"
    return str(path)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(db_path(), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def insert(table: str, **cols: Any) -> int:
    """INSERT one row; returns its rowid."""
    keys = list(cols)
    with connect() as con:
        cur = con.execute(
            f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
            [cols[k] for k in keys],
        )
        return cur.lastrowid


def lookup(table: str, name: str, **cols: Any) -> int:
    """The id of the row named *name* in a lookup table, created if missing."""
    with connect() as con:
        row = con.execute(f"SELECT id FROM {table} WHERE name = ? ORDER BY id LIMIT 1", (name,)).fetchone()
        if row:
            return row[0]
    return insert(table, name=name, **cols)


def add_model(archive_path: Optional[str] = None, archive_hash: Optional[str] = None, *,
              sculptor: Optional[str] = None, collection: Optional[str] = None,
              subcollection: Optional[str] = None, category: Optional[str] = None,
              release: Optional[str] = None, tags: tuple = (), scales: tuple = (),
              **cols: Any) -> int:
    """Insert a model; lookups are given by name and created as needed."""
    n = next(_counter)
    cols.setdefault("is_missing", 0)
    cols.setdefault("to_print", 0)
    cols.setdefault("created_at", _now())
    cols.setdefault("updated_at", _now())
    if sculptor is not None:
        cols["sculptor_id"] = lookup("sculptors", sculptor)
    if collection is not None:
        cols["collection_id"] = lookup("collections", collection)
    if subcollection is not None:
        cid = cols.get("collection_id")
        with connect() as con:
            row = con.execute("SELECT id FROM subcollections WHERE name = ? AND collection_id IS ?",
                              (subcollection, cid)).fetchone()
        cols["subcollection_id"] = row[0] if row else insert("subcollections", name=subcollection, collection_id=cid)
    if category is not None:
        cols["category_id"] = lookup("categories", category)
    if release is not None:
        cols["release_id"] = lookup("releases", release)
    mid = insert("models", archive_path=archive_path or f"F:\\lib\\model{n}.zip",
                 archive_hash=archive_hash if archive_hash is not None else f"hash{n}", **cols)
    if tags:
        link_tags(mid, *tags)
    if scales:
        link_scales(mid, *scales)
    return mid


def link_tags(model_id: int, *names: str) -> None:
    for name in names:
        tid = lookup("tags", name)
        insert("model_tags", model_id=model_id, tag_id=tid)


def link_scales(model_id: int, *names: str) -> None:
    for name in names:
        insert("model_scales", model_id=model_id, scale_id=lookup("scales", name))


def add_image(model_id: int, filename: Optional[str] = "img.jpg", **cols: Any) -> int:
    return insert("images", model_id=model_id, filename=filename, **cols)


def add_stl_file(model_id: int, filename: str, file_size: Optional[int] = None,
                 archive_hash: Optional[str] = None) -> None:
    insert("stl_files", model_id=model_id, filename=filename, file_size=file_size, archive_hash=archive_hash)


def execute(sql: str, *args: Any) -> None:
    with connect() as con:
        con.execute(sql, args)


def rows(sql: str, *args: Any) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(sql, args).fetchall()


def scalar(sql: str, *args: Any) -> Any:
    r = rows(sql, *args)
    return r[0][0] if r else None


def row(table: str, row_id: int) -> Optional[dict]:
    """One row by id, as a dict (``None`` when it does not exist)."""
    r = rows(f"SELECT * FROM {table} WHERE id = ?", row_id)
    return dict(r[0]) if r else None


def count(table: str, where: str = "1", *args: Any) -> int:
    return scalar(f"SELECT count(*) FROM {table} WHERE {where}", *args)


def names(model_id: int, kind: str) -> list[str]:
    """A model's tag or scale names, by name."""
    link, table, col = {"tags": ("model_tags", "tags", "tag_id"),
                        "scales": ("model_scales", "scales", "scale_id")}[kind]
    return [r[0] for r in rows(f"SELECT t.name FROM {link} k JOIN {table} t ON t.id = k.{col} "
                               f"WHERE k.model_id = ? ORDER BY t.name", model_id)]


def lookup_name(model_id: int, kind: str) -> Optional[str]:
    """The name of a model's sculptor / collection / subcollection / category / release."""
    table = {"sculptor": "sculptors", "collection": "collections", "subcollection": "subcollections",
             "category": "categories", "release": "releases"}[kind]
    return scalar(f"SELECT l.name FROM models m JOIN {table} l ON l.id = m.{kind}_id WHERE m.id = ?", model_id)
