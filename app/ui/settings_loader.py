"""
Re-export of :func:`app.core.settings.build_crawler_config`.

Kept so existing callers (and tests that monkeypatch
``app.ui.settings_loader.build_crawler_config``) keep working. New code should
import from :mod:`app.core.settings` directly.
"""
from app.core.settings import build_crawler_config  # noqa: F401
