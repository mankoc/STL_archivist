"""
Per-folder METADATA.7z sidecar files.

Each folder containing STL archives can carry a METADATA.7z that stores:
  - metadata.json  : DB record snapshot for every direct-child archive
  - thumbnails/    : JPEG thumbnails referenced by the JSON

This allows a library to be restored on a new machine without re-crawling
every archive: the crawler imports the sidecar first, then only re-extracts
archives whose hash has changed since the snapshot was written.

Public API
----------
export_folder_metadata(folder, session, thumb_base_dir)
    Write/update METADATA.7z for all direct-child archives in *folder*.

import_folder_metadata(folder, session, thumb_base_dir)
    Populate the DB from METADATA.7z; return list of imported archive_paths.

folder_has_db_records(folder, session)
    Return True if any direct-child archive of *folder* is already in the DB.
"""
import io
import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import py7zr

logger = logging.getLogger(__name__)

METADATA_FILENAME = "METADATA.7z"
_JSON_MEMBER      = "metadata.json"
_THUMBS_DIR       = "thumbnails"
_SCHEMA_VERSION   = 1


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def folder_has_db_records(folder: Path, session) -> bool:
    """Return True when at least one direct-child archive of *folder* is in DB."""
    from app.crawler.archive import ARCHIVE_SUFFIXES, METADATA_FILENAME as _MF
    from app.database.models import STLModel

    try:
        child_paths = [
            str(p)
            for p in folder.iterdir()
            if p.suffix.lower() in ARCHIVE_SUFFIXES and p.name != _MF
        ]
    except OSError:
        return False

    if not child_paths:
        return False

    return (
        session.query(STLModel.id)
        .filter(STLModel.archive_path.in_(child_paths))
        .first()
    ) is not None


def export_folder_metadata(
    folder: Path,
    session,
    thumb_base_dir: Path,
    include_thumbnails: bool = True,
) -> bool:
    """
    Create or replace METADATA.7z in *folder* with a snapshot of all
    direct-child archive records currently in the DB.

    Parameters
    ----------
    include_thumbnails:
        When True (default), thumbnail JPEG files are embedded in the archive
        and referenced from the JSON.  When False the ``images`` list in the
        JSON is cleared and no image files are written — useful to save disk
        space when thumbnails are not needed for restore.

    Returns True when the file was written, False when there was nothing to do
    (no matching DB records or a write error).
    """
    from app.database.models import STLModel

    folder = Path(folder)
    thumb_base_dir = Path(thumb_base_dir)

    # Collect all STLModel records whose archive file lives directly in folder
    all_models = (
        session.query(STLModel)
        .filter(STLModel.archive_path.like(str(folder) + "/%"))
        .all()
    )
    models = [m for m in all_models if Path(m.archive_path).parent == folder]

    if not models:
        logger.debug("export_folder_metadata: no records for %s — skipping", folder)
        return False

    logger.debug(
        "export_folder_metadata: exporting %d record(s) from %s (thumbnails=%s)",
        len(models), folder, include_thumbnails,
    )

    data = _serialize_models(models, thumb_base_dir, include_thumbnails=include_thumbnails)

    metadata_path = folder / METADATA_FILENAME
    try:
        _write_metadata_7z(
            metadata_path, data, models, thumb_base_dir,
            include_thumbnails=include_thumbnails,
        )
        logger.info("Wrote %s (%d model(s), thumbnails=%s)", metadata_path, len(models), include_thumbnails)
        return True
    except Exception as exc:
        logger.error("Failed to write %s: %s", metadata_path, exc, exc_info=True)
        return False


def import_folder_metadata(folder: Path, session, thumb_base_dir: Path) -> list[str]:
    """
    Read METADATA.7z from *folder* and populate the DB with any records whose
    archive file exists on disk but is not yet in the DB.

    Returns a list of archive_path strings that were newly inserted.
    """
    from app.database.models import STLModel, Image, Sculptor, Category, Subcategory, Release, Scale, Tag
    from app.utils.thumbnailer import save_thumbnail

    folder = Path(folder)
    metadata_path = folder / METADATA_FILENAME
    if not metadata_path.exists():
        return []

    logger.debug("import_folder_metadata: reading %s", metadata_path)

    try:
        data, thumb_bytes_map = _read_metadata_7z(metadata_path)
    except Exception as exc:
        logger.error("Failed to read %s: %s", metadata_path, exc, exc_info=True)
        return []

    version = data.get("version", 0)
    if version != _SCHEMA_VERSION:
        logger.warning("Unsupported METADATA.7z schema version %s in %s — skipping import", version, folder)
        return []

    imported: list[str] = []

    for model_data in data.get("models", []):
        archive_name = model_data.get("archive_name")
        if not archive_name:
            continue

        archive_path = folder / archive_name
        archive_str  = str(archive_path)

        # Skip if archive file doesn't exist on disk
        if not archive_path.exists():
            logger.debug("  import: archive not on disk, skipping — %s", archive_name)
            continue

        # Skip if already in DB (will be handled by normal hash-based upsert)
        if session.query(STLModel.id).filter_by(archive_path=archive_str).first():
            logger.debug("  import: already in DB, skipping — %s", archive_name)
            continue

        logger.debug("  import: restoring %s", archive_name)

        model = STLModel(
            archive_path  = archive_str,
            archive_hash  = model_data.get("archive_hash", ""),
            title         = model_data.get("title"),
            author        = model_data.get("author"),
            source        = model_data.get("source"),
            release_year  = model_data.get("release_year"),
            release_month = model_data.get("release_month"),
            url           = model_data.get("url"),
            description   = model_data.get("description"),
            sequence      = model_data.get("sequence"),
            presupports   = model_data.get("presupports"),
            warning       = model_data.get("warning"),
        )
        session.add(model)

        # FK relations — resolve/create by name
        _restore_fk(session, model, model_data)

        # Tags
        for tag_name in model_data.get("tags", []):
            tag_name = tag_name.strip().lower()
            if not tag_name:
                continue
            tag = session.query(Tag).filter_by(name=tag_name).first()
            if tag is None:
                tag = Tag(name=tag_name)
                session.add(tag)
                session.flush()
            model.tags.append(tag)

        session.flush()  # get model.id

        # Images / thumbnails
        for img_data in model_data.get("images", []):
            thumb_key = img_data.get("thumbnail", "")
            raw_bytes = thumb_bytes_map.get(thumb_key)
            if not raw_bytes:
                continue
            img = Image(model=model, filename=img_data.get("filename", ""))
            session.add(img)
            session.flush()  # get img.id
            rel_path = save_thumbnail(raw_bytes, img.id)
            if rel_path:
                img.thumbnail_path = rel_path
            else:
                session.delete(img)

        imported.append(archive_str)

    logger.debug("import_folder_metadata: imported %d record(s) from %s", len(imported), folder)
    return imported


# ---------------------------------------------------------------------------
# Internal serialisation helpers
# ---------------------------------------------------------------------------

def _serialize_models(models, thumb_base_dir: Path, include_thumbnails: bool = True) -> dict:
    """Build the metadata.json payload from a list of STLModel instances."""
    entries = []
    for m_idx, model in enumerate(models):
        images_list = []
        if include_thumbnails:
            for i_idx, img in enumerate(model.images):
                if img.thumbnail_path:
                    images_list.append({
                        "filename":  img.filename or "",
                        "thumbnail": f"{_THUMBS_DIR}/{m_idx}_{i_idx}.jpg",
                    })

        entries.append({
            "archive_name":  Path(model.archive_path).name,
            "archive_hash":  model.archive_hash or "",
            "title":         model.title,
            "author":        model.author,
            "source":        model.source,
            "release_year":  model.release_year,
            "release_month": model.release_month,
            "url":           model.url,
            "description":   model.description,
            "sequence":      model.sequence,
            "presupports":   model.presupports,
            "warning":       model.warning,
            "sculptor":      model.sculptor.name  if model.sculptor   else None,
            "category":      model.category.name  if model.category   else None,
            "subcategory":   model.subcategory.name if model.subcategory else None,
            "release":       model.release.name   if model.release    else None,
            "scale":         model.scale.name     if model.scale      else None,
            "tags":          [t.name for t in model.tags],
            "images":        images_list,
        })

    return {
        "version":      _SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models":       entries,
    }


def _write_metadata_7z(
    dest: Path,
    data: dict,
    models,
    thumb_base_dir: Path,
    include_thumbnails: bool = True,
) -> None:
    """
    Write *data* as metadata.json plus (optionally) thumbnails into a 7z archive
    at *dest*.  Uses a temp file + atomic rename to avoid leaving a corrupt
    archive on failure.
    """
    tmp_path = dest.with_suffix(".7z.tmp")

    try:
        with py7zr.SevenZipFile(str(tmp_path), mode="w") as zf:
            # Add metadata.json
            json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            zf.writef(io.BytesIO(json_bytes), _JSON_MEMBER)

            # Add thumbnails only when the option is enabled
            if include_thumbnails:
                for m_idx, model in enumerate(models):
                    for i_idx, img in enumerate(model.images):
                        if not img.thumbnail_path:
                            continue
                        thumb_file = thumb_base_dir / img.thumbnail_path
                        if not thumb_file.exists():
                            logger.debug("  thumbnail missing, skipping: %s", thumb_file)
                            continue
                        arc_name = f"{_THUMBS_DIR}/{m_idx}_{i_idx}.jpg"
                        zf.write(str(thumb_file), arc_name)

        # Atomic replace
        tmp_path.replace(dest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _read_metadata_7z(metadata_path: Path) -> tuple[dict, dict[str, bytes]]:
    """
    Extract metadata.json and all thumbnails from *metadata_path*.

    Returns:
        data           – parsed JSON dict
        thumb_bytes    – mapping of in-archive path → JPEG bytes
                         (e.g. ``{"thumbnails/0_0.jpg": b"..."}``
    """
    _shm = "/dev/shm" if Path("/dev/shm").is_dir() else None
    with tempfile.TemporaryDirectory(dir=_shm) as tmpdir:
        tmp = Path(tmpdir)
        with py7zr.SevenZipFile(str(metadata_path), mode="r") as zf:
            zf.extractall(path=tmpdir)

        # py7zr may preserve restrictive archive permissions — ensure files are readable
        for _f in tmp.rglob("*"):
            if _f.is_file():
                _f.chmod(_f.stat().st_mode | 0o644)

        # Read JSON
        json_file = tmp / _JSON_MEMBER
        if not json_file.exists():
            raise ValueError(f"metadata.json not found in {metadata_path}")
        data = json.loads(json_file.read_bytes())

        # Read thumbnails
        thumb_bytes: dict[str, bytes] = {}
        thumbs_dir = tmp / _THUMBS_DIR
        if thumbs_dir.is_dir():
            for f in thumbs_dir.iterdir():
                if f.is_file():
                    arc_name = f"{_THUMBS_DIR}/{f.name}"
                    thumb_bytes[arc_name] = f.read_bytes()

    return data, thumb_bytes


def _restore_fk(session, model, model_data: dict) -> None:
    """Resolve or create FK-backed entities and attach them to *model*."""
    from app.database.models import Sculptor, Category, Subcategory, Release, Scale

    sculptor_name = model_data.get("sculptor")
    if sculptor_name:
        sc = session.query(Sculptor).filter_by(name=sculptor_name).first()
        if sc is None:
            sc = Sculptor(name=sculptor_name)
            session.add(sc)
            session.flush()
        model.sculptor = sc

    category_name = model_data.get("category")
    if category_name:
        cat = session.query(Category).filter_by(name=category_name).first()
        if cat is None:
            cat = Category(name=category_name)
            session.add(cat)
            session.flush()
        model.category = cat

    subcategory_name = model_data.get("subcategory")
    if subcategory_name and model.category:
        sub = session.query(Subcategory).filter_by(
            name=subcategory_name, category_id=model.category.id
        ).first()
        if sub is None:
            sub = Subcategory(name=subcategory_name, category_id=model.category.id)
            session.add(sub)
            session.flush()
        model.subcategory = sub

    release_name = model_data.get("release")
    if release_name:
        rel = session.query(Release).filter_by(name=release_name).first()
        if rel is None:
            rel = Release(name=release_name)
            session.add(rel)
            session.flush()
        model.release = rel

    scale_name = model_data.get("scale")
    if scale_name:
        sc = session.query(Scale).filter_by(name=scale_name).first()
        if sc is None:
            sc = Scale(name=scale_name)
            session.add(sc)
            session.flush()
        model.scale = sc
