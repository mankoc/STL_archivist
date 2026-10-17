"""Optional native (Rust) core: the ``stl_native`` module built from ``rust/stl-py``.

The module is an accelerator, never a requirement. Every function ported to
Rust keeps its Python implementation as a fallback, so the app runs the same
without the module, with a stale build, or with the module turned off.

Usage pattern for a ported function::

    from app import native

    def thing(args):
        mod = native.module()
        if mod is not None:
            try:
                return mod.thing(args)
            except (TypeError, ValueError, UnicodeError):
                pass                    # inputs the port does not accept
        return _thing_py(args)

``STL_NATIVE=0`` (or ``false`` / ``no`` / ``off``) disables the module, which
is how the golden tests compare both implementations and how a user can rule
the native code out when reporting a problem. Build it with
``python tools/build_native.py``; see ``docs/rust-migration-plan.md``.
"""
from __future__ import annotations

import logging
import os
from types import ModuleType
from typing import Optional

log = logging.getLogger(__name__)

_DISABLED_VALUES = {"0", "false", "no", "off"}

_module: Optional[ModuleType] = None
_reason = ""


def _load() -> None:
    global _module, _reason
    _module, _reason = None, ""
    if os.environ.get("STL_NATIVE", "").strip().lower() in _DISABLED_VALUES:
        _reason = "disabled by STL_NATIVE"
        return
    try:
        import stl_native
    except ImportError as exc:
        _reason = f"not installed ({exc})"
        return
    _module = stl_native


def module() -> Optional[ModuleType]:
    """The ``stl_native`` module, or ``None`` when unavailable or disabled."""
    return _module


def available() -> bool:
    return _module is not None


def status() -> str:
    """One line for logs and the About box: version in use, or why not."""
    if _module is not None:
        return f"native core {getattr(_module, '__version__', '?')}"
    return f"native core unavailable: {_reason}"


def reload() -> None:
    """Re-read ``STL_NATIVE`` and re-import; for tests."""
    _load()


_load()
