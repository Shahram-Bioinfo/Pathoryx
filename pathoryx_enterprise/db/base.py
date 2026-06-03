"""
SQLAlchemy declarative base and shared mixins.

All models inherit from Base. TimestampMixin adds timezone-aware
created_at / updated_at columns to every table.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Project-wide declarative base."""
    pass


class TimestampMixin:
    """
    Adds created_at and updated_at columns populated automatically by the DB.
    Use timezone=True so PostgreSQL stores TIMESTAMPTZ (not bare TIMESTAMP).
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ImmutableTimestampMixin:
    """
    For append-only / event-sourced tables: only created_at, no updated_at.
    The absence of updated_at makes it clear the row is never modified.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
