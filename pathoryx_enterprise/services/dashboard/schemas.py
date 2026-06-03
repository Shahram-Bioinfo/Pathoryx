"""Pydantic response schemas for all dashboard API endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


class SlideStatusCounts(BaseModel):
    total: int
    by_status: dict[str, int]


class TriggerStatusCounts(BaseModel):
    pending: int
    running: int
    failed: int
    completed: int


class RunnerStatusCounts(BaseModel):
    active: int
    stale: int
    other: dict[str, int]


class OverviewResponse(BaseModel):
    as_of: datetime
    slides: SlideStatusCounts
    triggers: TriggerStatusCounts
    runners: RunnerStatusCounts
    events_last_24h: int


# ---------------------------------------------------------------------------
# Slides
# ---------------------------------------------------------------------------


class SlideItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    internal_id: int
    global_artifact_id: Optional[str] = None
    original_filename: Optional[str] = None
    current_filename: Optional[str] = None
    status: Optional[str] = None
    file_size: Optional[int] = None
    file_format: Optional[str] = None
    scanner_id: Optional[str] = None
    scanner_name: Optional[str] = None
    artifact_type: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    original_path: Optional[str] = None
    current_file_path: Optional[str] = None


class SlideListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SlideItem]


# ---------------------------------------------------------------------------
# Slide detail
# ---------------------------------------------------------------------------


class QCResultSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    internal_id: int
    qc_result: Optional[str] = None
    decision_status: Optional[str] = None
    decision_reason: Optional[str] = None
    error_reason: Optional[str] = None
    qc_context: Optional[str] = None
    total_duration_seconds: Optional[float] = None
    processed_at: Optional[datetime] = None
    source_path: Optional[str] = None
    scanner_name: Optional[str] = None
    scanner_id: Optional[str] = None
    trust_scanner_qc: Optional[bool] = None
    input_mode: Optional[str] = None
    next_service: Optional[str] = None
    next_stage: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ConversionResultSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    internal_id: int
    conversion_status: Optional[str] = None
    output_format: Optional[str] = None
    was_already_dicom: Optional[bool] = None
    conversion_tool: Optional[str] = None
    input_file_size_bytes: Optional[int] = None
    output_file_size_bytes: Optional[int] = None
    duration_seconds: Optional[float] = None
    processed_at: Optional[datetime] = None
    source_path: Optional[str] = None
    output_path: Optional[str] = None


class UploadResultSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    internal_id: int
    upload_status: Optional[str] = None
    target_system: Optional[str] = None
    final_outcome: Optional[str] = None
    duration_seconds: Optional[float] = None
    retry_count: int = 0
    processed_at: Optional[datetime] = None
    source_path: Optional[str] = None
    target_endpoint: Optional[str] = None
    upload_method: Optional[str] = None


class EventItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: int
    event_type: str
    global_artifact_id: Optional[str] = None
    global_run_id: Optional[str] = None
    service_name: str
    occurred_at: datetime
    aggregate_type: str
    aggregate_id: str
    event_payload: Optional[dict] = None


class TriggerItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    internal_id: int
    source_service: str
    target_service: str
    stage_name: str
    trigger_status: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    error_message: Optional[str] = None
    triggered_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    correlation_id: Optional[str] = None


class RecoveryEventItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    internal_id: int
    change_type: str
    watch_folder_label: str
    old_path: Optional[str] = None
    new_path: Optional[str] = None
    inferred_action: Optional[str] = None
    review_status: str
    detected_at: datetime
    case_id: Optional[str] = None
    recovery_outcome: Optional[str] = None
    recovery_reason: Optional[str] = None
    recovery_destination_path: Optional[str] = None
    recovered_at: Optional[datetime] = None
    timestamp_in_filename: bool = False
    timestamp_extracted_from_wsi: bool = False
    requeued_at: Optional[datetime] = None


class ExtractionResultSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    internal_id: int
    slide_id: Optional[str] = None
    stain_type: Optional[str] = None
    scanner_id: Optional[str] = None
    scanner_model: Optional[str] = None
    scanner_vendor: Optional[str] = None
    intake_decision: Optional[str] = None
    action_taken: Optional[str] = None
    next_stage: Optional[str] = None
    extraction_status: Optional[str] = None
    requires_qc: Optional[bool] = None
    has_internal_qc: Optional[bool] = None


class SlideDetailResponse(BaseModel):
    file_record: SlideItem
    qc_result: Optional[QCResultSummary] = None
    conversion_result: Optional[ConversionResultSummary] = None
    upload_result: Optional[UploadResultSummary] = None
    recent_events: list[EventItem]
    triggers: list[TriggerItem] = []
    recovery_events: list[RecoveryEventItem] = []
    extraction_result: Optional[ExtractionResultSummary] = None


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class EventListResponse(BaseModel):
    items: list[EventItem]
    count: int


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------


class QueueRow(BaseModel):
    target_service: str
    pending: int
    running: int
    failed: int
    completed: int


class QueueStatusResponse(BaseModel):
    queues: list[QueueRow]
    total_pending: int
    total_failed: int


# ---------------------------------------------------------------------------
# Recovery (failed_watcher / RecoverySentry)
# ---------------------------------------------------------------------------


class RecoveryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    internal_id: int
    change_type: str
    watch_folder_label: str
    global_artifact_id: Optional[str] = None
    review_status: str
    detected_at: datetime
    inferred_action: Optional[str] = None
    recovery_outcome: Optional[str] = None
    recovery_reason: Optional[str] = None
    recovered_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class RecoveryResponse(BaseModel):
    items: list[RecoveryItem]
    total: int
    # Accurate per-status counts from a separate GROUP BY query.
    # Populated regardless of the active review_status filter so the
    # dashboard can display correct summary totals at all times.
    by_status: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


class FailedSlideItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    internal_id: int
    global_artifact_id: Optional[str] = None
    original_filename: Optional[str] = None
    status: Optional[str] = None
    updated_at: Optional[datetime] = None
    # Enriched for Incident Command view — read directly from FileRecord columns
    scanner_name: Optional[str] = None
    scanner_id: Optional[str] = None
    current_file_path: Optional[str] = None
    file_format: Optional[str] = None


class FailedTriggerItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    internal_id: int
    source_service: str
    target_service: str
    stage_name: str
    global_artifact_id: Optional[str] = None
    trigger_status: Optional[str] = None
    retry_count: int
    max_retries: int = 3
    error_message: Optional[str] = None
    triggered_at: Optional[datetime] = None
    # Timing chain — used for forensic timeline in expanded rows
    accepted_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # Trigger payload for incident inspection
    trigger_payload_json: Optional[dict] = None


class FailuresResponse(BaseModel):
    failed_slides: list[FailedSlideItem]
    failed_triggers: list[FailedTriggerItem]
    # Subset of artifact IDs that have at least one TechnicianChange record.
    # Computed with a single IN query so the UI can show a recovery indicator
    # without loading all recovery detail.
    artifact_ids_with_recovery: list[str] = []


# ---------------------------------------------------------------------------
# Recovery — watched folder stats
# ---------------------------------------------------------------------------


class WatchFolderSummary(BaseModel):
    label: str
    path: Optional[str] = None
    total_files: int = 0
    recently_changed: int = 0
    awaiting_review: int = 0
    auto_recovered: int = 0
    last_scan_time: Optional[datetime] = None


class WatchFoldersResponse(BaseModel):
    folders: list[WatchFolderSummary]
    as_of: datetime


# ---------------------------------------------------------------------------
# Recovery — monitored file listing (WatchedFolderSnapshot + latest TC)
# ---------------------------------------------------------------------------


class MonitoredFileItem(BaseModel):
    file_id: int
    filename: str
    file_path: str
    folder_label: str
    folder_path: Optional[str] = None
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    file_size: Optional[int] = None
    slide_id: Optional[str] = None
    case_id: Optional[str] = None
    extension: Optional[str] = None
    global_artifact_id: Optional[str] = None
    file_record_internal_id: Optional[int] = None
    # Enriched from latest TechnicianChange (None when no change recorded)
    change_id: Optional[int] = None
    change_type: Optional[str] = None
    review_status: Optional[str] = None
    recovery_outcome: Optional[str] = None
    recovery_reason: Optional[str] = None
    detected_at: Optional[datetime] = None
    inferred_action: Optional[str] = None


class MonitoredFilesResponse(BaseModel):
    total: int
    items: list[MonitoredFileItem]


# ---------------------------------------------------------------------------
# Recovery — technician rename action
# ---------------------------------------------------------------------------


class TechnicianRenameRequest(BaseModel):
    proposed_filename: str
    technician_note: Optional[str] = None
    confirm: bool = False


class TechnicianRenameResponse(BaseModel):
    outcome: str  # auto_recovered | manual_review_required | validation_failed
    reason: Optional[str] = None
    destination_path: Optional[str] = None
    final_filename: Optional[str] = None
    case_id: Optional[str] = None
    slide_id: Optional[str] = None
    change_id: Optional[int] = None
    validation_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Recovery — structured filename validation
# ---------------------------------------------------------------------------


class ValidationComponent(BaseModel):
    case_id: Optional[str] = None
    pot: Optional[str] = None
    block: Optional[str] = None
    section: Optional[str] = None
    stain: Optional[str] = None
    timestamp: Optional[str] = None
    extension: Optional[str] = None


class ValidationIssue(BaseModel):
    code: str
    message: str


class FilenameValidationRequest(BaseModel):
    filename: str


class FilenameValidationResponse(BaseModel):
    filename: str
    # valid | partially_valid | invalid
    classification: str
    components: Optional[ValidationComponent] = None
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    suggested_correction: Optional[str] = None


# ---------------------------------------------------------------------------
# Recovery — review state update
# ---------------------------------------------------------------------------


class ReviewStateUpdateRequest(BaseModel):
    review_status: str
    technician_note: Optional[str] = None


class ReviewStateUpdateResponse(BaseModel):
    change_id: int
    previous_status: str
    new_status: str
    reviewed_at: str


# ---------------------------------------------------------------------------
# Recovery — label preview (enriched)
# ---------------------------------------------------------------------------


class LabelPreviewResponse(BaseModel):
    file_id: int
    filename: Optional[str] = None
    available: bool
    unavailable_reason: Optional[str] = None
    slide_id: Optional[str] = None
    case_id: Optional[str] = None
    scanner_id: Optional[str] = None
    scanner_vendor: Optional[str] = None
    scanner_model: Optional[str] = None
    stain_type: Optional[str] = None
    suggested_filename: Optional[str] = None
    datamatrix_raw: Optional[str] = None
    datamatrix_decode_status: Optional[str] = None
    datamatrix_error: Optional[str] = None
    stain_ocr_raw: Optional[str] = None
    stain_matched: Optional[str] = None
    stain_origin: Optional[str] = None
    roi_case_number: Optional[str] = None
    roi_lab_id: Optional[str] = None
    roi_stain: Optional[str] = None
    routing_type: Optional[str] = None
    routing_reason: Optional[str] = None
    original_filename: Optional[str] = None
    extraction_metadata: Optional[dict] = None


# ---------------------------------------------------------------------------
# Recovery — audit trail
# ---------------------------------------------------------------------------


class AuditChangeItem(BaseModel):
    change_id: int
    change_type: str
    inferred_action: Optional[str] = None
    old_filename: Optional[str] = None
    new_filename: Optional[str] = None
    old_path: Optional[str] = None
    new_path: Optional[str] = None
    review_status: Optional[str] = None
    recovery_outcome: Optional[str] = None
    recovery_reason: Optional[str] = None
    technician_notes: Optional[str] = None
    review_notes: Optional[str] = None
    detected_at: Optional[str] = None
    recovered_at: Optional[str] = None
    requeued_at: Optional[str] = None
    reviewed_at: Optional[str] = None


class AuditEventItem(BaseModel):
    event_id: int
    event_type: str
    service_name: str
    occurred_at: Optional[str] = None
    event_payload: Optional[dict] = None


class AuditTrailResponse(BaseModel):
    file_id: int
    filename: Optional[str] = None
    global_artifact_id: Optional[str] = None
    changes: list[AuditChangeItem] = []
    events: list[AuditEventItem] = []


# ---------------------------------------------------------------------------
# Phase 10 — Observability & Operational Safety
# ---------------------------------------------------------------------------


class ServiceHealthExtended(BaseModel):
    runner_id: str
    service_name: str
    host_id: str
    pid: int
    status: str
    health_state: str  # healthy | degraded | stale | disconnected
    heartbeat_age_seconds: Optional[float] = None
    uptime_seconds: Optional[float] = None
    started_at: Optional[datetime] = None
    last_heartbeat_at: Optional[datetime] = None
    environment: Optional[str] = None
    service_version: Optional[str] = None
    queue_pending: int = 0
    queue_running: int = 0
    queue_failed: int = 0


class ServiceHealthExtendedResponse(BaseModel):
    services: list[ServiceHealthExtended]
    stale_threshold_seconds: int
    as_of: datetime


class StuckTriggerItem(BaseModel):
    trigger_id: int
    kind: str          # pending_stuck | running_stuck | exhausted
    severity: str      # warning | critical
    stage: str
    target_service: str
    global_artifact_id: Optional[str] = None
    stuck_seconds: Optional[float] = None
    retry_count: int = 0
    max_retries: int = 3
    error_message: Optional[str] = None
    triggered_at: Optional[datetime] = None
    likely_cause: str


class StuckTriggersResponse(BaseModel):
    items: list[StuckTriggerItem]
    total: int
    pending_stuck: int
    running_stuck: int
    exhausted: int


class OperationalIncident(BaseModel):
    severity: str      # info | warning | critical
    category: str
    title: str
    detail: str
    related_ids: list[int] = []


class OperationalIncidentsResponse(BaseModel):
    incidents: list[OperationalIncident]
    total: int
    critical_count: int
    warning_count: int
    info_count: int
    as_of: datetime


class EnvironmentConfig(BaseModel):
    environment: str           # development | test | staging | production
    upload_dry_run: bool
    c_store_enabled: bool
    lis_enabled: bool
    pasnet_enabled: bool
    upload_peer_ip: Optional[str] = None
    upload_peer_port: Optional[str] = None
    sec_dcm_bin: Optional[str] = None


class DbHealthResponse(BaseModel):
    table_sizes: dict[str, int] = {}
    failed_triggers: int = 0
    pending_triggers: int = 0
    oldest_pending_age_seconds: Optional[float] = None
    recovery_backlog: int = 0
    as_of: datetime


# ---------------------------------------------------------------------------
# Phase 9 — Artifact investigation
# ---------------------------------------------------------------------------


class RetryChainItem(BaseModel):
    stage: str
    total_attempts: int
    total_retries: int
    final_outcome: Optional[str] = None
    failure_category: Optional[str] = None  # transient|validation|infrastructure|network|parser|unknown
    trigger_ids: list[int] = []


class QueueMetric(BaseModel):
    stage: str
    attempts: int
    avg_queue_delay_seconds: Optional[float] = None
    avg_exec_seconds: Optional[float] = None
    avg_total_seconds: Optional[float] = None
    max_queue_delay_seconds: Optional[float] = None
    max_exec_seconds: Optional[float] = None


class FailureGroup(BaseModel):
    category: str  # transient|validation|infrastructure|network|parser|unknown
    count: int
    trigger_ids: list[int] = []
    representative_error: Optional[str] = None


class PathLineageItem(BaseModel):
    stage: str
    event: str
    filename: Optional[str] = None
    path: Optional[str] = None
    previous_filename: Optional[str] = None


class ArtifactInvestigationResponse(BaseModel):
    """
    Full investigation bundle for a single artifact.

    Returned by GET /dashboard/api/artifacts/{id}/investigation.
    All sub-queries execute within one DB session for consistency.
    """
    file_record: SlideItem
    qc_result: Optional[QCResultSummary] = None
    conversion_result: Optional[ConversionResultSummary] = None
    upload_result: Optional[UploadResultSummary] = None
    extraction_result: Optional[ExtractionResultSummary] = None
    triggers: list[TriggerItem] = []
    recovery_events: list[RecoveryEventItem] = []
    recent_events: list[EventItem] = []
    events_total: int = 0

    # Intelligence layers (computed server-side)
    retry_chains: list[RetryChainItem] = []
    queue_metrics: list[QueueMetric] = []
    failure_groups: list[FailureGroup] = []
    path_lineage: list[PathLineageItem] = []


# ---------------------------------------------------------------------------
# Services health
# ---------------------------------------------------------------------------


class RunnerItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    runner_id: str
    service_name: str
    host_id: str
    pid: int
    status: str
    environment: Optional[str] = None
    service_version: Optional[str] = None
    started_at: datetime
    last_heartbeat_at: datetime


class ServicesHealthResponse(BaseModel):
    runners: list[RunnerItem]
    stale_threshold_seconds: int
    as_of: datetime
