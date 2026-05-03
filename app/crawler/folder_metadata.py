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
from sqlalchemy.orm import Session

from app.database.helpers import (
    get_or_create_category,
    get_or_create_collection,
    get_or_create_release,
    get_or_create_scale,
    get_or_create_sculptor,
    get_or_create_subcollection,
    get_or_create_tag,
)

logger = logging.getLogger(__name__)

METADATA_FILENAME = "METADATA.7z"
_JSON_MEMBER      = "metadata.json"
_THUMBS_DIR       = "thumbnails"
_SCHEMA_VERSION   = 1


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def folder_has_db_records(folder: Path, session: Session) -> bool:
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
    session: Session,
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


def import_folder_metadata(folder: Path, session: Session, thumb_base_dir: Path) -> list[str]:
    """
    Read METADATA.7z from *folder* and populate the DB with any records whose
    archive file exists on disk but is not yet in the DB.

    Returns a list of archive_path strings that were newly inserted.
    """
    from app.database.models import STLModel, Image, STLFile
    from app.utils.thumbnailer import write_raw_thumbnail

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
            creator       = model_data.get("creator") or model_data.get("author"),
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
            if tag_name.strip():
                model.tags.append(get_or_create_tag(session, tag_name))

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
            # Thumbnails in METADATA.7z are already sized + JPEG-encoded; store
            # the bytes as-is rather than re-decoding through Pillow.
            rel_path = write_raw_thumbnail(img.id, raw_bytes)
            if rel_path:
                img.thumbnail_path = rel_path
            else:
                session.delete(img)

        # STL file list
        for sf_data in model_data.get("stl_files", []):
            fname = sf_data.get("filename")
            if fname:
                session.add(STLFile(
                    model=model,
                    filename=fname,
                    file_size=sf_data.get("file_size"),
                    archive_hash=sf_data.get("archive_hash"),
                ))

        imported.append(archive_str)

    logger.debug("import_folder_metadata: imported %d record(s) from %s", len(imported), folder)
    return imported


# ---------------------------------------------------------------------------
# Internal serialisation helpers
# ---------------------------------------------------------------------------

def _serialize_models(models: list, thumb_base_dir: Path, include_thumbnails: bool = True) -> dict:
    """Build the ``metadata.json`` payload dict from a list of ``STLModel`` instances.

    Args:
        models: List of ``STLModel`` ORM objects to serialise.
        thumb_base_dir: Base directory of the LMDB thumbnail store (currently
            unused in the payload itself; thumbnails are fetched from LMDB).
        include_thumbnails: When ``True``, each image entry includes a
            ``thumbnail`` key referencing its position in the sidecar archive.

    Returns:
        Dict ready for JSON serialisation, containing ``version``,
        ``generated_at``, and a ``models`` list.
    """
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
            "creator":       model.creator,
            "source":        model.source,
            "release_year":  model.release_year,
            "release_month": model.release_month,
            "url":           model.url,
            "description":   model.description,
            "sequence":      model.sequence,
            "presupports":   model.presupports,
            "warning":       model.warning,
            "sculptor":      model.sculptor.name     if model.sculptor     else None,
            "collection":    model.collection.name   if model.collection   else None,
            "subcollection": model.subcollection.name if model.subcollection else None,
            "category":      model.category.name     if model.category     else None,
            "release":       model.release.name      if model.release      else None,
            "scale":         model.scale.name        if model.scale        else None,
            "tags":          [t.name for t in model.tags],
            "images":        images_list,
            "stl_files":     [
                {
                    "filename":     sf.filename,
                    "file_size":    sf.file_size,
                    "archive_hash": sf.archive_hash,
                }
                for sf in model.stl_files
            ],
        })

    return {
        "version":      _SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models":       entries,
    }


def _write_metadata_7z(
    dest: Path,
    data: dict,
    models: list,
    thumb_base_dir: Path,
    include_thumbnails: bool = True,
) -> None:
    """Write *data* as ``metadata.json`` plus thumbnails into a 7z archive at *dest*.

    Uses a temp file + atomic rename so that a failure never leaves a corrupt
    or truncated sidecar archive at *dest*.

    Args:
        dest: Target path for the ``.7z`` sidecar file.
        data: Serialised metadata payload from ``_serialize_models()``.
        models: ``STLModel`` list (used to iterate ``Image`` records for thumbnails).
        thumb_base_dir: Unused — thumbnails are fetched from LMDB by image ID.
        include_thumbnails: When ``False``, the archive contains only
            ``metadata.json`` and no image files.
    """
    tmp_path = dest.with_suffix(".7z.tmp")

    try:
        with py7zr.SevenZipFile(str(tmp_path), mode="w") as zf:
            # Add metadata.json
            json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            zf.writef(io.BytesIO(json_bytes), _JSON_MEMBER)

            # Add thumbnails only when the option is enabled
            if include_thumbnails:
                from app.utils.thumbnailer import get_thumbnail_bytes
                for m_idx, model in enumerate(models):
                    for i_idx, img in enumerate(model.images):
                        if not img.thumbnail_path:
                            continue
                        thumb_bytes = get_thumbnail_bytes(img.id)
                        if not thumb_bytes:
                            logger.debug("  thumbnail missing in LMDB: image_id=%s", img.id)
                            continue
                        arc_name = f"{_THUMBS_DIR}/{m_idx}_{i_idx}.jpg"
                        zf.writef(io.BytesIO(thumb_bytes), arc_name)

        # Atomic replace
        tmp_path.replace(dest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _read_metadata_7z(metadata_path: Path) -> tuple[dict, dict[str, bytes]]:
    """Extract ``metadata.json`` and all thumbnails from a METADATA.7z sidecar.

    Extracts to a temporary directory (on ``/dev/shm`` when available for
    in-memory speed), then reads the JSON and all thumbnail files.

    Args:
        metadata_path: Path to the ``METADATA.7z`` sidecar file.

    Returns:
        A 2-tuple of:
        - ``data``: Parsed JSON dict from ``metadata.json``.
        - ``thumb_bytes``: Mapping of in-archive path to raw JPEG bytes,
          e.g. ``{"thumbnails/0_0.jpg": b"..."}``.

    Raises:
        ValueError: If ``metadata.json`` is not found inside the archive.
        Exception: Propagates any ``py7zr`` or I/O error to the caller.
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


def _restore_fk(session: Session, model, model_data: dict) -> None:
    """Resolve or create FK-backed lookup entities and attach them to *model*.

    For each named FK field (sculptor, category, subcategory, release, scale)
    the function looks up an existing record by name, creates one if it does not
    exist yet, and assigns the relationship on *model*.  Subcategory lookup is
    scoped to the already-resolved category.

    Args:
        session: Active SQLAlchemy session.
        model: The ``STLModel`` being populated.
        model_data: Deserialised dict for this archive from ``metadata.json``.
    """
    sculptor_name = model_data.get("sculptor")
    if sculptor_name:
        model.sculptor = get_or_create_sculptor(session, sculptor_name)

    # "collection" key is new; fall back to "category" for old sidecar files
    collection_name = model_data.get("collection") or model_data.get("category")
    if collection_name:
        model.collection = get_or_create_collection(session, collection_name)

    # "subcollection" key is new; fall back to "subcategory" for old sidecar files
    subcollection_name = model_data.get("subcollection") or model_data.get("subcategory")
    if subcollection_name and model.collection:
        model.subcollection = get_or_create_subcollection(
            session, subcollection_name, model.collection.id,
        )

    category_name = model_data.get("category") if "collection" in model_data else None
    if category_name:
        model.category = get_or_create_category(session, category_name)

    release_name = model_data.get("release")
    if release_name:
        model.release = get_or_create_release(session, release_name)

    scale_name = model_data.get("scale")
    if scale_name:
        model.scale = get_or_create_scale(session, scale_name)
