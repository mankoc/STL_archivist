"""
Model editing for the editor dialog, the AI tagger and the mass editor — Qt-free
(native: ``rust/stl-core/src/storage/model_edit.rs``).  Each write runs in one
transaction.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.database import native_db

#: Fields the mass editor trims when asked to.
TRIMMABLE = ("title", "creator", "source", "url", "description")


# ---------------------------------------------------------------------------
# Editor dialog
# ---------------------------------------------------------------------------

def editor_choices() -> dict[str, list[str]]:
    """Completion lists for the editor dialog."""
    lib = native_db.library()
    return lib.edit_editor_choices()


def subcollection_names(collection_name: str) -> list[str]:
    """Subcollection names (sorted) of the named collection, or of all
    collections when the name is blank or unknown."""
    lib = native_db.library()
    return lib.edit_subcollection_names(collection_name)


def model_for_editor(model_id: int) -> "dict | None":
    """The editor's view of one model; lists in id order (``images``:
    ``(image_id, has_thumbnail)``)."""
    lib = native_db.library()
    return lib.edit_model_for_editor(int(model_id))


def save_model_editor(model_id: int, form: dict) -> "str | None":
    """Save the editor dialog's form; returns the archive's folder (for the
    sidecar refresh) or ``None`` when the model is gone.

    *form*: ``title``, ``creator``, ``source``, ``date``, ``presupports``,
    ``url``, ``description`` (strings), ``sequence`` (int, 0 = none),
    ``release``, ``sculptor``, ``collection``, ``subcollection``, ``category``
    (names, blank clears), ``scales`` and ``tags`` (lists of names).
    """
    path = native_db.library().edit_save_model_editor(int(model_id), _editor_form(form))
    return str(Path(path).parent) if path else None


def _editor_form(form: dict) -> dict:
    """The form with every key present and the right types, for the native call."""
    out = {k: str(form.get(k) or "") for k in ("title", "creator", "source", "date", "presupports",
                                               "url", "description", "release", "sculptor",
                                               "collection", "subcollection", "category")}
    out["sequence"] = int(form.get("sequence") or 0)
    out["scales"] = [str(x) for x in (form.get("scales") or [])]
    out["tags"] = [str(x) for x in (form.get("tags") or [])]
    return out


# ---------------------------------------------------------------------------
# AI tagger
# ---------------------------------------------------------------------------

def ai_model_inputs(model_id: int) -> "dict | None":
    """What the AI tagger shows and sends for a model: ``archive_path``,
    ``title``, known metadata (ordered ``(label, value)``), ``images``
    (``(image_id, filename)`` in id order) and ``files`` (STL file names)."""
    lib = native_db.library()
    return lib.edit_ai_model_inputs(int(model_id))


def ai_commit(model_id: int, fields: dict, tags: list[str], overwrite: bool) -> bool:
    """Write the AI tagger's suggestions.  Without *overwrite* only empty fields
    are filled; tags are always merged.  ``False`` when the model is gone.

    *fields*: ``title``, ``creator``, ``description``, ``source``, ``sculptor``,
    ``collection``, ``subcollection``, ``scale`` (strings, empty = no value)
    and ``year`` (int or ``None``).
    """
    lib = native_db.library()
    f = {k: str(fields.get(k) or "") for k in ("title", "creator", "description", "source",
                                              "sculptor", "collection", "subcollection", "scale")}
    return lib.edit_ai_commit(int(model_id), f, fields.get("year"), [str(t) for t in tags], bool(overwrite))


# ---------------------------------------------------------------------------
# Mass editor
# ---------------------------------------------------------------------------

def mass_choices(model_ids: list[int]) -> dict[str, list[str]]:
    """Completion lists for the mass editor, scoped to the selection where the
    dialog always scoped them (dates, tags, releases, collections,
    subcollections), unless the selection is larger than 999 models."""
    lib = native_db.library()
    return lib.edit_mass_choices([int(i) for i in model_ids])


def archive_paths(model_ids: list[int]) -> dict[int, str]:
    """``{model_id: archive_path}`` for the models that exist."""
    lib = native_db.library()
    return lib.edit_archive_paths([int(i) for i in model_ids])


def resolve_tokens(value: str, context: dict[str, str]) -> str:
    """Replace ``{token}`` with ``context[token]``; unknown tokens stay as written."""
    return re.sub(r"\{(\w+)\}", lambda m: context.get(m.group(1), m.group(0)), value)


def mass_save(plan: dict) -> None:
    """Apply the mass editor's changes to every selected model in one transaction.

    *plan* keys: ``model_ids``; ``path_results`` (``{model_id: extracted}``)
    and ``overwrite`` (path values replace existing ones); ``manual``
    (``{field: template | None}`` for source / creator / release_date / url /
    title, ``None`` clears); ``clearing`` (field names); ``scale`` /
    ``release`` / ``tags`` templates; ``collection`` / ``subcollection`` /
    ``category`` / ``sculptor`` names; ``presupports`` (``None`` = no change,
    ``""`` clears); ``find`` (``None`` or ``{"find", "replace", "case",
    "regex", "fields"}``); ``trim``.  Raises ``re.error`` for an invalid regex.
    """
    native_db.library().edit_mass_save(_native_plan(plan))


def _native_plan(plan: dict) -> dict:
    out = dict(plan)
    out["model_ids"] = [int(i) for i in plan["model_ids"]]
    out["path_results"] = {int(k): dict(v) for k, v in plan.get("path_results", {}).items()}
    out["clearing"] = sorted(plan.get("clearing", ()))
    out["manual"] = dict(plan.get("manual", {}))
    return out


