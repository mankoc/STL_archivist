from datetime import datetime, timezone


def _utcnow():
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
    __tablename__ = "sculptors"

    id   = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), unique=True, nullable=False)

    models = relationship("STLModel", back_populates="sculptor")

    def __repr__(self):
        return f"<Sculptor {self.name!r}>"


class ModelType(Base):
    __tablename__ = "model_types"

    id   = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False)  # e.g. Movies, Comics, Games

    subtypes = relationship("Subtype", back_populates="model_type", cascade="all, delete-orphan")
    models   = relationship("STLModel", back_populates="type")

    def __repr__(self):
        return f"<ModelType {self.name!r}>"


class Subtype(Base):
    __tablename__ = "subtypes"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    model_type_id = Column(Integer, ForeignKey("model_types.id", ondelete="CASCADE"), nullable=False)
    name          = Column(String(128), nullable=False)  # e.g. Marvel, DC, Star Wars

    __table_args__ = (UniqueConstraint("model_type_id", "name", name="uq_subtype_per_type"),)

    model_type = relationship("ModelType", back_populates="subtypes")
    models     = relationship("STLModel", back_populates="subtype")

    def __repr__(self):
        return f"<Subtype {self.name!r} (type_id={self.model_type_id})>"


class Release(Base):
    __tablename__ = "releases"

    id   = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), unique=True, nullable=False)

    models = relationship("STLModel", back_populates="release")

    def __repr__(self):
        return f"<Release {self.name!r}>"


class STLModel(Base):
    __tablename__ = "models"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    title         = Column(String(512))
    author        = Column(String(256))
    source        = Column(String(128))          # patreon / kickstarter / myminifactory / unknown
    release_date  = Column(String(32))           # ISO date string, e.g. "2024-01"
    url           = Column(Text)
    description   = Column(Text)
    archive_path  = Column(Text, unique=True, nullable=False)
    archive_hash  = Column(String(64), nullable=False)
    sequence      = Column(Integer, nullable=True)               # position in a series, e.g. 3
    created_at    = Column(DateTime, default=_utcnow)
    updated_at    = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    sculptor_id   = Column(Integer, ForeignKey("sculptors.id", ondelete="SET NULL"), nullable=True)
    type_id       = Column(Integer, ForeignKey("model_types.id", ondelete="SET NULL"), nullable=True)
    subtype_id    = Column(Integer, ForeignKey("subtypes.id", ondelete="SET NULL"), nullable=True)
    release_id    = Column(Integer, ForeignKey("releases.id", ondelete="SET NULL"), nullable=True)

    tags     = relationship("Tag",      secondary=model_tags, back_populates="models")
    images   = relationship("Image",    back_populates="model", cascade="all, delete-orphan")
    sculptor = relationship("Sculptor", back_populates="models")
    type     = relationship("ModelType", back_populates="models")
    subtype  = relationship("Subtype",  back_populates="models")
    release  = relationship("Release",  back_populates="models")

    def __repr__(self):
        return f"<STLModel id={self.id} title={self.title!r}>"


class Tag(Base):
    __tablename__ = "tags"

    id   = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False)

    models = relationship("STLModel", secondary=model_tags, back_populates="tags")

    def __repr__(self):
        return f"<Tag {self.name!r}>"


class Image(Base):
    __tablename__ = "images"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    model_id       = Column(Integer, ForeignKey("models.id", ondelete="CASCADE"), nullable=False)
    filename       = Column(String(512))
    thumbnail_path = Column(String(512), nullable=True)   # relative path within thumbnails dir
    thumbnail      = Column(LargeBinary, nullable=True)   # legacy blob – NULL after migration

    model = relationship("STLModel", back_populates="images")
