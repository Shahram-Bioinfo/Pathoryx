// API response types — mirrors backend Pydantic schemas exactly.

export interface SlideStatusCounts {
  total: number
  by_status: Record<string, number>
}

export interface TriggerStatusCounts {
  pending: number
  running: number
  failed: number
  completed: number
}

export interface RunnerStatusCounts {
  active: number
  stale: number
  other: Record<string, number>
}

export interface OverviewResponse {
  as_of: string
  slides: SlideStatusCounts
  triggers: TriggerStatusCounts
  runners: RunnerStatusCounts
  events_last_24h: number
}

// ---- Slides ----

export interface SlideItem {
  internal_id: number
  global_artifact_id: string | null
  original_filename: string | null
  current_filename: string | null
  status: string | null
  file_size: number | null
  file_format: string | null
  scanner_id: string | null
  scanner_name: string | null
  artifact_type: string | null
  created_at: string | null
  updated_at: string | null
  original_path: string | null
  current_file_path: string | null
}

export interface SlideListResponse {
  total: number
  page: number
  page_size: number
  items: SlideItem[]
}

export interface QCResultSummary {
  internal_id: number
  qc_result: string | null
  decision_status: string | null
  decision_reason: string | null
  error_reason: string | null
  qc_context: string | null
  total_duration_seconds: number | null
  processed_at: string | null
  source_path: string | null
  scanner_name: string | null
  scanner_id: string | null
  trust_scanner_qc: boolean | null
  input_mode: string | null
  next_service: string | null
  next_stage: string | null
  started_at: string | null
  finished_at: string | null
}

export interface ConversionResultSummary {
  internal_id: number
  conversion_status: string | null
  output_format: string | null
  was_already_dicom: boolean | null
  conversion_tool: string | null
  input_file_size_bytes: number | null
  output_file_size_bytes: number | null
  duration_seconds: number | null
  processed_at: string | null
  source_path: string | null
  output_path: string | null
}

export interface UploadResultSummary {
  internal_id: number
  upload_status: string | null
  target_system: string | null
  final_outcome: string | null
  duration_seconds: number | null
  retry_count: number
  processed_at: string | null
  source_path: string | null
  target_endpoint: string | null
  upload_method: string | null
}

export interface EventItem {
  event_id: number
  event_type: string
  global_artifact_id: string | null
  global_run_id: string | null
  service_name: string
  occurred_at: string
  aggregate_type: string
  aggregate_id: string
  event_payload: Record<string, unknown> | null
}

export interface TriggerItem {
  internal_id: number
  source_service: string
  target_service: string
  stage_name: string
  trigger_status: string | null
  retry_count: number
  max_retries: number
  error_message: string | null
  triggered_at: string | null
  accepted_at: string | null
  started_at: string | null
  finished_at: string | null
  correlation_id: string | null
}

export interface RecoveryEventItem {
  internal_id: number
  change_type: string
  watch_folder_label: string
  old_path: string | null
  new_path: string | null
  inferred_action: string | null
  review_status: string
  detected_at: string
  case_id: string | null
  recovery_outcome: string | null
  recovery_reason: string | null
  recovery_destination_path: string | null
  recovered_at: string | null
  timestamp_in_filename: boolean
  timestamp_extracted_from_wsi: boolean
  requeued_at: string | null
}

export interface ExtractionResultSummary {
  internal_id: number
  slide_id: string | null
  stain_type: string | null
  scanner_id: string | null
  scanner_model: string | null
  scanner_vendor: string | null
  intake_decision: string | null
  action_taken: string | null
  next_stage: string | null
  extraction_status: string | null
  requires_qc: boolean | null
  has_internal_qc: boolean | null
}

export interface SlideDetailResponse {
  file_record: SlideItem
  qc_result: QCResultSummary | null
  conversion_result: ConversionResultSummary | null
  upload_result: UploadResultSummary | null
  recent_events: EventItem[]
  triggers: TriggerItem[]
  recovery_events: RecoveryEventItem[]
  extraction_result: ExtractionResultSummary | null
}

// ---- Events ----

export interface EventListResponse {
  items: EventItem[]
  count: number
}

// ---- Queues ----

export interface QueueRow {
  target_service: string
  pending: number
  running: number
  failed: number
  completed: number
}

export interface QueueStatusResponse {
  queues: QueueRow[]
  total_pending: number
  total_failed: number
}

// ---- Recovery ----

export interface RecoveryItem {
  internal_id: number
  change_type: string
  watch_folder_label: string
  global_artifact_id: string | null
  review_status: string
  detected_at: string
  inferred_action: string | null
  recovery_outcome: string | null
  recovery_reason: string | null
  recovered_at: string | null
  created_at: string | null
}

export interface RecoveryResponse {
  items: RecoveryItem[]
  total: number
  /** True per-status counts from a GROUP BY query, unaffected by active filter. */
  by_status: Record<string, number>
}

// ---- Failures ----

export interface FailedSlideItem {
  internal_id: number
  global_artifact_id: string | null
  original_filename: string | null
  status: string | null
  updated_at: string | null
  scanner_name: string | null
  scanner_id: string | null
  current_file_path: string | null
  file_format: string | null
}

export interface FailedTriggerItem {
  internal_id: number
  source_service: string
  target_service: string
  stage_name: string
  global_artifact_id: string | null
  trigger_status: string | null
  retry_count: number
  max_retries: number
  error_message: string | null
  triggered_at: string | null
  accepted_at: string | null
  started_at: string | null
  finished_at: string | null
  trigger_payload_json: Record<string, unknown> | null
}

export interface FailuresResponse {
  failed_slides: FailedSlideItem[]
  failed_triggers: FailedTriggerItem[]
  artifact_ids_with_recovery: string[]
}

// ---- Recovery — watched folder stats ----

export interface WatchFolderSummary {
  label: string
  path: string | null
  total_files: number
  recently_changed: number
  awaiting_review: number
  auto_recovered: number
  last_scan_time: string | null
}

export interface WatchFoldersResponse {
  folders: WatchFolderSummary[]
  as_of: string
}

// ---- Recovery — monitored file listing ----

export interface MonitoredFileItem {
  file_id: number
  filename: string
  file_path: string
  folder_label: string
  folder_path: string | null
  /** Path of the containing directory relative to the watch root (e.g. "2026-06-05"). Empty string for top-level files. */
  relative_folder_path: string | null
  /** Whether the containing directory currently exists on disk. */
  folder_exists: boolean
  first_seen_at: string | null
  last_seen_at: string | null
  file_size: number | null
  slide_id: string | null
  case_id: string | null
  extension: string | null
  global_artifact_id: string | null
  file_record_internal_id: number | null
  // Enriched from latest TechnicianChange
  change_id: number | null
  change_type: string | null
  review_status: string | null
  recovery_outcome: string | null
  recovery_reason: string | null
  detected_at: string | null
  inferred_action: string | null
}

export interface MonitoredFilesResponse {
  total: number
  items: MonitoredFileItem[]
}

// ---- Recovery — open folder action ----

export interface OpenFolderResponse {
  opened: boolean
  path: string | null
  message: string
}

// ---- Recovery — technician rename ----

export interface TechnicianRenameRequest {
  proposed_filename: string
  technician_note?: string
  confirm: boolean
}

export interface TechnicianRenameResponse {
  outcome: 'auto_recovered' | 'manual_review_required' | 'validation_failed' | 'skipped'
  reason: string | null
  destination_path: string | null
  final_filename: string | null
  case_id: string | null
  slide_id: string | null
  change_id: number | null
  validation_error: string | null
}

// ---- Recovery — filename validation ----

export interface ValidationComponent {
  case_id: string | null
  pot: string | null
  block: string | null
  section: string | null
  stain: string | null
  timestamp: string | null
  extension: string | null
}

export interface ValidationIssue {
  code: string
  message: string
}

export interface FilenameValidationResponse {
  filename: string
  classification: 'valid' | 'partially_valid' | 'invalid'
  components: ValidationComponent | null
  errors: ValidationIssue[]
  warnings: ValidationIssue[]
  suggested_correction: string | null
  normalized_filename: string | null  // stain-canonicalized form when different from input
}

// ---- Recovery — review state update ----

export interface ReviewStateUpdateRequest {
  review_status: string
  technician_note?: string
}

export interface ReviewStateUpdateResponse {
  change_id: number
  previous_status: string
  new_status: string
  reviewed_at: string
}

// ---- Recovery — label preview (enriched) ----

export interface LabelPreviewResponse {
  file_id: number
  filename: string | null
  available: boolean
  unavailable_reason: string | null
  slide_id: string | null
  case_id: string | null
  scanner_id: string | null
  scanner_vendor: string | null
  scanner_model: string | null
  stain_type: string | null
  suggested_filename: string | null
  datamatrix_raw: string | null
  datamatrix_decode_status: string | null
  datamatrix_error: string | null
  stain_ocr_raw: string | null
  stain_matched: string | null
  stain_origin: string | null
  roi_case_number: string | null
  roi_lab_id: string | null
  roi_stain: string | null
  routing_type: string | null
  routing_reason: string | null
  original_filename: string | null
  extraction_metadata: Record<string, unknown> | null
}

// ---- Recovery — audit trail ----

export interface AuditChangeItem {
  change_id: number
  change_type: string
  inferred_action: string | null
  old_filename: string | null
  new_filename: string | null
  old_path: string | null
  new_path: string | null
  review_status: string | null
  recovery_outcome: string | null
  recovery_reason: string | null
  technician_notes: string | null
  review_notes: string | null
  detected_at: string | null
  recovered_at: string | null
  requeued_at: string | null
  reviewed_at: string | null
}

export interface AuditEventItem {
  event_id: number
  event_type: string
  service_name: string
  occurred_at: string | null
  event_payload: Record<string, unknown> | null
}

export interface AuditTrailResponse {
  file_id: number
  filename: string | null
  global_artifact_id: string | null
  changes: AuditChangeItem[]
  events: AuditEventItem[]
}

// ---- Phase 10 — Observability & Safety ----

export interface ServiceHealthExtended {
  runner_id: string
  service_name: string
  host_id: string
  pid: number
  status: string
  /** healthy | degraded | stale | disconnected */
  health_state: string
  heartbeat_age_seconds: number | null
  uptime_seconds: number | null
  started_at: string | null
  last_heartbeat_at: string | null
  environment: string | null
  service_version: string | null
  queue_pending: number
  queue_running: number
  queue_failed: number
}

export interface ServiceHealthExtendedResponse {
  services: ServiceHealthExtended[]
  stale_threshold_seconds: number
  as_of: string
}

export interface StuckTriggerItem {
  trigger_id: number
  /** pending_stuck | running_stuck | exhausted */
  kind: string
  /** warning | critical */
  severity: string
  stage: string
  target_service: string
  global_artifact_id: string | null
  stuck_seconds: number | null
  retry_count: number
  max_retries: number
  error_message: string | null
  triggered_at: string | null
  likely_cause: string
}

export interface StuckTriggersResponse {
  items: StuckTriggerItem[]
  total: number
  pending_stuck: number
  running_stuck: number
  exhausted: number
}

export interface OperationalIncident {
  severity: 'info' | 'warning' | 'critical'
  category: string
  title: string
  detail: string
  related_ids: number[]
}

export interface OperationalIncidentsResponse {
  incidents: OperationalIncident[]
  total: number
  critical_count: number
  warning_count: number
  info_count: number
  as_of: string
}

export interface EnvironmentConfig {
  environment: string
  upload_dry_run: boolean
  c_store_enabled: boolean
  lis_enabled: boolean
  pasnet_enabled: boolean
  upload_peer_ip: string | null
  upload_peer_port: string | null
  sec_dcm_bin: string | null
}

export interface DbHealthResponse {
  table_sizes: Record<string, number>
  failed_triggers: number
  pending_triggers: number
  oldest_pending_age_seconds: number | null
  recovery_backlog: number
  as_of: string
}

// ---- Phase 9 — Artifact investigation ----

export interface RetryChainItem {
  stage: string
  total_attempts: number
  total_retries: number
  final_outcome: string | null
  /** transient | validation | infrastructure | network | parser | unknown */
  failure_category: string | null
  trigger_ids: number[]
}

export interface QueueMetric {
  stage: string
  attempts: number
  avg_queue_delay_seconds: number | null
  avg_exec_seconds: number | null
  avg_total_seconds: number | null
  max_queue_delay_seconds: number | null
  max_exec_seconds: number | null
}

export interface FailureGroup {
  category: string
  count: number
  trigger_ids: number[]
  representative_error: string | null
}

export interface PathLineageItem {
  stage: string
  event: string
  filename: string | null
  path: string | null
  previous_filename?: string | null
}

export interface ArtifactInvestigationResponse {
  file_record: SlideItem
  qc_result: QCResultSummary | null
  conversion_result: ConversionResultSummary | null
  upload_result: UploadResultSummary | null
  extraction_result: ExtractionResultSummary | null
  triggers: TriggerItem[]
  recovery_events: RecoveryEventItem[]
  recent_events: EventItem[]
  events_total: number
  retry_chains: RetryChainItem[]
  queue_metrics: QueueMetric[]
  failure_groups: FailureGroup[]
  path_lineage: PathLineageItem[]
}

// ---- Services health ----

export interface RunnerItem {
  runner_id: string
  service_name: string
  host_id: string
  pid: number
  status: string
  environment: string | null
  service_version: string | null
  started_at: string
  last_heartbeat_at: string
}

export interface ServicesHealthResponse {
  runners: RunnerItem[]
  stale_threshold_seconds: number
  as_of: string
}

// ---- Phase 3.5 — Upload Operations ----

export type UploadStatus = 'queued' | 'estimating' | 'uploading' | 'uploaded' | 'delayed' | 'failed'

export interface UploadQueueItem {
  id: number
  file_record_internal_id: number | null
  slide_id: string | null
  filename: string
  scanner_id: string | null
  uploader_host: string | null
  queued_at: string
  estimated_upload_at: string | null
  upload_started_at: string | null
  upload_completed_at: string | null
  upload_status: UploadStatus
  retry_count: number
  file_size_bytes: number | null
  // Priority: 0=UPLOAD_NEXT, 1=HIGH, 5=NORMAL
  priority: number
  priority_source: 'default' | 'watch_folder' | 'manual' | 'upload_next'
  priority_reason: string | null
  priority_updated_at: string | null
  priority_updated_by: string | null
  watch_folder_path: string | null
  watch_folder_label: string | null
  upload_speed_mbps: number | null
  failure_reason: string | null
  last_updated_at: string
  is_delayed: boolean
}

export interface UploadQueueResponse {
  total: number
  page: number
  page_size: number
  items: UploadQueueItem[]
}

export interface UploadMetrics {
  queued_count: number
  active_count: number
  completed_today: number
  failed_count: number
  delayed_count: number
  avg_duration_seconds: number | null
  avg_throughput_mbps: number | null
}

export interface UploadFilterOptions {
  scanners: string[]
  hosts: string[]
  priorities: number[]
}

export interface WatchFolderPrioritySummary {
  watch_folder_path: string
  watch_folder_label: string
  priority: number
  queued_count: number
}

export interface UploadPrioritySummary {
  by_priority: { upload_next: number; high: number; normal: number }
  by_source: { manual: number; watch_folder: number; upload_next: number; default: number }
  watch_folders: WatchFolderPrioritySummary[]  // only HIGH folders
}

export interface UploadIngestRecord {
  slide_id?: string
  filename: string
  scanner_id?: string
  uploader_host?: string
  queued_at: string
  estimated_upload_at?: string
  upload_started_at?: string
  upload_completed_at?: string
  upload_status?: string
  retry_count?: number
  file_size_bytes?: number
  priority?: number
  upload_speed_mbps?: number
  failure_reason?: string
  last_updated_at?: string
}

export interface UploadIngestRequest {
  records: UploadIngestRecord[]
}

export interface UploadIngestResponse {
  upserted_count: number
  skipped_count: number
}

export interface UploadPriorityRequest {
  mode: 'upload_next' | 'high' | 'normal' | 'clear_upload_next'
  reason?: string
}

// ---- Phase 4.4 — Computer Core analytics ----

export interface CoreOverviewResponse {
  total_slides: number
  slides_today: number
  uploaded_today: number
  failed_slides: number
  active_uploads: number
  queued_uploads: number
  delayed_uploads: number
  recovery_backlog: number
  unreviewed_changes: number
  total_bytes: number
  status_counts: Record<string, number>
  upload_status_counts: Record<string, number>
  as_of: string
}

export interface ScannerActivityItem {
  scanner_id: string
  display_name: string
  total_slides: number
  failed_count: number
  uploaded_count: number
  total_bytes: number
  avg_file_size: number
  last_activity: string | null
  avg_upload_speed_mbps: number | null
  /** active | idle | no_recent_activity */
  operational_state: string
}

export interface ScannerActivityResponse {
  scanners: ScannerActivityItem[]
  as_of: string
}

export interface StainDistributionItem {
  stain_type: string
  count: number
  percentage: number
}

export interface StainDistributionResponse {
  items: StainDistributionItem[]
  total: number
  as_of: string
}

export interface RecoveryStatsResponse {
  total_monitored: number
  failed_count: number
  suspicious_count: number
  manual_review_count: number
  auto_recovered: number
  manual_review_required: number
  total_changes: number
  total_resolved: number
  recovery_rate: number
  recent_7d: number
  by_folder: Record<string, number>
  by_review_status: Record<string, number>
  by_outcome: Record<string, number>
  as_of: string
}

export interface StorageScannerItem {
  scanner_id: string
  count: number
  total_bytes: number
  avg_bytes: number
}

export interface StorageStatsResponse {
  total_slides_with_size: number
  total_bytes: number
  avg_bytes: number
  max_bytes: number
  min_bytes: number
  uploaded_today_bytes: number
  by_scanner: StorageScannerItem[]
  as_of: string
}

export interface DailyUploadCount {
  day: string | null
  count: number
}

export interface UploadVelocityResponse {
  avg_speed_mbps: number | null
  avg_duration_seconds: number | null
  total_in_queue: number
  completed_total: number
  failed_total: number
  total_retries: number
  queue_depth: number
  delayed_count: number
  daily_uploads_7d: DailyUploadCount[]
  as_of: string
}

// ---- Phase 3.6 — Scanner Fleet ----

export interface ScannerConfig {
  scanner_id: string
  display_name: string
  location: string
  vendor: string
  model: string
  serial_number: string
  aliases: string[]
  enabled: boolean
}

export interface ScannerFleetResponse {
  scanners: ScannerConfig[]
  total: number
  enabled_count: number
}

export interface ScannerSummaryItem {
  scanner_id: string
  display_name: string
  location: string
  vendor: string
  model: string
  serial_number: string
  aliases: string[]
  enabled: boolean
  queued: number
  active: number
  failed: number
  delayed: number
  total: number
}

export interface ScannerSummaryResponse {
  scanners: ScannerSummaryItem[]
  as_of: string
}

/** Lookup map: scanner_id → display_name */
export type ScannerMap = Record<string, string>

// ── Phase 4.8 — Routing Policy Engine ────────────────────────────────────────

export interface ScannerDestinationItem {
  scanner_id: string
  destination: string
}

export interface RoutingModeInfo {
  name: string
  profile: string
  default_destination: string
  active_start: string
  active_end: string
  is_overnight: boolean
  is_active: boolean
  scanner_destinations: ScannerDestinationItem[]
}

export interface ColorDotRule {
  color: string
  destination: string
}

export interface RoutingValidationIssue {
  severity: 'error' | 'warning'
  message: string
  field: string | null
}

export interface NextModeInfo {
  name: string
  starts_at: string
}

export interface RoutingStatusResponse {
  active_mode: string | null
  active_profile: string | null
  active_default_destination: string | null
  next_mode: NextModeInfo | null
  timezone: string
  dry_run: boolean
  fallback_destination: string
  modes: RoutingModeInfo[]
  color_dot_rules: ColorDotRule[]
  validation_issues: RoutingValidationIssue[]
  as_of: string
}

export interface RoutingOverrideItem {
  id: number
  created_at: string
  created_by: string | null
  reason: string | null
  target_type: string
  target_value: string
  destination: string
  expires_at: string | null
  is_active: boolean
}

export interface RoutingOverridesResponse {
  active: RoutingOverrideItem[]
  all: RoutingOverrideItem[]
  total_active: number
  as_of: string
}

export interface CreateOverrideRequest {
  target_type: 'scanner' | 'file' | 'case'
  target_value: string
  destination: string
  expires_at?: string | null
  reason?: string | null
  created_by?: string | null
}

export interface RoutingPreviewItem {
  slide_id: string | null
  original_filename: string | null
  scanner_id: string | null
  scanner_name: string | null
  color_dot: string | null
  current_status: string | null
  predicted_destination: string
  routing_reason: string
  mode: string | null
  profile: string | null
  override_id: number | null
}

export interface RoutingPreviewResponse {
  items: RoutingPreviewItem[]
  total: number
  active_mode: string | null
  dry_run: boolean
  as_of: string
}

export interface RoutingDecisionItem {
  id: number
  created_at: string
  slide_id: string | null
  scanner_id: string | null
  mode: string | null
  profile: string | null
  color_dot: string | null
  color_dot_confidence: number | null
  destination: string
  routing_reason: string
  override_id: number | null
  dry_run: boolean
}

export interface RoutingDecisionsResponse {
  items: RoutingDecisionItem[]
  total: number
  stats: Record<string, unknown>
  as_of: string
}

export interface DecisionChainStep {
  step: number
  label: string
  applied: boolean
  value: string | null
  detail: string | null
}

export interface DecisionChainResponse {
  decision_id: number
  slide_id: string | null
  scanner_id: string | null
  mode: string | null
  color_dot: string | null
  final_destination: string
  final_reason: string
  dry_run: boolean
  chain: DecisionChainStep[]
  as_of: string
}
