"""
Internationalisation (i18n) support.

Translations live as Qt Linguist ``.ts`` files in ``resources/i18n/`` — one per
language (``en.ts``, ``de.ts``, …). These files are the editable *language
definitions*: each ``<message>`` maps a ``<source>`` English string to its
``<translation>`` for that language. New languages are added simply by copying
``en.ts`` and translating the ``<translation>`` values (Qt Linguist can open
them directly).

Normally Qt loads compiled ``.qm`` binaries via :class:`QTranslator`, which
requires the ``lrelease`` tool. To avoid that build step we parse the ``.ts``
XML ourselves at runtime and feed it to a :class:`QTranslator` subclass whose
virtual ``translate()`` is overridden — ``QObject.tr()`` and
``QCoreApplication.translate()`` route through it transparently. Strings with no
entry (or an empty/unfinished translation) fall back to the English source.

Application code marks translatable strings the normal Qt way::

    self.tr("Configuration")                       # inside QObject subclasses
    QCoreApplication.translate("ctx", "Open Folder")  # free functions / module scope

``pylupdate6`` scans the source for those calls to (re)generate the ``.ts``
files; see ``tools``/the project docs for the regeneration command.
"""
from __future__ import annotations

import logging
from pathlib import Path
from xml.etree import ElementTree as ET

from PyQt6.QtCore import QCoreApplication, QTranslator

log = logging.getLogger(__name__)

# Directory holding the <code>.ts language-definition files.
LANG_DIR = Path(__file__).parent.parent.parent / "resources" / "i18n"

DEFAULT_LANGUAGE = "en"

# Human-readable names for known language codes. Codes without an entry fall
# back to their uppercased code, so adding a new <code>.ts file is enough to
# make it selectable even before it is listed here.
_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "de": "Deutsch",
    "es": "Español",
    "fr": "Français",
    "it": "Italiano",
    "pt": "Português",
    "nl": "Nederlands",
    "pl": "Polski",
    "ru": "Русский",
    "ja": "日本語",
    "zh": "中文",
}

# The currently installed translator (kept so it can be removed on switch).
_active_translator: QTranslator | None = None
_active_language: str = DEFAULT_LANGUAGE


def language_display_name(code: str) -> str:
    """Return a human-readable name for a language code (e.g. ``"de"`` → ``"Deutsch"``)."""
    return _LANGUAGE_NAMES.get(code, code.upper())


def available_languages() -> list[tuple[str, str]]:
    """Return ``(code, display_name)`` pairs for every available language.

    English is always present (it is the source language and needs no file);
    any other ``<code>.ts`` file found in :data:`LANG_DIR` is added too.
    """
    codes = {DEFAULT_LANGUAGE}
    if LANG_DIR.is_dir():
        for ts in LANG_DIR.glob("*.ts"):
            codes.add(ts.stem)
    pairs = [(c, language_display_name(c)) for c in codes]
    pairs.sort(key=lambda p: (p[0] != DEFAULT_LANGUAGE, p[1].lower()))
    return pairs


def current_language() -> str:
    """Return the code of the currently installed language."""
    return _active_language


def _parse_ts(path: Path) -> dict[tuple[str, str], str]:
    """Parse a Qt ``.ts`` file into ``{(context, source): translation}``.

    Messages whose translation is empty or marked ``type="unfinished"`` are
    skipped so they fall back to the English source text at runtime.
    """
    mapping: dict[tuple[str, str], str] = {}
    tree = ET.parse(path)
    for context in tree.getroot().findall("context"):
        name_el = context.find("name")
        ctx = name_el.text or "" if name_el is not None else ""
        for message in context.findall("message"):
            source_el = message.find("source")
            trans_el = message.find("translation")
            if source_el is None or source_el.text is None:
                continue
            if trans_el is None:
                continue
            if trans_el.get("type") == "unfinished":
                continue
            text = trans_el.text
            if not text:
                continue
            mapping[(ctx, source_el.text)] = text
    return mapping


class _TsTranslator(QTranslator):
    """A :class:`QTranslator` backed by an in-memory ``.ts`` mapping."""

    def __init__(self, mapping: dict[tuple[str, str], str]) -> None:
        super().__init__()
        self._mapping = mapping

    def translate(self, context, sourceText, disambiguation=None, n=-1):  # noqa: N802 (Qt override)
        # Return None for unknown strings so Qt falls back to the source
        # (English) text. Returning an empty string would instead blank the
        # label, so a missing/unfinished entry must never map to "".
        return self._mapping.get((context, sourceText))

    def isEmpty(self) -> bool:  # noqa: N802 (Qt override)
        return not self._mapping


def install_language(code: str | None) -> bool:
    """Install the translator for *code* on the running ``QCoreApplication``.

    Removes any previously installed translator first. English (the source
    language) needs no translator; selecting it simply uninstalls translations.
    Returns ``True`` if a translation file was loaded and installed.
    """
    global _active_translator, _active_language

    app = QCoreApplication.instance()
    if app is None:
        log.warning("install_language(%r) called before QApplication exists", code)
        return False

    code = code or DEFAULT_LANGUAGE

    if _active_translator is not None:
        app.removeTranslator(_active_translator)
        _active_translator = None

    _active_language = code

    if code == DEFAULT_LANGUAGE:
        # Source strings are already English — nothing to install.
        return False

    ts_path = LANG_DIR / f"{code}.ts"
    if not ts_path.exists():
        log.warning("Language file not found: %s — falling back to English", ts_path)
        _active_language = DEFAULT_LANGUAGE
        return False

    try:
        mapping = _parse_ts(ts_path)
    except (ET.ParseError, OSError) as exc:
        log.error("Failed to parse language file %s: %s", ts_path, exc)
        _active_language = DEFAULT_LANGUAGE
        return False

    translator = _TsTranslator(mapping)
    if not app.installTranslator(translator):
        log.warning("Qt refused to install translator for %s", code)
        return False

    _active_translator = translator
    log.info("Installed language '%s' (%d strings)", code, len(mapping))
    return True
