"""Enterprise initial schema — all schemas, tables, constraints, indexes.

Revision ID: 0001
Revises: (none)
Create Date: 2026-05-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Extensions ──────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")

    # ── Schemas ──────────────────────────────────────────────────────────────
    for schema in ("core", "events", "ops", "babelshark", "qc", "dicomizer", "uploader", "failed_watcher"):
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    # ── core.file_records ───────────────────────────────────────────────────
    op.create_table(
        "file_records",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("parent_artifact_id", sa.Text),
        sa.Column("source_service", sa.Text),
        sa.Column("source_artifact_id", sa.Text),
        sa.Column("artifact_type", sa.Text),
        sa.Column("original_filename", sa.Text),
        sa.Column("current_filename", sa.Text),
        sa.Column("original_path", sa.Text),
        sa.Column("current_file_path", sa.Text),
        sa.Column("canonical_path", sa.Text),
        sa.Column("file_format", sa.Text),
        sa.Column("file_size", sa.BigInteger),
        sa.Column("checksum_sha256", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column("current_snapshot_id", sa.BigInteger),
        sa.Column("metadata_json", postgresql.JSONB),
        sa.Column("input_metadata_json", postgresql.JSONB),
        sa.Column("output_metadata_json", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("canonical_path", name="uq_file_records_canonical_path"),
        sa.UniqueConstraint("source_service", "source_artifact_id", name="uq_file_records_source"),
        sa.UniqueConstraint("uuid", name="uq_file_records_uuid"),
        schema="core",
    )
    op.create_index("ix_file_records_global_artifact_id", "file_records", ["global_artifact_id"], schema="core")
    op.create_index("ix_file_records_status", "file_records", ["status"], schema="core")
    op.create_index("ix_file_records_original_filename", "file_records", ["original_filename"], schema="core")

    # Status check constraint — prevents invalid states from being written
    op.execute("""
        ALTER TABLE core.file_records ADD CONSTRAINT ck_file_records_status
        CHECK (status IS NULL OR status IN (
            'detected','intake_running','intake_registered',
            'qc_pending','qc_running','qc_passed','qc_failed',
            'dicom_pending','dicom_running','dicom_done','dicom_failed',
            'upload_pending','upload_running','uploaded','upload_failed',
            'manual_review','archived','discarded'
        ))
    """)

    # ── core.metadata_snapshots ─────────────────────────────────────────────
    op.create_table(
        "metadata_snapshots",
        sa.Column("snapshot_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("global_artifact_id", sa.Text, nullable=False),
        sa.Column("snapshot_version", sa.Integer, nullable=False),
        sa.Column("source_service", sa.Text, nullable=False),
        sa.Column("snapshot_payload", postgresql.JSONB, nullable=False),
        sa.Column("payload_hash", sa.Text, nullable=False),
        sa.Column("previous_snapshot_id", sa.BigInteger, sa.ForeignKey("core.metadata_snapshots.snapshot_id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("global_artifact_id", "snapshot_version", name="uq_metadata_snapshots_artifact_version"),
        schema="core",
    )
    op.create_index("ix_metadata_snapshots_artifact", "metadata_snapshots", ["global_artifact_id"], schema="core")

    # ── core.pipeline_runs ──────────────────────────────────────────────────
    op.create_table(
        "pipeline_runs",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("global_run_id", sa.Text),
        sa.Column("file_record_internal_id", sa.BigInteger, sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL")),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("service_name", sa.Text),
        sa.Column("pipeline_name", sa.Text),
        sa.Column("run_status", sa.Text),
        sa.Column("final_outcome", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.BigInteger),
        sa.Column("runner_id", sa.Text),
        sa.Column("host_id", sa.Text),
        sa.Column("correlation_id", sa.Text),
        sa.Column("otel_trace_id", sa.Text),
        sa.Column("otel_span_id", sa.Text),
        sa.Column("attempt_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("uuid", name="uq_pipeline_runs_uuid"),
        schema="core",
    )
    op.create_index("ix_pipeline_runs_global_run_id", "pipeline_runs", ["global_run_id"], schema="core")
    op.create_index("ix_pipeline_runs_status", "pipeline_runs", ["service_name", "run_status"], schema="core")

    # ── core.step_runs ──────────────────────────────────────────────────────
    op.create_table(
        "step_runs",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("pipeline_run_internal_id", sa.BigInteger, sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="CASCADE")),
        sa.Column("step_name", sa.Text),
        sa.Column("step_status", sa.Text),
        sa.Column("outcome", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.BigInteger),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text),
        sa.Column("error_type", sa.Text),
        sa.Column("context_json", postgresql.JSONB),
        sa.Column("cpu_percent_avg", sa.Float),
        sa.Column("memory_rss_mb", sa.Float),
        sa.Column("disk_read_mb", sa.Float),
        sa.Column("disk_write_mb", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="core",
    )
    op.create_index("ix_step_runs_run_step", "step_runs", ["pipeline_run_internal_id", "step_name"], schema="core")

    # ── core.service_trigger ────────────────────────────────────────────────
    op.create_table(
        "service_trigger",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("source_service", sa.Text, nullable=False),
        sa.Column("target_service", sa.Text, nullable=False),
        sa.Column("stage_name", sa.Text, nullable=False),
        sa.Column("file_record_internal_id", sa.BigInteger, sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL")),
        sa.Column("pipeline_run_internal_id", sa.BigInteger, sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("global_run_id", sa.Text),
        sa.Column("trigger_status", sa.Text),
        sa.Column("trigger_payload_json", postgresql.JSONB),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer, nullable=False, server_default="3"),
        sa.Column("error_message", sa.Text),
        sa.Column("triggered_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("correlation_id", sa.Text),
        sa.Column("otel_trace_id", sa.Text),
        sa.Column("claimed_by_runner_id", sa.Text),
        sa.Column("claimed_by_host_id", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint(
            "source_service", "target_service", "stage_name", "file_record_internal_id",
            name="uq_trigger_per_file_stage",
        ),
        schema="core",
    )
    # Partial index for dequeue — only indexes pending/failed rows
    op.execute("""
        CREATE INDEX ix_trigger_dequeue ON core.service_trigger
        (target_service, trigger_status, triggered_at)
        WHERE trigger_status IN ('pending', 'failed')
    """)
    op.create_index("ix_trigger_correlation", "service_trigger", ["correlation_id"], schema="core")

    # ── core.technical_metrics ──────────────────────────────────────────────
    op.create_table(
        "technical_metrics",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("file_record_internal_id", sa.BigInteger, sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL")),
        sa.Column("pipeline_run_internal_id", sa.BigInteger, sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")),
        sa.Column("step_run_internal_id", sa.BigInteger, sa.ForeignKey("core.step_runs.internal_id", ondelete="SET NULL")),
        sa.Column("service_name", sa.Text),
        sa.Column("stage_name", sa.Text),
        sa.Column("host_id", sa.Text),
        sa.Column("runner_id", sa.Text),
        sa.Column("pid", sa.Integer),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.BigInteger),
        sa.Column("service_version", sa.Text),
        sa.Column("correlation_id", sa.Text),
        sa.Column("cpu_percent_avg", sa.Float), sa.Column("cpu_percent_peak", sa.Float),
        sa.Column("cpu_time_user", sa.Float), sa.Column("cpu_time_system", sa.Float),
        sa.Column("memory_rss_mb", sa.Float), sa.Column("memory_peak_mb", sa.Float), sa.Column("memory_percent", sa.Float),
        sa.Column("disk_read_mb", sa.Float), sa.Column("disk_write_mb", sa.Float),
        sa.Column("read_count", sa.BigInteger), sa.Column("write_count", sa.BigInteger),
        sa.Column("input_file_size_bytes", sa.BigInteger), sa.Column("output_file_size_bytes", sa.BigInteger),
        sa.Column("gpu_name", sa.Text), sa.Column("gpu_index", sa.Integer),
        sa.Column("gpu_memory_allocated_mb", sa.Float), sa.Column("gpu_memory_reserved_mb", sa.Float),
        sa.Column("gpu_memory_peak_mb", sa.Float), sa.Column("gpu_utilization_percent", sa.Float),
        sa.Column("gpu_temperature_celsius", sa.Float),
        sa.Column("extra_metrics_json", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="core",
    )

    # ── core.runner_registrations ───────────────────────────────────────────
    op.create_table(
        "runner_registrations",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("runner_id", sa.Text, nullable=False),
        sa.Column("service_name", sa.Text, nullable=False),
        sa.Column("host_id", sa.Text, nullable=False),
        sa.Column("pid", sa.Integer, nullable=False),
        sa.Column("environment", sa.Text),
        sa.Column("service_version", sa.Text),
        sa.Column("config_hash", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shutdown_at", sa.DateTime(timezone=True)),
        sa.Column("capabilities_json", postgresql.JSONB),
        sa.UniqueConstraint("runner_id", name="uq_runner_registrations_runner_id"),
        schema="core",
    )

    # ── events.pipeline_events ──────────────────────────────────────────────
    op.create_table(
        "pipeline_events",
        sa.Column("event_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("event_schema_version", sa.Text, nullable=False, server_default="1.0.0"),
        sa.Column("event_version", sa.Integer, nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("aggregate_type", sa.Text, nullable=False),
        sa.Column("aggregate_id", sa.Text, nullable=False),
        sa.Column("file_record_internal_id", sa.BigInteger),
        sa.Column("pipeline_run_internal_id", sa.BigInteger),
        sa.Column("step_run_internal_id", sa.BigInteger),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("global_run_id", sa.Text),
        sa.Column("parent_artifact_id", sa.Text),
        sa.Column("service_name", sa.Text, nullable=False),
        sa.Column("runner_id", sa.Text),
        sa.Column("host_id", sa.Text),
        sa.Column("service_version", sa.Text),
        sa.Column("correlation_id", sa.Text),
        sa.Column("otel_trace_id", sa.Text),
        sa.Column("otel_span_id", sa.Text),
        sa.Column("caused_by_event_id", sa.BigInteger, sa.ForeignKey("events.pipeline_events.event_id", ondelete="RESTRICT")),
        sa.Column("event_payload", postgresql.JSONB, nullable=False),
        sa.Column("metadata_snapshot_id", sa.BigInteger),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_pipeline_events_idempotency"),
        schema="events",
    )
    op.create_index("ix_pipeline_events_aggregate", "pipeline_events", ["aggregate_type", "aggregate_id", "event_version"], schema="events")
    op.create_index("ix_pipeline_events_artifact", "pipeline_events", ["global_artifact_id", "event_type"], schema="events")
    op.create_index("ix_pipeline_events_correlation", "pipeline_events", ["correlation_id"], schema="events")
    op.create_index("ix_pipeline_events_time", "pipeline_events", ["occurred_at"], schema="events")

    # Revoke UPDATE/DELETE on pipeline_events from the app user
    # Replace 'pathoryx_user' with the actual DB username if different.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pathoryx_user') THEN
            REVOKE UPDATE, DELETE ON events.pipeline_events FROM pathoryx_user;
          END IF;
        END $$
    """)

    # ── ops tables ──────────────────────────────────────────────────────────
    op.create_table(
        "event_logs",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("service_name", sa.Text, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("file_record_internal_id", sa.BigInteger, sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL")),
        sa.Column("pipeline_run_internal_id", sa.BigInteger, sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")),
        sa.Column("step_run_internal_id", sa.BigInteger),
        sa.Column("global_run_id", sa.Text),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("correlation_id", sa.Text),
        sa.Column("runner_id", sa.Text),
        sa.Column("host_id", sa.Text),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="ops",
    )
    op.create_table(
        "error_logs",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("service_name", sa.Text, nullable=False),
        sa.Column("error_type", sa.Text),
        sa.Column("error_message", sa.Text),
        sa.Column("stack_trace", sa.Text),
        sa.Column("file_record_internal_id", sa.BigInteger, sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL")),
        sa.Column("pipeline_run_internal_id", sa.BigInteger),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("correlation_id", sa.Text),
        sa.Column("runner_id", sa.Text),
        sa.Column("host_id", sa.Text),
        sa.Column("context_json", postgresql.JSONB),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_note", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="ops",
    )

    # ── Service-specific tables ──────────────────────────────────────────────
    for schema, table, cols in [
        ("babelshark", "extraction_results", [
            sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("idempotency_key", sa.Text, nullable=False),
            sa.Column("file_record_internal_id", sa.BigInteger, sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL")),
            sa.Column("pipeline_run_internal_id", sa.BigInteger, sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")),
            sa.Column("global_run_id", sa.Text), sa.Column("global_artifact_id", sa.Text),
            sa.Column("correlation_id", sa.Text),
            sa.Column("intake_decision", sa.Text), sa.Column("action_taken", sa.Text),
            sa.Column("next_stage", sa.Text),
            sa.Column("scanner_id", sa.Text), sa.Column("scanner_model", sa.Text),
            sa.Column("scanner_vendor", sa.Text), sa.Column("slide_id", sa.Text),
            sa.Column("stain_type", sa.Text),
            sa.Column("requires_qc", sa.Boolean), sa.Column("has_internal_qc", sa.Boolean),
            sa.Column("extraction_status", sa.Text), sa.Column("extraction_duration_ms", sa.BigInteger),
            sa.Column("metadata_snapshot_id", sa.BigInteger),
            sa.Column("raw_extraction_payload", postgresql.JSONB),
            sa.Column("normalized_metadata", postgresql.JSONB),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("idempotency_key", name=f"uq_extraction_results_idempotency"),
        ]),
        ("qc", "qc_results", [
            sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("idempotency_key", sa.Text, nullable=False),
            sa.Column("file_record_internal_id", sa.BigInteger, sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL")),
            sa.Column("pipeline_run_internal_id", sa.BigInteger, sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")),
            sa.Column("step_run_internal_id", sa.BigInteger),
            sa.Column("global_run_id", sa.Text), sa.Column("global_artifact_id", sa.Text),
            sa.Column("correlation_id", sa.Text),
            sa.Column("qc_result", sa.Text), sa.Column("decision_status", sa.Text),
            sa.Column("decision_reason", sa.Text),
            sa.Column("blur_metrics", postgresql.JSONB), sa.Column("stain_metrics", postgresql.JSONB),
            sa.Column("penmark_metrics", postgresql.JSONB), sa.Column("bubble_metrics", postgresql.JSONB),
            sa.Column("sharpness_metrics", postgresql.JSONB),
            sa.Column("decision_threshold_json", postgresql.JSONB),
            sa.Column("final_routed_path", sa.Text),
            sa.Column("total_duration_seconds", sa.Float),
            sa.Column("model_versions_json", postgresql.JSONB),
            sa.Column("raw_qc_payload_json", postgresql.JSONB),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("idempotency_key", name="uq_qc_results_idempotency"),
        ]),
        ("dicomizer", "conversion_results", [
            sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("idempotency_key", sa.Text, nullable=False),
            sa.Column("file_record_internal_id", sa.BigInteger, sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL")),
            sa.Column("pipeline_run_internal_id", sa.BigInteger, sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")),
            sa.Column("global_run_id", sa.Text), sa.Column("global_artifact_id", sa.Text),
            sa.Column("correlation_id", sa.Text),
            sa.Column("source_path", sa.Text), sa.Column("output_path", sa.Text),
            sa.Column("output_format", sa.Text),
            sa.Column("conversion_status", sa.Text),
            sa.Column("was_already_dicom", sa.Boolean), sa.Column("conversion_required", sa.Boolean),
            sa.Column("conversion_tool", sa.Text), sa.Column("conversion_tool_version", sa.Text),
            sa.Column("input_file_size_bytes", sa.BigInteger), sa.Column("output_file_size_bytes", sa.BigInteger),
            sa.Column("input_checksum_sha256", sa.Text), sa.Column("output_checksum_sha256", sa.Text),
            sa.Column("duration_seconds", sa.Float),
            sa.Column("failure_context", postgresql.JSONB), sa.Column("metadata_summary", postgresql.JSONB),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("idempotency_key", name="uq_conversion_results_idempotency"),
        ]),
        ("uploader", "upload_results", [
            sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("idempotency_key", sa.Text, nullable=False),
            sa.Column("file_record_internal_id", sa.BigInteger, sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL")),
            sa.Column("pipeline_run_internal_id", sa.BigInteger, sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")),
            sa.Column("global_run_id", sa.Text), sa.Column("global_artifact_id", sa.Text),
            sa.Column("correlation_id", sa.Text),
            sa.Column("source_path", sa.Text), sa.Column("target_system", sa.Text),
            sa.Column("target_endpoint", sa.Text),
            sa.Column("upload_status", sa.Text), sa.Column("final_outcome", sa.Text),
            sa.Column("duration_seconds", sa.Float),
            sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("response_summary", postgresql.JSONB), sa.Column("failure_context", postgresql.JSONB),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("idempotency_key", name="uq_upload_results_idempotency"),
        ]),
    ]:
        op.create_table(table, *cols, schema=schema)

    # ── failed_watcher tables ────────────────────────────────────────────────
    op.create_table(
        "watched_folder_snapshots",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("folder_label", sa.Text, nullable=False),
        sa.Column("folder_path", sa.Text, nullable=False),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("file_size", sa.BigInteger),
        sa.Column("mtime_ns", sa.BigInteger),
        sa.Column("checksum_sha256", sa.Text),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("file_record_internal_id", sa.BigInteger, sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sidecar_metadata_json", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("folder_label", "file_path", name="uq_watched_folder_snapshots_path"),
        schema="failed_watcher",
    )
    op.create_table(
        "technician_changes",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("change_type", sa.Text, nullable=False),
        sa.Column("watch_folder_label", sa.Text, nullable=False),
        sa.Column("old_path", sa.Text), sa.Column("old_filename", sa.Text),
        sa.Column("old_file_size", sa.BigInteger), sa.Column("old_checksum_sha256", sa.Text),
        sa.Column("old_mtime_ns", sa.BigInteger),
        sa.Column("new_path", sa.Text), sa.Column("new_filename", sa.Text),
        sa.Column("new_file_size", sa.BigInteger), sa.Column("new_checksum_sha256", sa.Text),
        sa.Column("new_mtime_ns", sa.BigInteger),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("parent_artifact_id", sa.Text),
        sa.Column("file_record_internal_id", sa.BigInteger, sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL")),
        sa.Column("inferred_action", sa.Text),
        sa.Column("slide_id_inferred", sa.Text),
        sa.Column("sidecar_metadata_json", postgresql.JSONB),
        sa.Column("technician_notes", sa.Text),
        sa.Column("review_status", sa.Text, nullable=False, server_default="detected"),
        sa.Column("reviewed_by", sa.Text), sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_notes", sa.Text),
        sa.Column("requeue_trigger_id", sa.BigInteger, sa.ForeignKey("core.service_trigger.internal_id", ondelete="SET NULL")),
        sa.Column("requeued_at", sa.DateTime(timezone=True)),
        sa.Column("requires_approval", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("approved_by", sa.Text), sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.Text),
        sa.Column("runner_id", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_technician_changes_idempotency"),
        schema="failed_watcher",
    )
    op.create_index("ix_tc_artifact", "technician_changes", ["global_artifact_id"], schema="failed_watcher")
    op.create_index("ix_tc_status", "technician_changes", ["review_status"], schema="failed_watcher")
    op.create_index("ix_tc_detected", "technician_changes", ["detected_at"], schema="failed_watcher")


def downgrade() -> None:
    # Drop in reverse dependency order
    for schema, table in [
        ("failed_watcher", "technician_changes"),
        ("failed_watcher", "watched_folder_snapshots"),
        ("uploader", "upload_results"),
        ("dicomizer", "conversion_results"),
        ("qc", "qc_results"),
        ("babelshark", "extraction_results"),
        ("ops", "error_logs"),
        ("ops", "event_logs"),
        ("events", "pipeline_events"),
        ("core", "runner_registrations"),
        ("core", "technical_metrics"),
        ("core", "service_trigger"),
        ("core", "step_runs"),
        ("core", "pipeline_runs"),
        ("core", "metadata_snapshots"),
        ("core", "file_records"),
    ]:
        op.drop_table(table, schema=schema)

    for schema in ("failed_watcher", "uploader", "dicomizer", "qc", "babelshark", "ops", "events", "core"):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
