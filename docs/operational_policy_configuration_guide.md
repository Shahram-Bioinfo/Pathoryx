# DPARS Operational Policy Configuration Guide

This guide covers all operator-facing configuration for the DPARS pipeline. It is written for system administrators and lab IT staff who need to tune pipeline behaviour, connect external systems, or prepare a deployment for production.

---

## Table of Contents

1. [Configuration System Overview](#1-configuration-system-overview)
2. [Environment Variables Reference](#2-environment-variables-reference)
3. [Database Connection](#3-database-connection)
4. [BabelShark Intake Configuration](#4-babelshark-intake-configuration)
5. [Watch Folders and Priority Queuing](#5-watch-folders-and-priority-queuing)
6. [Pipeline Stage Toggles](#6-pipeline-stage-toggles)
7. [QC Service Configuration](#7-qc-service-configuration)
8. [Recovery Sentry Configuration](#8-recovery-sentry-configuration)
9. [Routing Policy Engine](#9-routing-policy-engine)
10. [DICOM and PACS Connection](#10-dicom-and-pacs-connection)
11. [PASNET / LIS Integration](#11-pasnet--lis-integration)
12. [Path Settings and Security Roots](#12-path-settings-and-security-roots)
13. [Service Identity](#13-service-identity)
14. [Observability and Logging](#14-observability-and-logging)
15. [Dry-Run Flags](#15-dry-run-flags)
16. [Production Hardening Checklist](#16-production-hardening-checklist)

---

## 1. Configuration System Overview

DPARS uses a two-layer configuration system:

**Layer 1 — YAML config files** (in `configs/`) hold service-specific operational parameters: paths, thresholds, model weights, schedule windows. Each service loads its own file.

**Layer 2 — Environment variables** (via `.env` file or system environment) hold secrets, connection strings, and deployment-specific overrides. Environment variables always take priority over YAML defaults.

| Env var | Points to |
|---|---|
| `BABELSHARK_CONFIG_PATH` | `configs/babelshark_config.yaml` |
| `QC_CONFIG_PATH` | `configs/qc_config.yaml` |
| `DICOM_CONFIG_PATH` | `configs/dicom_config.yaml` |
| `RECOVERY_SENTRY_CONFIG` | `configs/recovery_sentry.yaml` |

These four variables can be changed to point to different files, which allows maintaining separate configs for development, staging, and production without touching the code.

> **Production safety:** Never edit `configs/dicom_config_production.yaml` for testing. That file is the live production reference with `upload.dry_run: false` and `cstore.upload_via_c_store: true`. All development uses `configs/dicom_config.yaml`.

---

## 2. Environment Variables Reference

All variables are read from the environment or the `.env` file at the repository root. Required variables have no default and the service will refuse to start without them.

### Database

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | **Yes** | — | PostgreSQL connection string: `postgresql+psycopg2://user:pass@host:port/dbname` |

### Config file paths

| Variable | Required | Default | Description |
|---|---|---|---|
| `BABELSHARK_CONFIG_PATH` | No | `configs/babelshark_config.yaml` | Path to BabelShark YAML config |
| `QC_CONFIG_PATH` | No | `configs/qc_config.yaml` | Path to QC YAML config |
| `DICOM_CONFIG_PATH` | No | `configs/dicom_config.yaml` | Path to DICOM YAML config |
| `RECOVERY_SENTRY_CONFIG` | No | `configs/recovery_sentry.yaml` | Path to Recovery Sentry YAML config |

### Dashboard

| Variable | Required | Default | Description |
|---|---|---|---|
| `DASHBOARD_HOST` | No | `127.0.0.1` | Listen address for the dashboard FastAPI server |
| `DASHBOARD_PORT` | No | `8090` | Port for the dashboard API |

### OpenSlide (Windows only)

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENSLIDE_DLL_PATH` | Windows: Yes | — | Absolute path to the OpenSlide `bin\` directory containing DLLs |

### Service identity

| Variable | Required | Default | Description |
|---|---|---|---|
| `PATHORYX_ENVIRONMENT` | No | `development` | Deployment tier label (`development`, `staging`, `production`) |
| `PATHORYX_SITE_CODE` | No | `site_local` | Site identifier stored with every pipeline event |
| `PATHORYX_SERVICE_VERSION` | No | `1.0.0` | Version label in runner registrations |

### Paths

| Variable | Required | Default | Description |
|---|---|---|---|
| `PATHORYX_RUNTIME_ROOT` | No | `/data/pathoryx/runtime` | Runtime working directory |
| `PATHORYX_OUTPUT_ROOT` | No | `/data/pathoryx/output` | Primary output directory |
| `PATHORYX_ARCHIVE_ROOT` | No | — | Optional long-term archive root |
| `PATHORYX_QUARANTINE_ROOT` | No | `/data/pathoryx/quarantine` | Quarantine for files that fail safety checks |
| `PATHORYX_FAILED_ROOT` | No | `/data/pathoryx/failed` | Failed slide holding area |
| `PATHORYX_SUSPICIOUS_ROOT` | No | `/data/pathoryx/suspicious` | Suspicious slide holding area |
| `PATHORYX_TECHNICIAN_REVIEW_ROOT` | No | `/data/pathoryx/technician_review` | Manual review queue |
| `PATHORYX_LOG_ROOT` | No | — | Optional structured log output directory |
| `PATHORYX_TEMP_ROOT` | No | `/tmp/pathoryx` | Temporary working space |
| `PATHORYX_ALLOWED_INPUT_ROOTS` | No | *(empty)* | Comma-separated list of allowed input path prefixes (see section 12) |

### Sectra PACS

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECTRA_HOST` | No | `localhost` | PACS server hostname or IP |
| `SECTRA_PORT` | No | `104` | DICOM port on the PACS server |
| `SECTRA_AE_TITLE` | No | `LOCAL_AE` | Application Entity title for this workstation |
| `SECTRA_REMOTE_AE_TITLE` | No | `REMOTE_AE` | Application Entity title of the PACS |
| `SECTRA_CSTORE_BIN` | No | `storescu` | Path to the `storescu` DICOM tool |
| `SECTRA_UPLOAD_TIMEOUT_SECONDS` | No | `1800` | Per-upload timeout (30 min default) |
| `SECTRA_CSTORE_BATCH_SIZE` | No | `500` | Max DICOM files per `storescu` invocation |

### PASNET / LIS

| Variable | Required | Default | Description |
|---|---|---|---|
| `PASNET_SERVER` | Conditional | — | LIS server address (required only when `pasnet_validation: true`) |
| `PASNET_USERNAME` | Conditional | — | LIS read-only service account username |
| `PASNET_PASSWORD` | Conditional | — | LIS service account password (never logged) |

### Logging and observability

| Variable | Required | Default | Description |
|---|---|---|---|
| `LOG_LEVEL` | No | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | No | `json` | Log format: `json` (structured) or `text` (human-readable) |
| `PROMETHEUS_METRICS_ENABLED` | No | `true` | Enable the Prometheus `/metrics` endpoint |
| `PROMETHEUS_METRICS_PORT` | No | `9090` | Port for Prometheus scrape endpoint |
| `HEALTH_HTTP_ENABLED` | No | `true` | Enable the HTTP health endpoint |
| `HEALTH_HTTP_PORT` | No | `8080` | Port for the health endpoint |
| `OTEL_ENABLED` | No | `false` | Enable OpenTelemetry tracing |
| `OTEL_SERVICE_NAME` | No | `pathoryx-enterprise` | Service name in OTEL traces |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | — | OTLP collector endpoint URL |

---

## 3. Database Connection

`DATABASE_URL` is the single required secret. Its format:

```
postgresql+psycopg2://USERNAME:PASSWORD@HOST:PORT/DATABASE
```

Example:

```
DATABASE_URL=postgresql+psycopg2://pathoryx:s3cur3pass@localhost:5432/pathoryx
```

DPARS rejects obvious placeholder passwords (`CHANGEME`, `strongpassword`, `password123`, `yourpassword`) and will fail to start if one is detected. Set a real credential.

**Connection pool settings** (rarely need changing):

| Env var | Default | Description |
|---|---|---|
| `DB_POOL_SIZE` | `10` | Sustained concurrent connections per process |
| `DB_MAX_OVERFLOW` | `20` | Extra connections allowed under peak load |
| `DB_POOL_RECYCLE` | `1800` | Seconds before recycling idle connections |
| `DB_POOL_PRE_PING` | `true` | Test connection liveness before use |
| `DB_ECHO_SQL` | `false` | Log every SQL statement (very verbose; use only for debugging) |

---

## 4. BabelShark Intake Configuration

File: `configs/babelshark_config.yaml`

BabelShark is the intake service. It watches for new WSI files, extracts identifiers, generates Slide IDs, and routes files into the pipeline.

### Top-level operational flags

```yaml
enable_full_pipeline: true     # Run all stages end-to-end
defer_trigger: true            # Enqueue into DB rather than running inline
dry_run: false                 # false = actually move files; true = simulate only
operation_mode: "copy"         # "copy" keeps the original; "move" removes it after intake
timestamp_tag_enabled: true    # Embed UTC timestamp in generated SlideIDs
watch_mode: true               # Enable folder watcher
watch_interval_minutes: 1      # How often to scan the watch directory
```

> `dry_run: false` here controls *file movement* (BabelShark). It is distinct from the routing engine's `dry_run` (section 9) and the slide ID generator's `dry_run` (section 15).

### Accepted file types

```yaml
wsi_types:
  - ".svs"
  - ".ndpi"
  - ".tif"
  - ".tiff"
  - ".scn"
  - ".mrxs"
  - ".bif"
  - ".png"
  - ".jpg"
  - ".jpeg"
```

Add or remove extensions to control which files BabelShark picks up.

### Key paths

```yaml
watch_dir: "D:/Slides/Palantir/data/watch"
staging_dir: "D:/Slides/Palantir/data/staging"
failed_output_dir: "D:/Slides/Palantir/data/failed"
suspicious_output_routine_dir: "D:/Slides/Palantir/data/suspicious"
final_output_dir: "D:/Slides/Palantir/data/final"
label_root_dir: "D:/Slides/Palantir/data/labels"
```

All paths must use forward slashes or be quoted strings on Windows.

---

## 5. Watch Folders and Priority Queuing

You can define multiple watch folders, each with an independent priority level. Files detected from a higher-priority folder move to the front of the processing queue.

```yaml
watch_folders:
  - path: "D:/Slides/Palantir/data/watch/urgent"
    priority: 1
    label: "Urgent Biopsy"
  - path: "D:/Slides/Palantir/data/watch"
    priority: 5
    label: "Routine"
```

**Priority values:**

| Value | Label | Meaning |
|---|---|---|
| `0` | STAT | Immediately dequeued before all others |
| `1` | High | Urgent; processed before routine |
| `5` | Normal | Default routine throughput |
| `9` | Low | Background / bulk imports |

Path matching is most-specific wins: a file in `watch/urgent/` matches the `urgent` entry before the parent `watch/` entry.

If `watch_folders` is absent, BabelShark falls back to the single `watch_dir` with priority `5`.

---

## 6. Pipeline Stage Toggles

Individual BabelShark stages can be enabled or disabled:

```yaml
pipeline_stages:
  label_extraction: true       # Required — extract label/macro image from WSI
  datamatrix: true             # Decode DataMatrix barcode on label
  roi_fallback: true           # Extract ROI metadata when DataMatrix fails (must run before stain)
  stain_extraction: true       # OCR-based stain detection (must run after ROI)
  slide_id_generation: true    # Build SlideID, rename file, route to output folder
  pasnet_validation: false     # LIS lookup — enable only with valid PASNET credentials
```

**Stage order is enforced by the runner.** The stages listed above reflect the required execution order. Do not disable `label_extraction` or `slide_id_generation` in a production deployment.

Optional stages (feature-flagged, disabled by default):

- `color_marker_detection` — detect colored dot markers on labels
- `extra_field_extraction` — extract additional OCR fields
- `dicom_metadata_writing` — embed metadata into DICOM output

---

## 7. QC Service Configuration

File: `configs/qc_config.yaml`

### Model weight paths

```yaml
models:
  penmark_weights: "D:/Slides/Palantir/models_weights/penmark_detection_MobileNetV3.pth"
  bubble_weights: "D:/Slides/Palantir/models_weights/bubble_detection_ConvNeXtTiny_model.pth"
  stain_weights: "D:/Slides/Palantir/models_weights/stain_model_MobileNetV3.pth"
  blur_weights: "D:/Slides/Palantir/models_weights/blur_detection_resnet18_old.pth"
```

All four `.pth` files must exist at the configured paths before `pathoryx-qc` will start.

### Module on/off switches

```yaml
modules:
  enable_stain: true       # Stain classification
  enable_penmark: true     # Pen mark detection
  enable_bubble: false     # Air bubble detection (off by default — high false-positive rate on some stains)
  enable_blur: true        # Blur/focus detection
  enable_sharpness: true   # Sharpness scoring
```

Disabling modules reduces inference time but removes that quality signal from the QC result.

### Detection thresholds

```yaml
thresholds:
  penmark_threshold: 0.01    # Fraction of tile area covered by pen marks to trigger a flag
  bubble_threshold: 0.02     # Fraction of tile area with bubble artifacts
  min_tissue_ratio: 0.03     # Minimum tissue fraction required to pass QC
  sat_threshold: 0.01        # Saturation threshold for blank-slide detection
```

Lower values are more sensitive (more flags); higher values are more permissive (fewer flags, more QC passes). Start with the defaults and tune based on your false-positive/false-negative rate.

### Inference parameters

```yaml
parameters:
  thumb_size: 1024     # Thumbnail pixel dimension for model input
  patch_size: 224      # Patch size for tile-based inference
  stride: 112          # Stride between patches (overlap = patch_size - stride)
  batch_size: 16       # GPU/CPU batch size — reduce if you encounter memory errors
```

---

## 8. Recovery Sentry Configuration

File: `configs/recovery_sentry.yaml`

Recovery Sentry watches the failed, suspicious, and manual review folders for technician-renamed files and automatically recovers valid ones back into the pipeline.

### Polling and stability

```yaml
service:
  poll_interval_seconds: 30    # How often to scan watch folders
  stable_after_seconds: 10     # Wait this many seconds after detecting a change before acting
```

`stable_after_seconds` prevents acting on a file still being written or renamed by the technician.

### Watch and destination folders

```yaml
watch_folders:
  - "D:/Slides/Palantir/data/failed"
  - "D:/Slides/Palantir/data/suspicious"
  - "D:/Slides/Palantir/data/manual_review"

final_destination_root: "D:/Slides/Palantir/data/final"
scan_subfolders: true    # true = scan date-based subdirectories; false = immediate children only
```

### Recovery behaviour

```yaml
recovery:
  auto_recover_valid_slide_id: true         # Automatically recover when a valid SlideID is detected
  add_timestamp_if_missing: true            # Extract UTC timestamp from WSI metadata if not in filename
  overwrite_existing: false                 # Never overwrite an existing file at destination
  duplicate_strategy: suffix               # "suffix" adds _1, _2...; "manual_review" leaves for review
  checksum_mode: partial                   # "partial" = first 4 MB SHA-256; "full" = whole file; "none" = skip
  allow_filesystem_timestamp_fallback: false  # true = use mtime as last-resort timestamp source
```

**`duplicate_strategy`**: In busy labs where the same case may appear twice, `suffix` is safer. Set `manual_review` if you want a human to decide when a duplicate is detected.

**`checksum_mode: partial`** is the recommended default — fast (reads only the first 4 MB) and catches all meaningful content changes on WSI files.

**`allow_filesystem_timestamp_fallback: false`** is the safe default. Filesystem modification times can be inaccurate after network copies or backup restores. Enable only if the WSI scanner does not embed a timestamp in the file metadata.

### Next-stage routing

```yaml
next_stage:
  target_service: qc_service
  stage_name: qc
```

After successful recovery the file is enqueued to QC. Do not change this unless you are modifying the pipeline topology.

---

## 9. Routing Policy Engine

Configuration section: `routing_policies` in `configs/babelshark_config.yaml`

The routing engine determines which PACS/storage destination receives each slide. Stage 1 (current) is **dry-run only**: the engine computes and audits decisions but does not alter upload destinations. Real switching requires Stage 2 code changes.

The same `routing_policies` block is present in **all three** platform config variants (`babelshark_config.yaml`, `babelshark_config.linux.yaml`, `babelshark_config.windows.yaml`) so the Routing Control Center works regardless of which file `BABELSHARK_CONFIG_PATH` points to.

### Overview

```yaml
routing_policies:
  dry_run: true                        # Stage 1: always true; do not change
  timezone: "Europe/Copenhagen"        # All schedule windows are evaluated in this timezone
  fallback_destination: "clinical_pacs"  # Used when no other rule matches
  default_mode: "clinical_day"         # Mode to use when no active mode matches the current time
```

**`timezone`** must be a valid IANA timezone identifier (e.g. `America/New_York`, `Europe/London`, `Asia/Tokyo`). An invalid timezone causes the engine to fall back to UTC with a warning in the logs.

**`default_mode`** names a mode that is used as a fallback when the current time falls in a gap between scheduled windows (e.g. between 07:00 and 08:00 in the current schedule).

### Mode windows

Modes define time-based routing schedules. Each mode is active during its `start`–`end` window in the configured timezone.

```yaml
modes:
  clinical_day:
    active:
      start: "08:00"
      end: "16:00"
    profile: "clinical"
    default_destination: "clinical_pacs"
    # Leave scanner_destinations empty to send all scanners to default_destination.
    scanner_destinations: {}
```

**Overnight modes** (end ≤ start) automatically wrap past midnight:

```yaml
  research_night:
    active:
      start: "16:00"
      end: "07:00"       # Active 16:00 through 06:59 the next morning
    profile: "research"
    default_destination: "research_storage"
    scanner_destinations:
      M40010:
        destination: "research_storage_resolute"
      M40015:
        destination: "research_storage_avenger"
      M40023:
        destination: "research_storage_homeone"
      M40024:
        destination: "research_storage_chimera"
      SS12620R:
        destination: "research_storage_stardestroyer"
```

**Scanner IDs** must match the `scanner_id` values in `configs/scanner_fleet.yaml` exactly:

| scanner_id | Display name | Location |
|---|---|---|
| `M40010` | Resolute | lab-main |
| `M40015` | Avenger | lab-main |
| `M40023` | HomeOne | lab-secondary |
| `M40024` | Chimera | lab-secondary |
| `SS12620R` | StarDestroyer Devastator | lab-main |

**`scanner_destinations`** — if a scanner is not listed in the active mode, the mode's `default_destination` is used. During `clinical_day` all scanners go to `clinical_pacs` via the empty `scanner_destinations: {}`.

**`default_destination`** is required for every mode. Omitting it is a validation error visible on the Routing Control Center page and in `pathoryx-validate-routing`.

### How to change clinical/research time windows

Edit the `active.start` and `active.end` values in `configs/babelshark_config.yaml` (and the same block in the linux/windows variants):

```yaml
# Example: extend clinical day to 18:00
    clinical_day:
      active:
        start: "08:00"
        end: "18:00"      # ← change this

    research_night:
      active:
        start: "18:00"    # ← must match new clinical_day end
        end: "07:00"
```

After saving, restart `pathoryx-dashboard` and `pathoryx-babelshark`. Verify with:

```
python -m pathoryx_enterprise.services.routing.validate_config
```

### How to change scanner destinations

Edit `scanner_destinations` under the relevant mode in any config variant. Scanner IDs must match `scanner_fleet.yaml` exactly (case-sensitive):

```yaml
    research_night:
      scanner_destinations:
        M40010:
          destination: "new_destination_name"   # ← change destination string
```

Destination strings are free-form labels. They are stored in `routing.routing_decisions` for audit but do not affect actual PACS connections while `dry_run: true`.

### How to change color-dot destinations

Edit `color_dot_rules` in the `routing_policies` block:

```yaml
  color_dot_rules:
    red:
      destination: "urgent_research"    # ← change destination
    blue:
      destination: "research_project_blue"
```

Add new colors by adding new keys. Color names are matched case-insensitively. Remove a rule by deleting its key.

### Color-dot routing rules (current defaults)

```yaml
color_dot_rules:
  red:
    destination: "urgent_research"
  blue:
    destination: "research_project_blue"
  green:
    destination: "research_project_green"
  yellow:
    destination: "clinical_special"
```

Color-dot routing takes priority over scanner-policy and mode-default routing but is overridden by manual dashboard overrides.

### Why dry_run must stay true before Stage 2

`dry_run: true` is a Stage 1 contract: the engine records what it *would* route but does not change any upload destination. This is enforced in the engine code — the `dry_run=True` field is hardcoded in every `RoutingResult` returned by Stage 1 code, regardless of the config value.

Changing `dry_run: false` in the YAML has no effect in Stage 1. The field only becomes operative when Stage 2 code (real destination switching) is released and the engine hardcode is removed. This design prevents accidental activation of live routing by a config edit alone.

### How to verify the dashboard loaded the correct config

**Option 1 — CLI validator** (run from the repo root):

```
python -m pathoryx_enterprise.services.routing.validate_config
```

This prints the active config path, loaded modes, scanner destinations, color-dot rules, dry-run status, and any validation errors.

**Option 2 — dashboard API**:

```
curl http://localhost:8090/dashboard/api/routing/status
```

A working response includes `"dry_run": true` and a `"modes"` array with `clinical_day` and `research_night`. If the response contains `"validation_issues"` with `"routing_policies section missing"`, the loaded config file has no `routing_policies` block.

**Option 3 — Routing Control Center page**: open `/routing` in the dashboard. A correctly loaded config shows the DRY-RUN banner, two operational modes, four color-dot swatches, and `clinical_pacs` as the fallback destination.

### Routing priority chain (highest to lowest)

| Priority | Trigger |
|---|---|
| 1 | Emergency override created in the dashboard (targets a specific scanner or file) |
| 2 | Color-dot rule matching the slide's detected color marker |
| 3 | Scanner policy within the active mode |
| 4 | Active mode's `default_destination` |
| 5 | Global `fallback_destination` |

### Dashboard override API

Operators can create temporary overrides through the **Routing Control Center** page (`/routing`) in the dashboard. Overrides accept:

- `target_type`: `scanner`, `file`, or `case`
- `target_value`: the scanner ID, file ID, or case number to match
- `destination`: where to route matching slides
- `expires_at`: optional ISO timestamp when the override expires automatically
- `reason` and `operator`: for the audit trail

Overrides are stored in the `routing.routing_overrides` table and deactivate automatically when expired.

---

## 10. DICOM and PACS Connection

File: `configs/dicom_config.yaml`

> **Do not use `configs/dicom_config_production.yaml` for testing.** That file enables real uploads with `upload.dry_run: false` and `cstore.upload_via_c_store: true`.

Key settings in `configs/dicom_config.yaml`:

```yaml
upload:
  dry_run: true                  # true = convert but do not upload; false = actually send to PACS

cstore:
  upload_via_c_store: false      # false = skip C-STORE; true = send via storescu
  ae_title: LOCAL_AE             # Local AE title (also set via SECTRA_AE_TITLE env var)
  remote_ae_title: REMOTE_AE     # PACS AE title (also set via SECTRA_REMOTE_AE_TITLE env var)
```

PACS connection details (`SECTRA_HOST`, `SECTRA_PORT`, etc.) are set via environment variables (section 2) and take priority over any values in the YAML config.

The `storescu` binary must be on the system PATH or its full path provided in `SECTRA_CSTORE_BIN`.

---

## 11. PASNET / LIS Integration

PASNET validation checks each slide's case number against the Laboratory Information System (LIS) database before the slide ID is generated. It is **disabled by default** (`pasnet_validation: false` in `pipeline_stages`).

To enable, set the following in `babelshark_config.yaml`:

```yaml
pipeline_stages:
  pasnet_validation: true

pasnet_validator:
  enabled: true
  mode: "pre_rename"       # Run validation before file is renamed
  dry_run: false           # false = treat LIS failures as actual errors
  fail_open: true          # true = allow slide through if PASNET is unreachable
  server_env: "PASNET_SERVER"      # Env var containing the LIS server address
  username_env: "PASNET_USERNAME"  # Env var containing the LIS username
  password_env: "PASNET_PASSWORD"  # Env var containing the LIS password (never logged)
```

And set the corresponding environment variables in `.env`:

```dotenv
PASNET_SERVER=lis.hospital.internal
PASNET_USERNAME=lis_reader
PASNET_PASSWORD=YourLISPassword
```

**`fail_open: true`** (recommended): if the LIS server is unreachable, the slide is given a `PASNET_UNAVAILABLE` status and allowed to continue. Set to `false` if LIS validation is a hard gate and slides must not proceed without it.

---

## 12. Path Settings and Security Roots

DPARS enforces that all file operations happen within declared allowed roots. This prevents path traversal attacks when file paths come from external inputs (scanner filenames, dashboard API calls).

Set `PATHORYX_ALLOWED_INPUT_ROOTS` to a comma-separated list of base directories that DPARS is permitted to read from:

```dotenv
PATHORYX_ALLOWED_INPUT_ROOTS=D:\Slides\Palantir\data,E:\Archive
```

Any operation attempting to access a path outside these roots is rejected. In development with no value set, this check is permissive — set it in production.

`PathSettings` also exposes individual roots for each data zone. Set them via environment variables if your data is spread across multiple drives or network shares:

```dotenv
PATHORYX_OUTPUT_ROOT=D:\Slides\Palantir\data\final
PATHORYX_FAILED_ROOT=D:\Slides\Palantir\data\failed
PATHORYX_SUSPICIOUS_ROOT=D:\Slides\Palantir\data\suspicious
PATHORYX_QUARANTINE_ROOT=D:\Slides\Palantir\data\quarantine
```

---

## 13. Service Identity

These variables tag every database record and log line written by a service instance. Useful when running multiple instances across machines or sites.

```dotenv
PATHORYX_ENVIRONMENT=production     # development | staging | production
PATHORYX_SITE_CODE=oslo_main        # Short label for this physical site
PATHORYX_SERVICE_VERSION=2.1.0      # Application version deployed here
```

`runner_id` is generated automatically per process — no configuration needed.
`host_id` is taken from the machine hostname automatically.

---

## 14. Observability and Logging

### Log level and format

```dotenv
LOG_LEVEL=INFO         # DEBUG for troubleshooting; WARNING for quieter production logs
LOG_FORMAT=json        # json = structured (for log aggregators); text = human-readable
```

### Prometheus metrics

Each service exposes a `/metrics` endpoint for Prometheus scraping:

```dotenv
PROMETHEUS_METRICS_ENABLED=true
PROMETHEUS_METRICS_PORT=9090    # Override per-service if running multiple on one host
```

Default scrape ports by service:

| Service | Metrics port |
|---|---|
| BabelShark | 9091 |
| QC | 9092 |
| DICOM | 9093 |
| Uploader | 9094 |
| Recovery Sentry | 9097 |

### HTTP health endpoints

```dotenv
HEALTH_HTTP_ENABLED=true
HEALTH_HTTP_PORT=8080    # Base port; each service adds an offset (see CLAUDE.md service table)
```

### OpenTelemetry tracing

```dotenv
OTEL_ENABLED=true
OTEL_SERVICE_NAME=dpars-babelshark
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.internal:4317
```

Set `OTEL_ENABLED=false` (the default) unless you have an OTLP-compatible collector deployed.

---

## 15. Dry-Run Flags

DPARS has three independent dry-run controls. They operate at different layers and must each be set correctly for a production deployment.

| Config key | Location | Default | Effect when `true` |
|---|---|---|---|
| `dry_run` (top-level) | `babelshark_config.yaml` | `false` | BabelShark simulates file moves/copies; no files are moved |
| `slide_id_generator.dry_run` | `babelshark_config.yaml` | `true` | Slide IDs are generated and logged but the file is not renamed or moved |
| `routing_policies.dry_run` | `babelshark_config.yaml` | `true` | Routing engine audits decisions but does not change upload destinations |
| `upload.dry_run` | `dicom_config.yaml` | `true` | DICOM conversion runs but converted files are not sent to PACS |
| `pasnet_validator.dry_run` | `babelshark_config.yaml` | `true` | LIS lookups run but failures do not block processing |

**For a production deployment**, set all five to `false` — but do so one at a time and verify each stage before enabling the next. The recommended progression:

1. Start with all dry-runs enabled (`true`). Confirm the pipeline processes files correctly end-to-end without moving anything.
2. Set `slide_id_generator.dry_run: false`. Verify files are renamed correctly.
3. Set top-level `dry_run: false`. Files now move through the pipeline.
4. Set `upload.dry_run: false` in `dicom_config.yaml`. DICOM files now upload to PACS.
5. Set `routing_policies.dry_run: false` only after Stage 2 of the routing engine is released.

---

## 16. Production Hardening Checklist

Before going live, verify each item:

- [ ] `DATABASE_URL` uses a non-placeholder password and a dedicated `pathoryx` database user
- [ ] `.env` file is excluded from version control (`.gitignore` must include `.env`)
- [ ] `PATHORYX_ENVIRONMENT=production` is set
- [ ] `PATHORYX_SITE_CODE` is set to the correct site identifier
- [ ] `PATHORYX_ALLOWED_INPUT_ROOTS` is set to the actual data root paths
- [ ] `LOG_FORMAT=json` with a log aggregator (ELK, Grafana Loki, etc.) collecting logs
- [ ] `LOG_LEVEL=INFO` (not `DEBUG` — debug logging includes slide paths and is verbose)
- [ ] `SECTRA_HOST` and `SECTRA_REMOTE_AE_TITLE` point to the production PACS
- [ ] `configs/dicom_config.yaml` has `upload.dry_run: false` and `cstore.upload_via_c_store: true`
- [ ] `slide_id_generator.dry_run: false` in `babelshark_config.yaml`
- [ ] Top-level `dry_run: false` in `babelshark_config.yaml`
- [ ] `routing_policies.dry_run: true` kept until Stage 2 routing is validated
- [ ] `PASNET_PASSWORD` is set via environment variable, not hardcoded in any YAML file
- [ ] All data directories exist and the service process account has read/write access
- [ ] `OPENSLIDE_DLL_PATH` is set (Windows) or OpenSlide is installed (Linux)
- [ ] At least one Prometheus scrape target is configured to alert on missed heartbeats
- [ ] `pathoryx-migrate` was run against the production database before first start
