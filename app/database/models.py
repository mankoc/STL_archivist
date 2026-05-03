from datetime import datetime, timezone


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)

from sqlalchemy import (
    Column, Integer, String, Text, LargeBinary, DateTime,
    ForeignKey, Table, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# Many-to-many join table for model <-> tags
model_tags = Table(
    "model_tags",
    Base.metadata,
    Column("model_id", Integer, ForeignKey("models.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id",   Integer, ForeignKey("tags.id",   ondelete="CASCADE"), primary_key=True),
)


class Sculptor(Base):
    """An artist or creator associated with one or more STL models."""

    __tablename__ = "sculptors"

    id   = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), unique=True, nullable=False)

    models = relationship("STLModel", back_populates="sculptor")

    def __repr__(self) -> str:
        return f"<Sculptor {self.name!r}>"


class Collection(Base):
    """A top-level collection grouping (e.g. Movies, Comics, Games).

    Deleting a collection cascades to all its subcollections.
    """

    __tablename__ = "collections"

    id   = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False)

    subcollections = relationship("Subcollection", back_populates="collection", cascade="all, delete-orphan")
    models         = relationship("STLModel", back_populates="collection")

    def __repr__(self) -> str:
        return f"<Collection {self.name!r}>"


class Subcollection(Base):
    """A sub-division of a Collection (e.g. Marvel, DC under Comics).

    Names are unique per parent collection (``uq_subcollection_per_collection``).
    """

    __tablename__ = "subcollections"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    collection_id = Column(Integer, ForeignKey("collections.id", ondelete="CASCADE"), nullable=False)
    name          = Column(String(128), nullable=False)

    __table_args__ = (UniqueConstraint("collection_id", "name", name="uq_subcollection_per_collection"),)

    collection = relationship("Collection", back_populates="subcollections")
    models     = relationship("STLModel", back_populates="subcollection")

    def __repr__(self) -> str:
        return f"<Subcollection {self.name!r} (collection_id={self.collection_id})>"


class Category(Base):
    """A standalone category label (e.g. genre, content type)."""

    __tablename__ = "categories"

    id   = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False)

    models = relationship("STLModel", back_populates="category")

    def __repr__(self) -> str:
        return f"<Category {self.name!r}>"


class Release(Base):
    """A named release bundle that groups one or more STL models (e.g. a monthly Patreon drop)."""

    __tablename__ = "releases"

    id   = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), unique=True, nullable=False)

    models = relationship("STLModel", back_populates="release")

    def __repr__(self) -> str:
        return f"<Release {self.name!r}>"


class Scale(Base):
    """A miniature scale designation (e.g. ``28mm``, ``32mm``, ``1/35``)."""

    __tablename__ = "scales"

    id   = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), unique=True, nullable=False)

    models = relationship("STLModel", back_populates="scale")

    def __repr__(self) -> str:
        return f"<Scale {self.name!r}>"


class Creator(Base):
    """Extended profile for a model creator, keyed by their display name.

    The ``name`` column mirrors ``STLModel.creator`` and acts as the primary key
    so no FK column is needed on the models table.  All URL / name fields are
    optional; a row is created on first edit via the Creator editor dialog.
    """

    __tablename__ = "creators"

    name              = Column(String(256), primary_key=True)
    full_name         = Column(String(512), nullable=True)
    patreon_url       = Column(Text, nullable=True)
    artstation_url    = Column(Text, nullable=True)
    gumroad_url       = Column(Text, nullable=True)
    myminifactory_url = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Creator {self.name!r}>"


class STLModel(Base):
    """Central record for a single STL archive entry in the catalogue.

    Each row corresponds to exactly one archive file on disk (``archive_path``
    is unique).  The ``archive_hash`` fingerprint is recomputed on every crawl
    run; a changed hash triggers re-extraction of metadata and thumbnails.

    Nullable FK columns use ``SET NULL`` on delete so removing a sculptor,
    collection, etc. does not cascade-delete the model records.
    """

    __tablename__ = "models"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    title            = Column(String(512), index=True)
    creator          = Column(String(256), index=True)
    source           = Column(String(128), index=True)  # patreon / kickstarter / myminifactory / unknown
    release_year     = Column(Integer, nullable=True, index=True)
    release_month    = Column(Integer, nullable=True)   # 1-12, NULL when only year is known
    url              = Column(Text)
    description      = Column(Text)
    archive_path     = Column(Text, unique=True, nullable=False)
    archive_hash     = Column(String(64), nullable=False)
    sequence         = Column(Integer, nullable=True)               # position in a series, e.g. 3
    presupports      = Column(String(32), nullable=True)            # No / Lychee / Chitubox / Presupported
    warning          = Column(Text, nullable=True)                  # newline-separated warning strings, NULL if none
    created_at       = Column(DateTime, default=_utcnow)
    updated_at       = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    sculptor_id       = Column(Integer, ForeignKey("sculptors.id",     ondelete="SET NULL"), nullable=True, index=True)
    collection_id     = Column(Integer, ForeignKey("collections.id",   ondelete="SET NULL"), nullable=True, index=True)
    subcollection_id  = Column(Integer, ForeignKey("subcollections.id",ondelete="SET NULL"), nullable=True, index=True)
    category_id       = Column(Integer, ForeignKey("categories.id",    ondelete="SET NULL"), nullable=True, index=True)
    release_id        = Column(Integer, ForeignKey("releases.id",      ondelete="SET NULL"), nullable=True, index=True)
    scale_id          = Column(Integer, ForeignKey("scales.id",        ondelete="SET NULL"), nullable=True, index=True)

    tags          = relationship("Tag",          secondary=model_tags, back_populates="models")
    images        = relationship("Image",        back_populates="model", cascade="all, delete-orphan")
    stl_files     = relationship("STLFile",      back_populates="model", cascade="all, delete-orphan")
    sculptor      = relationship("Sculptor",     back_populates="models")
    collection    = relationship("Collection",   back_populates="models")
    subcollection = relationship("Subcollection",back_populates="models")
    category      = relationship("Category",     back_populates="models")
    release       = relationship("Release",      back_populates="models")
    scale         = relationship("Scale",        back_populates="models")

    def __repr__(self) -> str:
        return f"<STLModel id={self.id} title={self.title!r}>"


class Tag(Base):
    """A user-defined label that can be applied to many models (many-to-many)."""

    __tablename__ = "tags"

    id   = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False)

    models = relationship("STLModel", secondary=model_tags, back_populates="tags")

    def __repr__(self) -> str:
        return f"<Tag {self.name!r}>"


class Image(Base):
    """A preview image extracted from an archive and stored as a thumbnail.

    ``thumbnail_path`` holds the LMDB key (the image ID as a string).
    The ``thumbnail`` column is a legacy binary blob retained for backwards
    compatibility with pre-LMDB databases — it is NULL for all new records.
    """

    __tablename__ = "images"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    model_id       = Column(Integer, ForeignKey("models.id", ondelete="CASCADE"), nullable=False)
    filename       = Column(String(512))
    thumbnail_path = Column(String(512), nullable=True)
    thumbnail      = Column(LargeBinary, nullable=True)   # legacy blob – NULL after migration to LMDB
    dhash          = Column(Integer, nullable=True)      # 64-bit perceptual hash (dHash)

    model = relationship("STLModel", back_populates="images")


class STLFile(Base):
    """One STL file entry found inside a model's archive.

    Populated by the crawler when 'Store STL file list' is enabled.
    The composite primary key (model_id, filename) is unique per archive.
    ``file_size`` is the uncompressed byte count; ``archive_hash`` mirrors
    ``STLModel.archive_hash`` at the time of the last crawl.
    """

    __tablename__ = "stl_files"

    model_id     = Column(Integer, ForeignKey("models.id", ondelete="CASCADE"), primary_key=True)
    filename     = Column(String(512), primary_key=True)
    file_size    = Column(Integer, nullable=True)   # uncompressed bytes; NULL if unavailable
    archive_hash = Column(String(64), nullable=True)

    model = relationship("STLModel", back_populates="stl_files")
