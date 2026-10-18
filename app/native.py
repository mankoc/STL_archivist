"""The native (Rust) core: the ``stl_native`` module built from ``rust/stl-py``.

Required since Phase 4h: the database, the crawler and the services run in
it.  Some functions keep a Python implementation for input the port refuses
(``ValueError`` and friends) — archive readers, Pillow thumbnails, rules with
non-portable patterns, metadata edge cases — and the native crawl asks for
those through its host (``app/crawler/crawler.py``).  Build the module with
``python tools/build_native.py``; see ``docs/rust-migration-plan.md``.
"""
from __future__ import annotations

import logging
from types import ModuleType
from typing import Optional

log = logging.getLogger(__name__)

_module: Optional[ModuleType] = None
_reason = ""


def _load() -> None:
    global _module, _reason
    _module, _reason = None, ""
    try:
        import stl_native
    except ImportError as exc:
        _reason = f"not installed ({exc}); build it with: python tools/build_native.py"
        return
    _module = stl_native


def module() -> ModuleType:
    """The ``stl_native`` module.  Raises ``RuntimeError`` when it is missing."""
    if _module is None:
        raise RuntimeError(f"The native core is required but unavailable: {_reason}")
    return _module


def available() -> bool:
    return _module is not None


def status() -> str:
    """One line for logs and the About box: version in use, or why not."""
    if _module is not None:
        return f"native core {getattr(_module, '__version__', '?')}"
    return f"native core unavailable: {_reason}"


def reload() -> None:
    """Re-import the module (tests)."""
    _load()


_load()
