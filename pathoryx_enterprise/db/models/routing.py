"""
SQLAlchemy models for the routing.* schema — Phase 4.8.

Two tables:
  routing.routing_overrides  — temporary dashboard-created overrides
  routing.routing_decisions  — audit trail (append-only; never UPDATE/DELETE)
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, ImmutableTimestampMixin, TimestampMixin


class RoutingOverride(Base, TimestampMixin):
    """
    Temporary routing override created via the dashboard.

    When is_active=True and now < expires_at, the engine applies this override
    ahead of all config-based rules (priority 1 — emergency override).
    """

    __tablename__ = "routing_overrides"
    __table_args__ = (
        Index("ix_routing_overrides_active", "is_active", "expires_at"),
        Index("ix_routing_overrides_target", "target_type", "target_value"),
        {"schema": "routing"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    created_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # "scanner" | "file" | "case"
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_value: Mapped[str] = mapped_column(Text, nullable=False)

    destination: Mapped[str] = mapped_column(Text, nullable=False)

    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )


class RoutingDecision(Base, ImmutableTimestampMixin):
    """
    Append-only audit record of every routing decision made by the engine.

    Stage 1: all rows have dry_run=True; destination is predicted but not applied.
    Stage 2 will introduce dry_run=False rows with live destinations.
    """

    __tablename__ = "routing_decisions"
    __table_args__ = (
        Index("ix_routing_decisions_scanner", "scanner_id", "created_at"),
        Index("ix_routing_decisions_mode", "mode", "created_at"),
        {"schema": "routing"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    slide_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scanner_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    mode: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    profile: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color_dot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color_dot_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    destination: Mapped[str] = mapped_column(Text, nullable=False)
    routing_reason: Mapped[str] = mapped_column(Text, nullable=False)

    override_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
