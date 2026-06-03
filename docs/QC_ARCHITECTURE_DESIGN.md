# Scanner-Aware Dual-Mode QC Architecture Design

**Date:** 2026-05-29  
**Status:** Pending approval — no code changes made yet  
**Scope:** Enterprise QC service redesign to support pre-BabelShark and post-BabelShark modes with per-scanner policies

---

## Foundational Findings From Investigation

### Scanner identity — what exists

| Location | Scanner columns | Notes |
|----------|----------------|-------|
| `babelshark.extraction_results` | `scanner_id`, `scanner_model`, `scanner_vendor` | Populated from WSI metadata by BabelShark |
| `babelshark.slide_routing_decisions` | `scanner_id`, `scanner_model`, `scanner_vendor` | Same three columns |
| `core.file_records` | **none** | Scanner identity not surfaced at core file level |
| `qc.qc_results` | **none** | No scanner columns, no context, no mode, no source_path |
| `metadata_intake.py` | Extracts `scanner_id_raw`, `scanner_family`, `scanner_name` | Only available after BabelShark processes a file |

### Scanner identity — the fundamental challenge

In pre-BabelShark mode, QC processes a raw file before metadata extraction has occurred.
Scanner identity at that point can only come from **which input folder the file was found in**.
The scanner policy must map `input_dir → scanner_id` in config.
In post-BabelShark mode, scanner identity comes from the trigger payload or DB.

### Trigger payload gap (confirmed blocking bug)

`BabelSharkDBWriter.mark_intake_complete` calls `TriggerRepository.enqueue()` with no `payload`
argument → `trigger_payload_json = {}`. The QC runner reads
`trigger_payload_json.get("source_path", "")` and gets an empty string.
Post-BabelShark QC cannot resolve the file path. This is a blocking bug in BabelShark.

### Current config structure

- `configs/qc_config.yaml` — old adapter inference config (models, thresholds, watcher dirs).
  No scanner policies, no enterprise routing, no mode concept.
- `configs/babelshark_config.yaml` — single `watch_dir`, no per-scanner folder concept.
- `qc_context`, `input_mode`, `trust_scanner_qc`, `scanner_policy`, `qc_position` — absent
  from the entire codebase.

---

## 1. Final Config Schema

Two config files. They serve different concerns and must not be merged.

### `configs/qc_config.yaml` — INFERENCE CONFIG (minimal change)

Keep as-is. Remove only the `watcher:` section (watcher is now owned by `qc_service.yaml`).
All model weights, thresholds, modules, parameters, artifacts, and decision rules stay here.

```yaml
# QC inference configuration — models, thresholds, processing parameters.
# Referenced by qc_service.yaml via inference_config_path.
# Do NOT add routing, scanner policy, or service wiring here.

paths:
  output_root: /home/shahram/pathoryx_test_data/qc_output

pipeline:
  pipeline_name: slide_qc_service
  target_system: pathoryx

models:
  penmark_weights: /home/shahram/Pathoryx-Enterprise/models_weights/penmark_detection_MobileNetV3.pth
  bubble_weights:  /home/shahram/Pathoryx-Enterprise/models_weights/bubble_detection_ConvNeXtTiny_model.pth
  stain_weights:   /home/shahram/Pathoryx-Enterprise/models_weights/stain_model_MobileNetV3.pth
  blur_weights:    /home/shahram/Pathoryx-Enterprise/models_weights/blur_detection_resnet18_old.pth

modules:
  enable_stain: true
  enable_penmark: true
  enable_bubble: false
  enable_blur: true
  enable_sharpness: true

parameters:
  thumb_size: 1024
  patch_size: 224
  stride: 112
  batch_size: 16

thresholds:
  penmark_threshold: 0.01
  bubble_threshold:  0.02
  min_tissue_ratio:  0.03
  sat_threshold:     0.01

artifacts:
  save_csv: true
  save_visualizations: true

processing:
  force_reprocess: false

decision:
  blur_fail_threshold: 0.10
  route_passed_to_final: true
  route_failed_to_quarantine: true
  copy_instead_of_move: true

logging:
  level: INFO

postgres:
  url: ${DATABASE_URL}
```

---

### `configs/qc_service.yaml` — SERVICE CONFIG (new file)

Enterprise routing and policy config. Controls mode, scanner policies, and downstream wiring.

```yaml
# Pathoryx Enterprise — QC Service Routing & Scanner Policy Configuration
# Loaded by QCSettings via env var: QC_SERVICE_CONFIG
# This file controls WHAT to do with files and WHERE they go.
# The inference config (qc_config.yaml) controls HOW inference is performed.

service:
  enabled: true

  # Path to the old adapter inference config (models, thresholds, decisions).
  inference_config_path: /home/shahram/Pathoryx-Enterprise/configs/qc_config.yaml

  # Global mode — which intake pipelines to run.
  # "pre_babelshark"  : watcher-only (QC runs before BabelShark)
  # "post_babelshark" : trigger-only (QC runs after BabelShark)
  # "both"            : run watcher and trigger concurrently
  # "disabled"        : service starts but processes nothing
  mode: both


# ─── Pre-BabelShark Watcher Settings ─────────────────────────────────────────
# Only relevant when mode is "pre_babelshark" or "both".
# Per-scanner input_dirs are defined in scanner_policies below.
# These are shared watcher parameters.
pre_babelshark:
  poll_interval_seconds: 10
  stable_file_wait_seconds: 20
  allowed_extensions: [".svs", ".tif", ".tiff", ".ndpi", ".mrxs", ".vms"]
  recursive: false                  # do not recurse into subfolders by default
  file_routing: copy                # "copy" or "move" files after QC decision
  max_workers: 2


# ─── Post-BabelShark Trigger Settings ────────────────────────────────────────
# Only relevant when mode is "post_babelshark" or "both".
post_babelshark:
  trigger_target_service: qc_service
  trigger_stage_name: qc
  max_workers: 2
  trigger_poll_interval_seconds: 10
  max_consecutive_errors: 10


# ─── Scanner Policies ─────────────────────────────────────────────────────────
# One entry per scanner (or scanner folder).
# In pre_babelshark mode: scanner_id is assigned from this config (folder label).
# In post_babelshark mode: scanner_id comes from the trigger payload / BabelShark DB.
#
# Fields:
#   scanner_id           : logical scanner label used in DB records
#   input_dir            : folder to watch (pre_babelshark mode only)
#   pathoryx_qc_enabled  : if false, skip QC and route directly
#   qc_position          : "pre_babelshark" | "post_babelshark" | "both" | "none"
#   trust_scanner_qc     : if true, skip Pathoryx QC (scanner internal QC is trusted)
#   qc_skip_reason       : recorded in DB when skipping
#   file_routing         : "copy" or "move" (overrides pre_babelshark.file_routing)
#   passed_output_dir    : where QC-passed files go (pre_babelshark: → BabelShark watch dir)
#   failed_output_dir    : where QC-failed files go
#   quarantine_dir       : where corrupt/unsupported files go
#   next_service         : service to trigger after QC passes (post_babelshark only)
#   next_stage           : stage name for next_service trigger

scanner_policies:

  - scanner_id: aperio_scanner_01
    input_dir: /home/shahram/pathoryx_test_data/scanners/aperio_01
    pathoryx_qc_enabled: true
    qc_position: pre_babelshark
    trust_scanner_qc: false
    file_routing: copy
    passed_output_dir: /home/shahram/pathoryx_test_data/watch   # BabelShark watch dir
    failed_output_dir: /home/shahram/pathoryx_test_data/qc_failed/aperio_01
    quarantine_dir: /home/shahram/pathoryx_test_data/quarantine/aperio_01
    next_service: null    # watcher mode: BabelShark picks up from passed_output_dir
    next_stage: null

  - scanner_id: hamamatsu_scanner_01
    input_dir: /home/shahram/pathoryx_test_data/scanners/hamamatsu_01
    pathoryx_qc_enabled: true
    qc_position: post_babelshark
    trust_scanner_qc: false
    passed_output_dir: null    # post_babelshark: trigger-driven, no file move needed
    failed_output_dir: /home/shahram/pathoryx_test_data/qc_failed/hamamatsu_01
    quarantine_dir: /home/shahram/pathoryx_test_data/quarantine/hamamatsu_01
    next_service: dicom_service
    next_stage: dicom

  - scanner_id: leica_scanner_01
    input_dir: /home/shahram/pathoryx_test_data/scanners/leica_01
    pathoryx_qc_enabled: false
    qc_position: none
    trust_scanner_qc: true
    qc_skip_reason: "Leica GT450 scanner has validated internal quality control"
    file_routing: copy
    passed_output_dir: /home/shahram/pathoryx_test_data/watch   # goes straight to BabelShark
    failed_output_dir: null
    quarantine_dir: null
    next_service: null
    next_stage: null

  # Catch-all: files arriving with no matched scanner_id (unknown source).
  # scanner_id "__default__" is the convention for unmatched files.
  - scanner_id: __default__
    input_dir: null           # no watcher; used for policy lookup fallback only
    pathoryx_qc_enabled: true
    qc_position: pre_babelshark
    trust_scanner_qc: false
    file_routing: copy
    passed_output_dir: /home/shahram/pathoryx_test_data/watch
    failed_output_dir: /home/shahram/pathoryx_test_data/qc_failed/unknown
    quarantine_dir: /home/shahram/pathoryx_test_data/quarantine/unknown
    next_service: null
    next_stage: null
```

---

## 2. DB Migration Plan

Migration number: **`0004_qc_scanner_dual_mode.py`**  
Revises: `0003`

### Table: `core.file_records` — add 2 columns

Scanner identity at the core file level allows any service and any query to find
"all files from scanner X" without joining through BabelShark-specific tables.
BabelShark populates these after intake; QC's watcher populates them from policy config.

```sql
ALTER TABLE core.file_records ADD COLUMN scanner_id   TEXT;
ALTER TABLE core.file_records ADD COLUMN scanner_name TEXT;

CREATE INDEX ix_file_records_scanner_id ON core.file_records (scanner_id);
```

No constraint. NULL means scanner is unknown.

### Table: `qc.qc_results` — add 11 columns

These columns make every QC run fully self-describing for audit and traceability.

```sql
-- Context: which pipeline position produced this QC run
ALTER TABLE qc.qc_results ADD COLUMN qc_context TEXT;
  -- "pre_babelshark" | "post_babelshark" | "standalone"

ALTER TABLE qc.qc_results ADD COLUMN input_mode TEXT;
  -- "watcher" | "trigger"

-- File location at time of QC
ALTER TABLE qc.qc_results ADD COLUMN source_path TEXT;
  -- resolved absolute path of the file that was QC'd

-- Scanner traceability
ALTER TABLE qc.qc_results ADD COLUMN scanner_id    TEXT;
ALTER TABLE qc.qc_results ADD COLUMN scanner_name  TEXT;

-- Policy decisions recorded for this run
ALTER TABLE qc.qc_results ADD COLUMN trust_scanner_qc     BOOLEAN;
  -- was the scanner's internal QC trusted?
ALTER TABLE qc.qc_results ADD COLUMN pathoryx_qc_required BOOLEAN;
  -- was Pathoryx QC required by policy?
ALTER TABLE qc.qc_results ADD COLUMN qc_skip_reason TEXT;
  -- populated when pathoryx_qc_required=false or trust_scanner_qc=true

-- Downstream routing
ALTER TABLE qc.qc_results ADD COLUMN next_service TEXT;
  -- service triggered on pass (NULL in watcher/folder-dispatch mode)
ALTER TABLE qc.qc_results ADD COLUMN next_stage   TEXT;
  -- stage triggered on pass

-- Error classification
ALTER TABLE qc.qc_results ADD COLUMN error_reason TEXT;
  -- "unsupported_format" | "openslide_error" | "inference_error" | "file_missing"
  -- distinct from raw_qc_payload_json; queryable

CREATE INDEX ix_qc_results_scanner_id ON qc.qc_results (scanner_id);
CREATE INDEX ix_qc_results_qc_context ON qc.qc_results (qc_context);
CREATE INDEX ix_qc_results_input_mode ON qc.qc_results (input_mode);
```

All columns nullable. No backfill required. No change to `ck_file_records_status`.

### BabelShark trigger payload fix — code only, no migration

`BabelSharkDBWriter.mark_intake_complete` must include `source_path`, `scanner_id`,
and `scanner_name` in the trigger payload so the QC trigger runner can resolve the file.

---

## 3. Exact Files to Change

### New files

| File | Purpose |
|------|---------|
| `configs/qc_service.yaml` | Enterprise QC routing + scanner policy config |
| `pathoryx_enterprise/services/qc/watcher.py` | Pre-BabelShark watcher loop (new module) |
| `pathoryx_enterprise/db/migrations/versions/0004_qc_scanner_dual_mode.py` | DB migration |

### Modified files

| File | What changes | Why |
|------|-------------|-----|
| `pathoryx_enterprise/services/qc/config.py` | Complete rewrite | Load `qc_service.yaml`; expose `ScannerPolicy` dataclass; expose `QCServiceConfig` with `pre_babelshark`, `post_babelshark`, `scanner_policies`; keep `QCSettings` for env-based runtime params |
| `pathoryx_enterprise/services/qc/runner.py` | Major restructure | Dispatch watcher loop and/or trigger loop based on mode; pass scanner policy to each; add `source_path` fallback logic for trigger mode |
| `pathoryx_enterprise/services/qc/db_writer.py` | Extend signatures | Accept and write `qc_context`, `input_mode`, `source_path`, `scanner_id`, `scanner_name`, `trust_scanner_qc`, `pathoryx_qc_required`, `qc_skip_reason`, `next_service`, `next_stage`, `error_reason`; remove hardcoded `"dicom_service"`; write `scanner_id`/`scanner_name` to `core.file_records` |
| `pathoryx_enterprise/services/qc/main.py` | Minor update | Load `QCServiceConfig` in addition to `QCSettings`; pass both to `run()` |
| `pathoryx_enterprise/db/models/qc.py` | Add 11 mapped columns | Match migration 0004 |
| `pathoryx_enterprise/db/models/core.py` | Add 2 mapped columns | Add `scanner_id`, `scanner_name` to `FileRecord` |
| `pathoryx_enterprise/services/babelshark/db_writer.py` | Fix `mark_intake_complete` | Include `source_path`, `scanner_id`, `scanner_name` in trigger payload |

### Files explicitly NOT changed

| File | Reason |
|------|--------|
| `configs/qc_config.yaml` | Inference config stays as-is; only `watcher:` section removed |
| `configs/babelshark_config.yaml` | BabelShark watch config unchanged |
| `pathoryx_enterprise/services/babelshark/stage_runner.py` | Scanner extraction behavior unchanged |
| `pathoryx_enterprise/services/babelshark/stage_db_writer.py` | Already writes scanner fields to babelshark tables |
| Any DICOM/uploader service | Out of scope |

---

## 4. Data Flow Summary

### Mode A — Pre-BabelShark Watcher

```
Scanner drop folder  (e.g. /scanners/aperio_01)
        │
        ▼
QC Watcher polls input_dir (from scanner_policies[].input_dir)
scanner_id = policy.scanner_id  (folder label, not WSI metadata)
Creates FileRecord: status = detected → qc_running
        │
        ├─ pathoryx_qc_enabled=false OR trust_scanner_qc=true
        │   → write qc_results row (qc_skip_reason, pathoryx_qc_required=false)
        │   → copy/move file to passed_output_dir
        │   → FileRecord: status = qc_passed
        │
        ├─ QC Pass
        │   → copy/move to passed_output_dir (= BabelShark watch dir)
        │   → FileRecord: status = qc_passed
        │   → qc_results: qc_context=pre_babelshark, input_mode=watcher, next_service=null
        │   → BabelShark picks up file from watch dir naturally (no trigger created by QC)
        │
        └─ QC Fail / Unsupported format
            → copy/move to failed_output_dir or quarantine_dir
            → FileRecord: status = qc_failed
            → qc_results: error_reason set
```

### Mode B — Post-BabelShark Trigger

```
BabelShark completes intake
        │
        ▼  ServiceTrigger: target=qc_service, stage=qc
           trigger_payload_json = {source_path, scanner_id, scanner_name}  ← BabelShark fix
        │
QC Runner dequeues trigger
scanner_id = trigger_payload_json["scanner_id"] → lookup scanner_policies
source_path = trigger_payload_json["source_path"]
           OR file_records.current_file_path    ← fallback with warning
FileRecord: status = qc_pending → qc_running
        │
        ├─ QC Pass
        │   → FileRecord: status = qc_passed
        │   → qc_results: qc_context=post_babelshark, input_mode=trigger
        │   → ServiceTrigger dispatched to policy.next_service / policy.next_stage
        │   → Source trigger: completed
        │
        └─ QC Fail / Error
            → FileRecord: status = qc_failed
            → qc_results: error_reason set
            → Source trigger: failed (retry eligible)
```

### Mode C — QC Disabled (scanner_id: none or service.mode: disabled)

```
File arrives at BabelShark watch dir or via trigger
        │
        ▼
No QC processing.
If trust_scanner_qc=true or pathoryx_qc_enabled=false:
  → qc_results row written as skip record (pathoryx_qc_required=false)
  → file routes directly to next stage per policy
```

---

## 5. Scanner Traceability Query Examples

After implementation, every file has explicit answers to these questions:

```sql
-- Was Pathoryx QC run on this file?
SELECT
    fr.canonical_path,
    fr.scanner_id,
    qr.qc_context,
    qr.input_mode,
    qr.pathoryx_qc_required,
    qr.trust_scanner_qc,
    qr.qc_result,
    qr.qc_skip_reason,
    qr.error_reason
FROM core.file_records fr
LEFT JOIN qc.qc_results qr ON qr.file_record_internal_id = fr.internal_id
WHERE fr.scanner_id = 'aperio_scanner_01'
ORDER BY fr.created_at DESC;

-- All QC failures by scanner
SELECT scanner_id, error_reason, count(*)
FROM qc.qc_results
WHERE qc_result = 'failed'
GROUP BY scanner_id, error_reason
ORDER BY count(*) DESC;

-- Files that skipped QC (trusted scanner)
SELECT fr.original_filename, fr.scanner_id, qr.qc_skip_reason
FROM core.file_records fr
JOIN qc.qc_results qr ON qr.file_record_internal_id = fr.internal_id
WHERE qr.pathoryx_qc_required = false;
```

---

## 6. Open Decisions — Requires Sign-off Before Implementation

| # | Question | Options |
|---|----------|---------|
| 1 | `core.file_records` scanner columns | (A) Add `scanner_id` + `scanner_name` to file_records for global queryability. (B) Keep scanner identity only in `qc.qc_results` and `babelshark.extraction_results`. |
| 2 | QC skip records | (A) Always write a `qc_results` row even for skipped scanners — full audit trail. (B) Silently pass through without writing a skip row. |
| 3 | Pre-BabelShark file dispatch | (A) QC copies/moves to BabelShark watch folder; BabelShark discovers naturally. (B) QC creates a `ServiceTrigger` with `target_service=babelshark` instead. |

---

## 7. Implementation Order (once plan approved)

1. Migration `0004_qc_scanner_dual_mode.py`
2. `db/models/qc.py` + `db/models/core.py`
3. `configs/qc_service.yaml`
4. `services/qc/config.py`
5. `services/babelshark/db_writer.py` — trigger payload fix
6. `services/qc/db_writer.py`
7. `services/qc/watcher.py` (new file)
8. `services/qc/runner.py`
9. `services/qc/main.py`
