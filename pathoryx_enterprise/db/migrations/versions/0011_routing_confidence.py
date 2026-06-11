"""
Add color_dot_confidence to routing.routing_decisions.

Phase 4.8B — real pipeline decisions include color marker confidence
extracted from babelshark.color_marker_results.raw_payload.

Revision ID: 0011
Revises:     0010
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "routing_decisions",
        sa.Column("color_dot_confidence", sa.Float(), nullable=True),
        schema="routing",
    )


def downgrade() -> None:
    op.drop_column("routing_decisions", "color_dot_confidence", schema="routing")
