"""Shared SQLAlchemy metadata. Import concrete model modules here as they are added."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all persisted application models."""
