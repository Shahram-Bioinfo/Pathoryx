"""Add babelshark_failed and intake_failed to file_records status constraint.

Previously, slide_id_generator._status_for_final_route() wrote invalid values
('FINAL_ROUTE_FAILED', 'ROUTED_RESEARCH_ORIGINAL', etc.) that violated
ck_file_records_status. Those writes silently rolled back via SAVEPOINT,
leaving current_file_path pointing to the now-deleted staging path and
allowing the dispatch block to later set status=qc_pending and enqueue a
QC trigger for a file that was never routed to final/.

This migration extends the constraint to include the two new terminal
failure states so that failed-routed slides are correctly persisted.

Revision ID: 0006
Revises:     0005
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE core.file_records DROP CONSTRAINT IF EXISTS ck_file_records_status;
        ALTER TABLE core.file_records ADD CONSTRAINT ck_file_records_status
        CHECK (status IS NULL OR status IN (
            'detected','intake_running','intake_registered',
            'qc_pending','qc_running','qc_passed','qc_failed',
            'dicom_pending','dicom_running','dicom_done','dicom_failed',
            'upload_pending','upload_running','uploaded','upload_failed',
            'manual_review','archived','discarded',
            'babelshark_failed','intake_failed'
        ))
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE core.file_records DROP CONSTRAINT IF EXISTS ck_file_records_status;
        ALTER TABLE core.file_records ADD CONSTRAINT ck_file_records_status
        CHECK (status IS NULL OR status IN (
            'detected','intake_running','intake_registered',
            'qc_pending','qc_running','qc_passed','qc_failed',
            'dicom_pending','dicom_running','dicom_done','dicom_failed',
            'upload_pending','upload_running','uploaded','upload_failed',
            'manual_review','archived','discarded'
        ))
    """)
