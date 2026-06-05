# RecoverySentry

RecoverySentry monitors `failed/`, `suspicious/`, and `manual_review/` folders for technician-corrected WSI files and auto-recovers valid slides back into the normal Pathoryx pipeline.

## What it does

When a technician renames, replaces, or adds a corrected file into a watched folder, RecoverySentry:

1. Detects the change (filesystem snapshot diff + watchdog-style poll)
2. Validates the filename matches the Pathoryx SlideID format
3. Extracts the scan timestamp from WSI metadata (if not already in the filename)
4. Moves the file to `final/<CaseID>/` atomically
5. Updates `core.file_records` and sets status to `qc_pending`
6. Creates an idempotent QC service trigger
7. Emits immutable audit events

## Service identity

| Key | Value |
|-----|-------|
| Service name | `recovery_sentry` |
| CLI command | `pathoryx-recovery-sentry` |
| DB schema | `failed_watcher` (unchanged for migration safety) |
| Health port | 8087 |
| Metrics port | 9097 |

---

## Expected filename format

### Valid SlideID with timestamp (Case 1 — ready to move immediately)

```
N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs
```

Components:
- `N2024002863` — CaseID (N + 10 digits)
- `SA` — Pot
- `1` — Block
- `1` — Section
- `H&E` — Stain
- `UTC2024-08-22T08_36_39Z` — UTC timestamp (colons replaced with underscores)

### Valid SlideID without timestamp (Case 2 — timestamp extracted from WSI metadata)

```
N2024002863SA-1-1-H&E.svs
```

RecoverySentry opens the file with OpenSlide, reads vendor-specific scan metadata, and appends the timestamp automatically:

```
N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs
```

### Invalid filename (manual_review_required)

```
54564564.svs                    ← no CaseID pattern
slide_001.svs                   ← no SlideID structure
N2024002863SA-1-1-H&E.png      ← unsupported extension
```

These stay in the watched folder. RecoverySentry records `manual_review_required` in the DB.

---

## Auto-recovery workflow

```
technician renames/adds file in failed/
  → RecoverySentry detects change (next poll cycle)
  → wait for file to be stable (default: 10 seconds since last mtime change)
  → parse filename → validate SlideID
  → if invalid → record manual_review_required, stop
  → if valid with timestamp → build destination path
  → if valid without timestamp → extract from WSI metadata
       → if no metadata timestamp → record manual_review_required, stop
  → check destination final/<CaseID>/<filename>
       → if exists and duplicate_strategy=suffix → add safe suffix (_1, _2, ...)
       → if exists and duplicate_strategy=manual_review → record manual_review_required, stop
  → atomic move to final/<CaseID>/
  → DB transaction:
       → core.file_records.current_file_path = new path
       → core.file_records.status = qc_pending
       → core.service_trigger INSERT (idempotent) for qc_service
       → events.pipeline_events: recovery_sentry.auto_recovered (+ timestamp_added if extracted)
       → failed_watcher.technician_changes.recovery_outcome = auto_recovered
  → done
```

---

## Events emitted

| Event type | When |
|-----------|------|
| `recovery_sentry.change_detected` | Any change detected in a watched folder |
| `recovery_sentry.timestamp_extracted` | Timestamp read from WSI metadata |
| `recovery_sentry.timestamp_added` | Timestamp appended to filename |
| `recovery_sentry.auto_recovered` | File successfully moved to final/ |
| `recovery_sentry.qc_requeued` | QC trigger created |
| `recovery_sentry.manual_review_required` | File cannot be auto-recovered |
| `recovery_sentry.failed` | Unexpected error during processing |

All events are immutable rows in `events.pipeline_events`.

---

## Configuration

Primary config: `configs/recovery_sentry.yaml`

```yaml
service:
  name: recovery_sentry
  poll_interval_seconds: 30     # how often to scan folders
  stable_after_seconds: 10      # wait this long after last mtime change

watch_folders:
  - /data/pathoryx/failed
  - /data/pathoryx/suspicious
  - /data/pathoryx/manual_review

final_destination_root: /data/pathoryx/final
babelshark_config_path: ./configs/babelshark_config.yaml

recovery:
  auto_recover_valid_slide_id: true
  add_timestamp_if_missing: true
  overwrite_existing: false
  duplicate_strategy: suffix          # "suffix" | "manual_review"
  checksum_mode: partial              # "partial" | "full" | "none"
  allow_filesystem_timestamp_fallback: false

next_stage:
  target_service: qc_service
  stage_name: qc
```

Environment variables:

| Variable | Description |
|----------|-------------|
| `RECOVERY_SENTRY_CONFIG` | Path to YAML config (preferred) |
| `DATABASE_URL` | PostgreSQL connection string |
| `FAILED_WATCHER_FOLDERS` | Backward-compat comma-separated watch folders |

---

## PostgreSQL queries

### View all pending manual review files

```sql
SELECT
    tc.internal_id,
    tc.watch_folder_label,
    tc.new_filename,
    tc.new_path,
    tc.case_id,
    tc.recovery_reason,
    tc.detected_at
FROM failed_watcher.technician_changes tc
WHERE tc.recovery_outcome = 'manual_review_required'
  AND tc.review_status NOT IN ('dismissed', 'requeued')
ORDER BY tc.detected_at DESC;
```

### View all auto-recovered slides

```sql
SELECT
    tc.case_id,
    tc.new_filename,
    tc.recovery_destination_path,
    tc.recovered_at,
    tc.timestamp_extracted_from_wsi,
    fr.status AS current_status
FROM failed_watcher.technician_changes tc
LEFT JOIN core.file_records fr
    ON fr.current_file_path = tc.recovery_destination_path
WHERE tc.recovery_outcome = 'auto_recovered'
ORDER BY tc.recovered_at DESC;
```

### Check QC trigger status after recovery

```sql
SELECT
    st.trigger_status,
    st.triggered_at,
    st.accepted_at,
    st.claimed_by_runner_id,
    fr.current_file_path
FROM core.service_trigger st
JOIN core.file_records fr ON fr.internal_id = st.file_record_internal_id
WHERE st.source_service = 'recovery_sentry'
  AND st.target_service = 'qc_service'
ORDER BY st.triggered_at DESC;
```

### Manually requeue a manual_review_required case

If a technician has corrected a file and you need to manually force requeue:

```sql
-- 1. Update the file record status
UPDATE core.file_records
SET status = 'qc_pending',
    current_file_path = '/data/pathoryx/final/N2024002863/N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs',
    canonical_path = '/data/pathoryx/final/N2024002863/N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs',
    updated_at = now()
WHERE global_artifact_id = '<artifact_id>';

-- 2. Insert QC trigger (idempotent via unique constraint)
INSERT INTO core.service_trigger
    (source_service, target_service, stage_name, file_record_internal_id,
     global_artifact_id, trigger_status, triggered_at, retry_count, max_retries)
SELECT
    'recovery_sentry', 'qc_service', 'qc', internal_id,
    global_artifact_id, 'pending', now(), 0, 3
FROM core.file_records
WHERE global_artifact_id = '<artifact_id>'
ON CONFLICT (source_service, target_service, stage_name, file_record_internal_id) DO NOTHING;
```

---

## Troubleshooting

### File detected but not recovered — reason: invalid_slide_id_pattern

The filename doesn't match `N{10digits}{POT}-{BLOCK}-{SECTION}-{STAIN}[_UTC...Z].ext`.

Fix: Ask the technician to rename the file following the standard format.

### File detected but not recovered — reason: missing_timestamp_metadata

RecoverySentry opened the file with OpenSlide but could not find a scan timestamp in the metadata.

Options:
1. Enable `allow_filesystem_timestamp_fallback: true` in config (uses file mtime — less accurate).
2. Have the technician rename the file to include the timestamp: `N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs`.

### File detected but not recovered — reason: duplicate_destination

A file with the same final name already exists in `final/<CaseID>/`.

Fix: Check `duplicate_strategy` in config:
- `suffix` (default): auto-adds `_1`, `_2`, etc. — usually the right choice.
- `manual_review`: leaves for manual decision.

### Service exits immediately with "no_watch_folders_configured"

Set `RECOVERY_SENTRY_CONFIG=./configs/recovery_sentry.yaml` or `FAILED_WATCHER_FOLDERS=/path/to/failed`.

### Service exits immediately with "final_destination_not_configured"

Set `final_destination_root` in `configs/recovery_sentry.yaml` or ensure `babelshark_config_path` points to a valid config with `final_output_dir`.

### recovery_db_update_failed_after_move — CRITICAL log

The file was moved to `final/` but the DB update failed. This is a consistency incident.

Recovery steps:
1. Find the file at `recovery_destination_path` in the critical log.
2. Run the manual requeue SQL above to update `file_records` and create the QC trigger.
3. Acknowledge in the technician_changes table: `UPDATE failed_watcher.technician_changes SET review_status = 'requeued', review_notes = 'manual DB fix' WHERE internal_id = <id>;`

---

## Migration notes

- `pathoryx-failed-watcher` CLI is **deprecated** — it prints an error and exits. Use `pathoryx-recovery-sentry`.
- `PATHORYX_SERVICES=failed_watcher` in the orchestrator still routes to `pathoryx-recovery-sentry` via a backward-compat alias.
- `FAILED_WATCHER_FOLDERS` env var still works as a fallback for watch folder config (RecoverySentry reads it).
- `failed_watcher` **DB schema name is preserved** — `failed_watcher.technician_changes` and `failed_watcher.watched_folder_snapshots` are the live tables. No migration needed.
