"""Import-level guard for the browser UI modules.

The browser was split from one ~4,300-line file into several ``browser_*``
modules. UI behavior isn't unit-tested, but importing every module catches
import-time breakage (missing/circular imports, stray references) cheaply.
Importing PyQt6 modules does not require a running QApplication.
"""
import importlib

import pytest

pytest.importorskip("PyQt6")

_MODULES = [
    "app.ui.browser_common",
    "app.ui.browser_dialogs",
    "app.ui.browser_delegates",
    "app.ui.browser_table_model",
    "app.ui.browser_filter_tree",
    "app.ui.browser_jobs",
    "app.ui.browser_tab",
    "app.ui.main_window",
]


@pytest.mark.parametrize("module", _MODULES)
def test_module_imports(module):
    assert importlib.import_module(module) is not None


def test_browser_tab_exports_browsertab():
    from app.ui.browser_tab import BrowserTab
    from PyQt6.QtWidgets import QWidget
    assert issubclass(BrowserTab, QWidget)
