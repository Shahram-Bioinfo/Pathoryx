"""Routing policy engine tables — Phase 4.8.

Creates routing schema with two tables:
  routing.routing_overrides   — temporary dashboard overrides
  routing.routing_decisions   — append-only audit trail

Revision ID: 0010
Revises:     0009
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS routing")

    op.create_table(
        "routing_overrides",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Text, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("target_type", sa.Text, nullable=False),
        sa.Column("target_value", sa.Text, nullable=False),
        sa.Column("destination", sa.Text, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean, server_default="true", nullable=False),
        schema="routing",
    )
    op.create_index(
        "ix_routing_overrides_active",
        "routing_overrides",
        ["is_active", "expires_at"],
        schema="routing",
    )
    op.create_index(
        "ix_routing_overrides_target",
        "routing_overrides",
        ["target_type", "target_value"],
        schema="routing",
    )

    op.create_table(
        "routing_decisions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("slide_id", sa.Text, nullable=True),
        sa.Column("scanner_id", sa.Text, nullable=True),
        sa.Column("mode", sa.Text, nullable=True),
        sa.Column("profile", sa.Text, nullable=True),
        sa.Column("color_dot", sa.Text, nullable=True),
        sa.Column("destination", sa.Text, nullable=False),
        sa.Column("routing_reason", sa.Text, nullable=False),
        sa.Column("override_id", sa.BigInteger, nullable=True),
        sa.Column("dry_run", sa.Boolean, server_default="true", nullable=False),
        schema="routing",
    )
    op.create_index(
        "ix_routing_decisions_scanner",
        "routing_decisions",
        ["scanner_id", "created_at"],
        schema="routing",
    )
    op.create_index(
        "ix_routing_decisions_mode",
        "routing_decisions",
        ["mode", "created_at"],
        schema="routing",
    )


def downgrade() -> None:
    op.drop_index("ix_routing_decisions_mode", table_name="routing_decisions", schema="routing")
    op.drop_index("ix_routing_decisions_scanner", table_name="routing_decisions", schema="routing")
    op.drop_table("routing_decisions", schema="routing")

    op.drop_index("ix_routing_overrides_target", table_name="routing_overrides", schema="routing")
    op.drop_index("ix_routing_overrides_active", table_name="routing_overrides", schema="routing")
    op.drop_table("routing_overrides", schema="routing")

    op.execute("DROP SCHEMA IF EXISTS routing")
