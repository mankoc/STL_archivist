"""
Tests for app.utils.logger — only the parts that work without a QApplication.
"""
import gzip
import logging
from pathlib import Path

import pytest

from app.utils.logger import (
    LOG_FILENAME,
    _namer,
    _rotator,
    get_log_path,
    setup_logging,
)


def test_namer_appends_gz():
    assert _namer("app.log.1") == "app.log.1.gz"


def test_get_log_path_explicit_dir(tmp_path):
    path = get_log_path(data_dir=tmp_path)
    assert path == tmp_path / LOG_FILENAME


def test_get_log_path_string_dir(tmp_path):
    path = get_log_path(data_dir=str(tmp_path))
    assert path.name == LOG_FILENAME


def test_rotator_creates_gz_and_removes_source(tmp_path):
    src = tmp_path / "app.log"
    src.write_bytes(b"log content")
    dst = tmp_path / "app.log.1.gz"

    _rotator(str(src), str(dst))

    assert not src.exists()
    assert dst.exists()
    with gzip.open(dst, "rb") as f:
        assert f.read() == b"log content"


def test_setup_logging_creates_log_file(tmp_path):
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    try:
        setup_logging(data_dir=tmp_path)
        log_path = tmp_path / LOG_FILENAME
        # Writing one log message should flush the file into existence.
        logging.getLogger("test_logger").warning("hello from test")
        assert log_path.exists()
    finally:
        # Remove the handlers we just added so they don't pollute other tests.
        for h in list(root.handlers):
            if h not in handlers_before:
                root.removeHandler(h)
                h.close()
