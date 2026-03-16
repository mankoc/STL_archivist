from datetime import datetime

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


class STLModel(Base):
    __tablename__ = "models"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    title        = Column(String(512))
    author       = Column(String(256))
    type         = Column(String(128))          # patreon / kickstarter / myminifactory / unknown
    release_date = Column(String(32))           # ISO date string, e.g. "2024-01"
    url          = Column(Text)
    description  = Column(Text)
    archive_path = Column(Text, unique=True, nullable=False)
    archive_hash = Column(String(64), nullable=False)
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tags   = relationship("Tag",   secondary=model_tags, back_populates="models")
    images = relationship("Image", back_populates="model", cascade="all, delete-orphan")

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

    id        = Column(Integer, primary_key=True, autoincrement=True)
    model_id  = Column(Integer, ForeignKey("models.id", ondelete="CASCADE"), nullable=False)
    filename  = Column(String(512))
    thumbnail = Column(LargeBinary)   # small resized JPEG bytes

    model = relationship("STLModel", back_populates="images")
