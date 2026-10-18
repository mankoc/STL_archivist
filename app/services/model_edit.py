"""
Model editing for the editor dialog, the AI tagger and the mass editor — Qt-free.

The dialogs used to query and write through the ORM themselves.  Each function
here runs natively (``stl_native.LibraryDb``, ``rust/stl-core/src/storage/model_edit.rs``)
when :func:`app.database.native_db.library` allows it, and in Python (the
dialogs' former code, moved) otherwise; ``tests/golden/test_model_edit_golden.py``
compares the two.  Each write runs in one transaction.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.database import native_db
from app.database.db import session_scope
from app.database.helpers import (
    LookupCache,
    get_or_create_category,
    get_or_create_collection,
    get_or_create_release,
    get_or_create_scale,
    get_or_create_sculptor,
    get_or_create_subcollection,
    get_or_create_tag,
)
from app.database.models import (
    Category, Collection, Release, Scale, Sculptor, STLModel, Subcollection, Tag, model_tags,
)
from app.services import editing
from app.utils.path_pattern import apply_extracted_to_model, format_release_date, parse_release_date

#: Fields the mass editor trims when asked to.
TRIMMABLE = ("title", "creator", "source", "url", "description")


def _sorted_names(q) -> list[str]:
    return sorted(filter(None, (v for (v,) in q)))


# ---------------------------------------------------------------------------
# Editor dialog
# ---------------------------------------------------------------------------

def editor_choices() -> dict[str, list[str]]:
    """Completion lists for the editor dialog."""
    lib = native_db.library()
    if lib is not None:
        return lib.edit_editor_choices()
    with session_scope() as s:
        return {
            "titles": _sorted_names(s.query(STLModel.title).distinct()),
            "creators": _sorted_names(s.query(STLModel.creator).distinct()),
            "sources": _sorted_names(s.query(STLModel.source).distinct()),
            "dates": sorted({format_release_date(y, mo) for (y, mo) in
                             s.query(STLModel.release_year, STLModel.release_month)
                             .filter(STLModel.release_year.isnot(None)).distinct()}),
            "tags": [n for (n,) in s.query(Tag.name).order_by(Tag.name)],
            "releases": sorted(n for (n,) in s.query(Release.name)),
            "sculptors": sorted(n for (n,) in s.query(Sculptor.name)),
            "collections": sorted(n for (n,) in s.query(Collection.name)),
            "subcollections": sorted(n for (n,) in s.query(Subcollection.name)),
            "categories": sorted(n for (n,) in s.query(Category.name)),
            "scales": sorted(n for (n,) in s.query(Scale.name)),
        }


def subcollection_names(collection_name: str) -> list[str]:
    """Subcollection names (sorted) of the named collection, or of all
    collections when the name is blank or unknown."""
    lib = native_db.library()
    if lib is not None:
        return lib.edit_subcollection_names(collection_name)
    with session_scope() as s:
        q = s.query(Subcollection.name).order_by(Subcollection.name, Subcollection.id)
        if collection_name.strip():
            col = (s.query(Collection).filter_by(name=collection_name.strip())
                   .order_by(Collection.id).first())
            if col:
                q = q.filter(Subcollection.collection_id == col.id)
        return [n for (n,) in q]


def model_for_editor(model_id: int) -> "dict | None":
    """The editor's view of one model; lists in id order (``images``:
    ``(image_id, has_thumbnail)``)."""
    lib = native_db.library()
    if lib is not None:
        return lib.edit_model_for_editor(int(model_id))
    with session_scope() as s:
        m = s.get(STLModel, model_id)
        if m is None:
            return None
        return {
            "archive_path": m.archive_path, "title": m.title, "creator": m.creator,
            "source": m.source, "release_year": m.release_year, "release_month": m.release_month,
            "release": m.release.name if m.release else None,
            "sculptor": m.sculptor.name if m.sculptor else None,
            "collection": m.collection.name if m.collection else None,
            "subcollection": m.subcollection.name if m.subcollection else None,
            "category": m.category.name if m.category else None,
            "sequence": m.sequence, "presupports": m.presupports, "url": m.url,
            "description": m.description,
            "scales": [x.name for x in sorted(m.scales, key=lambda x: x.id)],
            "tags": [t.name for t in sorted(m.tags, key=lambda t: t.id)],
            "images": [(i.id, bool(i.thumbnail_path)) for i in sorted(m.images, key=lambda i: i.id)],
        }


def save_model_editor(model_id: int, form: dict) -> "str | None":
    """Save the editor dialog's form; returns the archive's folder (for the
    sidecar refresh) or ``None`` when the model is gone.

    *form*: ``title``, ``creator``, ``source``, ``date``, ``presupports``,
    ``url``, ``description`` (strings), ``sequence`` (int, 0 = none),
    ``release``, ``sculptor``, ``collection``, ``subcollection``, ``category``
    (names, blank clears), ``scales`` and ``tags`` (lists of names).
    """
    lib = native_db.library()
    if lib is not None:
        try:
            path = lib.edit_save_model_editor(int(model_id), _editor_form(form))
            return str(Path(path).parent) if path else None
        except ValueError:
            pass            # input the native port leaves to Python (nothing written)
    with session_scope() as s:
        model = s.get(STLModel, model_id)
        if model is None:
            return None
        model.title = (form.get("title") or "").strip() or None
        model.creator = (form.get("creator") or "").strip() or None
        model.source = (form.get("source") or "").strip() or None
        model.release_year, model.release_month = parse_release_date((form.get("date") or "").strip())
        editing.assign_scales(s, model, form.get("scales") or [])
        model.presupports = (form.get("presupports") or "").strip() or None
        model.url = (form.get("url") or "").strip() or None
        model.description = (form.get("description") or "").strip() or None
        seq = int(form.get("sequence") or 0)
        model.sequence = seq if seq > 0 else None
        editing.set_release(s, model, form.get("release"))
        editing.set_sculptor(s, model, form.get("sculptor"))
        editing.set_collection(s, model, form.get("collection"))
        # A subcollection is only kept under a collection.
        if model.collection is not None:
            editing.set_subcollection(s, model, form.get("subcollection"))
        else:
            model.subcollection = None
        editing.set_category(s, model, form.get("category"))
        editing.assign_tags(s, model, form.get("tags") or [])
        return str(Path(model.archive_path).parent) if model.archive_path else None


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
    if lib is not None:
        return lib.edit_ai_model_inputs(int(model_id))
    with session_scope() as s:
        m = s.get(STLModel, model_id)
        if m is None:
            return None
        meta: list[tuple[str, str]] = []

        def add(label, value):
            if value not in (None, ""):
                meta.append((label, str(value).strip()))

        add("Title", m.title)
        add("Creator", m.creator)
        add("Sculptor", m.sculptor.name if m.sculptor else None)
        add("Category", m.category.name if m.category else None)
        add("Collection", m.collection.name if m.collection else None)
        add("Subcollection", m.subcollection.name if m.subcollection else None)
        scales = [x.name for x in sorted(m.scales, key=lambda x: x.id)]
        add("Scale", ", ".join(scales) if scales else None)
        add("Source", m.source)
        add("Year", m.release_year)
        add("Description", m.description)
        tags = [t.name for t in sorted(m.tags, key=lambda t: t.id)]
        return {
            "archive_path": m.archive_path or "", "title": m.title,
            "meta": meta, "tags": tags,
            "images": [(i.id, i.filename) for i in sorted(m.images, key=lambda i: i.id)],
            "files": [f.filename for f in sorted(m.stl_files, key=lambda f: f.filename) if f.filename],
        }


def ai_commit(model_id: int, fields: dict, tags: list[str], overwrite: bool) -> bool:
    """Write the AI tagger's suggestions.  Without *overwrite* only empty fields
    are filled; tags are always merged.  ``False`` when the model is gone.

    *fields*: ``title``, ``creator``, ``description``, ``source``, ``sculptor``,
    ``collection``, ``subcollection``, ``scale`` (strings, empty = no value)
    and ``year`` (int or ``None``).
    """
    lib = native_db.library()
    if lib is not None:
        f = {k: str(fields.get(k) or "") for k in ("title", "creator", "description", "source",
                                                  "sculptor", "collection", "subcollection", "scale")}
        return lib.edit_ai_commit(int(model_id), f, fields.get("year"), [str(t) for t in tags], bool(overwrite))
    with session_scope() as s:
        model = s.get(STLModel, model_id)
        if model is None:
            return False
        if fields["title"] and (overwrite or not model.title):
            model.title = fields["title"]
        if fields["creator"] and (overwrite or not model.creator):
            model.creator = fields["creator"]
        if fields["year"] is not None and (overwrite or model.release_year is None):
            model.release_year = fields["year"]
        if fields["description"] and (overwrite or not model.description):
            model.description = fields["description"]
        if fields["source"] and (overwrite or not model.source):
            model.source = fields["source"]
        if fields["sculptor"] and (overwrite or model.sculptor is None):
            editing.set_sculptor(s, model, fields["sculptor"])
        if fields["collection"] and (overwrite or model.collection is None):
            editing.set_collection(s, model, fields["collection"])
        # Subcollection is scoped to its parent collection, so only set it when
        # the model has a collection to hang it under.
        if (fields["subcollection"] and model.collection is not None
                and (overwrite or model.subcollection is None)):
            editing.set_subcollection(s, model, fields["subcollection"])
        if fields["scale"] and (overwrite or not model.scales):
            editing.assign_scales(s, model, editing.split_list(fields["scale"]), replace=overwrite)
        # Tags are always merged, never removed.
        editing.assign_tags(s, model, tags, replace=False)
    return True


# ---------------------------------------------------------------------------
# Mass editor
# ---------------------------------------------------------------------------

def mass_choices(model_ids: list[int]) -> dict[str, list[str]]:
    """Completion lists for the mass editor, scoped to the selection where the
    dialog always scoped them (dates, tags, releases, collections,
    subcollections), unless the selection is larger than 999 models."""
    lib = native_db.library()
    if lib is not None:
        return lib.edit_mass_choices([int(i) for i in model_ids])
    ids = list(model_ids)
    large = len(ids) > 999
    with session_scope() as s:
        out = {
            "sources": _sorted_names(s.query(STLModel.source).distinct()),
            "creators": _sorted_names(s.query(STLModel.creator).distinct()),
            "titles": _sorted_names(s.query(STLModel.title).distinct()),
        }
        dq = (s.query(STLModel.release_year, STLModel.release_month)
              .filter(STLModel.release_year.isnot(None)))
        if not large:
            dq = dq.filter(STLModel.id.in_(ids))
        out["dates"] = sorted({format_release_date(y, mo) for (y, mo) in dq.distinct()})
        if large:
            out["tags"] = sorted(n for (n,) in s.query(Tag.name))
        else:
            out["tags"] = sorted(n for (n,) in (
                s.query(Tag.name).join(model_tags, Tag.id == model_tags.c.tag_id)
                .filter(model_tags.c.model_id.in_(ids)).distinct()))

        def fk_ids(col):
            q = s.query(col).filter(col.isnot(None))
            if not large:
                q = q.filter(STLModel.id.in_(ids))
            return [v for (v,) in q.distinct()]

        for key, col, cls in (("releases", STLModel.release_id, Release),
                              ("collections", STLModel.collection_id, Collection),
                              ("subcollections", STLModel.subcollection_id, Subcollection)):
            used = fk_ids(col)
            q = s.query(cls.name)
            out[key] = sorted(filter(None, (n for (n,) in (q.filter(cls.id.in_(used)) if used else q))))
        out["categories"] = sorted(n for (n,) in s.query(Category.name))
        out["scales"] = sorted(n for (n,) in s.query(Scale.name))
        out["sculptors"] = sorted(n for (n,) in s.query(Sculptor.name))
        return out


def archive_paths(model_ids: list[int]) -> dict[int, str]:
    """``{model_id: archive_path}`` for the models that exist."""
    lib = native_db.library()
    if lib is not None:
        return lib.edit_archive_paths([int(i) for i in model_ids])
    with session_scope() as s:
        return {mid: ap or "" for mid, ap in
                s.query(STLModel.id, STLModel.archive_path).filter(STLModel.id.in_(list(model_ids)))}


def resolve_tokens(value: str, context: dict[str, str]) -> str:
    """Replace ``{token}`` with ``context[token]``; unknown tokens stay as written."""
    return re.sub(r"\{(\w+)\}", lambda m: context.get(m.group(1), m.group(0)), value)


def token_context(model, path_extracted: dict[str, str]) -> dict[str, str]:
    """Token values for one model (after the path's values were applied)."""
    ap = model.archive_path or ""
    ctx: dict[str, str] = {
        "creator":  model.creator or "",
        "source":   model.source or "",
        "title":    model.title or "",
        "date":     format_release_date(model.release_year, model.release_month),
        "path":     ap,
        "folder":   Path(ap).parent.name if ap else "",
        "filename": Path(ap).stem if ap else "",
    }
    ctx.update(path_extracted)
    return ctx


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
    find = plan.get("find")
    pattern = None
    if find:
        flags = 0 if find["case"] else re.IGNORECASE
        raw = find["find"] if find["regex"] else re.escape(find["find"])
        pattern = re.compile(raw, flags)
    lib = native_db.library()
    if lib is not None:
        try:
            lib.edit_mass_save(_native_plan(plan))
            return
        except ValueError:
            pass            # e.g. a regex find: Python's re semantics (nothing written)
    _mass_save_py(plan, pattern)


def _native_plan(plan: dict) -> dict:
    out = dict(plan)
    out["model_ids"] = [int(i) for i in plan["model_ids"]]
    out["path_results"] = {int(k): dict(v) for k, v in plan.get("path_results", {}).items()}
    out["clearing"] = sorted(plan.get("clearing", ()))
    out["manual"] = dict(plan.get("manual", {}))
    return out


def _mass_save_py(plan: dict, pattern) -> None:
    clearing = set(plan.get("clearing", ()))
    manual = plan.get("manual", {})
    scale_tmpl = plan.get("scale") or ""
    apply_scale = "scale" in clearing or bool(scale_tmpl)
    presupports = plan.get("presupports")
    apply_presupports = "presupports" in clearing or presupports is not None
    presupports_value = None if "presupports" in clearing else presupports
    release_tmpl = plan.get("release") or ""
    collection_name = plan.get("collection") or ""
    subcollection_name = plan.get("subcollection") or ""
    category_name = plan.get("category") or ""
    sculptor_name = plan.get("sculptor") or ""
    tags_tmpl = plan.get("tags") or ""
    apply_tags = "tags" in clearing or bool(tags_tmpl.strip())
    find = plan.get("find")
    target_fields = list(find["fields"]) if find else []
    replace_str = find["replace"] if find else ""
    cache: LookupCache = {}
    fnr_cache: LookupCache = {}
    with session_scope() as session:
        for mid in plan["model_ids"]:
            model = session.get(STLModel, mid)
            if model is None:
                continue
            path_extracted = plan.get("path_results", {}).get(mid, {})
            apply_extracted_to_model(session, model, path_extracted, overwrite=plan.get("overwrite", False))

            ctx = token_context(model, path_extracted)
            for attr, value in manual.items():
                if value is None:
                    if attr == "release_date":
                        model.release_year = None
                        model.release_month = None
                    else:
                        setattr(model, attr, None)
                else:
                    resolved = resolve_tokens(value, ctx)
                    if attr == "release_date":
                        model.release_year, model.release_month = parse_release_date(resolved or "")
                    else:
                        setattr(model, attr, resolved or None)

            if "release" in clearing:
                model.release = None
            elif release_tmpl:
                release_name = resolve_tokens(release_tmpl, ctx)
                if release_name:
                    model.release = get_or_create_release(session, release_name, cache=cache)

            if "collection" in clearing:
                model.collection = None
                model.subcollection = None
            elif collection_name:
                col = get_or_create_collection(session, collection_name, cache=cache)
                model.collection = col
                if "subcollection" in clearing:
                    model.subcollection = None
                elif subcollection_name:
                    model.subcollection = get_or_create_subcollection(
                        session, subcollection_name, col.id, cache=cache)

            if "category" in clearing:
                model.category = None
            elif category_name:
                model.category = get_or_create_category(session, category_name, cache=cache)

            if "sculptor" in clearing:
                model.sculptor = None
            elif sculptor_name:
                model.sculptor = get_or_create_sculptor(session, sculptor_name, cache=cache)

            if apply_scale:
                model.scales.clear()
                if "scale" not in clearing or scale_tmpl:
                    seen: set[str] = set()
                    for name in [x.strip() for x in scale_tmpl.split(",") if x.strip()]:
                        if name.lower() in seen:
                            continue
                        model.scales.append(get_or_create_scale(session, name, cache=cache))
                        seen.add(name.lower())

            if apply_presupports:
                model.presupports = presupports_value or None

            if apply_tags:
                if "tags" in clearing and not tags_tmpl.strip():
                    model.tags.clear()
                else:
                    resolved_tags = resolve_tokens(tags_tmpl, ctx)
                    tag_names = [t.strip() for t in resolved_tags.split(",") if t.strip()]
                    model.tags.clear()
                    for name in tag_names:
                        model.tags.append(get_or_create_tag(session, name, cache=cache))

            if pattern:
                for field in [f for f in target_fields if f not in ("collection", "subcollection")]:
                    old_val = getattr(model, field, None)
                    if not old_val:
                        continue
                    new_val = pattern.sub(replace_str, old_val)
                    if new_val != old_val:
                        setattr(model, field, new_val or None)
                if "collection" in target_fields and model.collection:
                    new_name = pattern.sub(replace_str, model.collection.name)
                    if new_name and new_name != model.collection.name:
                        model.collection = get_or_create_collection(session, new_name, cache=fnr_cache)
                if "subcollection" in target_fields and model.subcollection:
                    new_name = pattern.sub(replace_str, model.subcollection.name)
                    if new_name and new_name != model.subcollection.name:
                        parent_id = model.collection.id if model.collection else None
                        if parent_id is not None:
                            model.subcollection = get_or_create_subcollection(
                                session, new_name, parent_id, cache=fnr_cache)

            # Trim last, so values parsed from the path, typed above, or
            # produced by Find & Replace are all cleaned up too.
            if plan.get("trim"):
                for attr in TRIMMABLE:
                    value = getattr(model, attr, None)
                    if not isinstance(value, str):
                        continue
                    stripped = value.strip()
                    if stripped != value:
                        setattr(model, attr, stripped or None)
