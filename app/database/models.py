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


class Category(Base):
    __tablename__ = "categories"

    id   = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False)  # e.g. Movies, Comics, Games

    subcategories = relationship("Subcategory", back_populates="category", cascade="all, delete-orphan")
    models        = relationship("STLModel", back_populates="category")

    def __repr__(self):
        return f"<Category {self.name!r}>"


class Subcategory(Base):
    __tablename__ = "subcategories"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    name        = Column(String(128), nullable=False)  # e.g. Marvel, DC, Star Wars

    __table_args__ = (UniqueConstraint("category_id", "name", name="uq_subcategory_per_category"),)

    category = relationship("Category", back_populates="subcategories")
    models   = relationship("STLModel", back_populates="subcategory")

    def __repr__(self):
        return f"<Subcategory {self.name!r} (category_id={self.category_id})>"


class Release(Base):
    __tablename__ = "releases"

    id   = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), unique=True, nullable=False)

    models = relationship("STLModel", back_populates="release")

    def __repr__(self):
        return f"<Release {self.name!r}>"


class STLModel(Base):
    __tablename__ = "models"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    title            = Column(String(512))
    author           = Column(String(256))
    source           = Column(String(128))          # patreon / kickstarter / myminifactory / unknown
    release_date     = Column(String(32))           # ISO date string, e.g. "2024-01"
    url              = Column(Text)
    description      = Column(Text)
    archive_path     = Column(Text, unique=True, nullable=False)
    archive_hash     = Column(String(64), nullable=False)
    sequence         = Column(Integer, nullable=True)               # position in a series, e.g. 3
    created_at       = Column(DateTime, default=_utcnow)
    updated_at       = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    sculptor_id      = Column(Integer, ForeignKey("sculptors.id", ondelete="SET NULL"), nullable=True)
    category_id      = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    subcategory_id   = Column(Integer, ForeignKey("subcategories.id", ondelete="SET NULL"), nullable=True)
    release_id       = Column(Integer, ForeignKey("releases.id", ondelete="SET NULL"), nullable=True)

    tags        = relationship("Tag",         secondary=model_tags, back_populates="models")
    images      = relationship("Image",       back_populates="model", cascade="all, delete-orphan")
    sculptor    = relationship("Sculptor",    back_populates="models")
    category    = relationship("Category",    back_populates="models")
    subcategory = relationship("Subcategory", back_populates="models")
    release     = relationship("Release",     back_populates="models")

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
