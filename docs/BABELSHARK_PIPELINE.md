# BabelShark Pipeline — Architecture Reference

## Overview

BabelShark is the WSI intake and enrichment service. It watches scanner drop folders,
copies/moves slides to a staging area, extracts metadata, validates against PASNET/LIS,
generates a structured SlideID, renames the file, and routes it to the appropriate
destination before dispatching a trigger to the next service (QC or DICOM).

## Two Operating Modes

### 1. Intake-only mode (default)

```
collect_slides()
  → watch, copy/move
  → metadata_intake (OpenSlide properties)
  → database_manager.register_collected_file()
      → FileRecord created
      → EventStore appended
      → QC trigger dispatched immediately
```

Activate by omitting `enable_full_pipeline` or setting it `false`.
This is the smoke-tested, production-verified baseline.

### 2. Full enrichment mode (`enable_full_pipeline: true`)

```
collect_slides()
  → watch, copy/move
  → metadata_intake
  → database_manager.register_collected_file(defer_trigger=True)
      → FileRecord created
      → EventStore appended
      → QC trigger DEFERRED
  → BabelSharkStageRunner.run_enrichment_pipeline()
      → Stage 1: label_extraction
      → Stage 2: datamatrix
      → Stage 3: stain_extraction
      → Stage 4: roi_fallback
      → Stage 5: pasnet_validation   (optional)
      → Stage 6: slide_id_generation + rename + routing
      → QC trigger dispatched (now pointing at the renamed/routed file)
```

## Feature Flags

```yaml
# In your collector YAML config:
enable_full_pipeline: true     # activates enrichment pipeline
defer_trigger: true            # default true when enable_full_pipeline is true

pipeline_stages:
  label_extraction: true
  datamatrix: true
  stain_extraction: true
  roi_fallback: true
  slide_id_generation: true
  pasnet_validation: false     # enable only when PASNET credentials are configured
```

Setting a stage to `false` skips it entirely but does NOT prevent subsequent stages
from running. Each stage is independently toggled.

## Service Entry Point

```
main.py → BabelSharkSettings (pydantic) → runner.py → collect_slides() loop
```

The `runner.py` poll loop:
- Validates config
- Starts health/metrics servers
- Registers the runner in the DB
- Calls `collect_slides()` on each interval
- Heartbeats to `RunnerRegistration`
- Handles graceful shutdown on SIGTERM

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | required | PostgreSQL DSN |
| `BABELSHARK_CONFIG` | required | Path to collector YAML |
| `BABELSHARK_POLL_INTERVAL_SECONDS` | 60 | Collection cycle interval |
| `BABELSHARK_NEXT_STAGE` | `qc` | Stage to trigger after intake |
| `BABELSHARK_NEXT_SERVICE` | `qc_service` | Service to receive the trigger |
| `BABELSHARK_MAX_CONSECUTIVE_ERRORS` | 10 | Abort loop after N consecutive errors |
| `PATHORYX_HEALTH_PORT` | 8081 | Health/ready HTTP port |
| `PATHORYX_METRICS_PORT` | 9091 | Prometheus scrape port |
| `LOG_LEVEL` | `INFO` | Logging level |

## Database State Machine

```
detected
  → intake_running
  → intake_registered
  → qc_pending          (intake-only OR after enrichment)
  → READY_FOR_DICOMIZER (after successful slide_id_generation)
  → qc_running → qc_passed → dicom_pending → dicom_done → upload_pending → uploaded
  → *_failed → (failed_watcher monitoring)
```

## Key Source Files

| File | Role |
|---|---|
| `services/babelshark/main.py` | CLI entrypoint |
| `services/babelshark/runner.py` | Poll loop, health, metrics, runner registration |
| `services/babelshark/config.py` | Pydantic settings |
| `services/babelshark/db_writer.py` | Trigger dispatch + EventStore writes |
| `services/babelshark/stage_runner.py` | Full enrichment orchestrator |
| `services/babelshark/core/collect_slides.py` | Watch/copy/move loop |
| `services/babelshark/core/database_manager.py` | FileRecord registration (enterprise drop-in) |
| `services/babelshark/core/metadata_intake.py` | OpenSlide property extraction |
| `services/babelshark/core/label_extractor.py` | WSI label/macro PNG extraction |
| `services/babelshark/core/datamatrix_reader.py` | Barcode decode from label PNGs |
| `services/babelshark/core/stain_extractor.py` | OCR stain type detection |
| `services/babelshark/core/roi_metadata_extractor.py` | ROI fallback wrapper |
| `services/babelshark/core/metadata_extractor_utilities/` | ROI extractor implementation |
| `services/babelshark/core/slide_id_generator.py` | SlideID, rename, routing, DB sync |
| `services/babelshark/core/pasnet_validator.py` | PASNet validation wrapper |
| `services/babelshark/core/pasnet_utilities/` | PASNet implementation |
