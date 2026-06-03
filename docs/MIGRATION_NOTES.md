# BabelShark Migration Notes — Legacy → Enterprise

## What Changed

### Legacy Architecture (before migration)
```
shell script / Prefect / Nextflow
  → collect_slides.py     (standalone script)
  → label_extractor.py    (standalone script)
  → datamatrix_reader.py  (standalone script)
  → stain_extractor.py    (standalone script)
  → roi_metadata_extractor.py (standalone script)
  → pasnet_validator.py   (standalone script)
  → slide_id_generator.py (standalone script)
```

Each step was a separate CLI process. Data interchange was via filesystem (Excel files,
CSV files, PNG directories). No central database. No event sourcing. No health endpoints.

### Enterprise Architecture (after migration)
```
pathoryx-babelshark (systemd / Docker)
  → runner.py (poll loop with health/metrics/shutdown)
      → collect_slides()
          → metadata_intake          (always)
          → database_manager         (always, enterprise DB)
          → [BabelSharkStageRunner]  (when enable_full_pipeline: true)
              → label_extractor      (direct call, no subprocess)
              → datamatrix_reader    (direct call)
              → stain_extractor      (direct call, in-process ROI)
              → roi_metadata_extractor (direct call, no subprocess)
              → pasnet_utilities     (direct call)
              → slide_id_generator   (direct call, enterprise DB sync)
```

## What Was NOT Changed

- **All business logic** in every legacy stage is preserved verbatim.
- **Slide ID format**: `{LabID}{Year}{Case6}S{Pot}-{BlockID}-{Section}-{Stain}`
- **PASNet matching rules**
- **Research case routing behavior**
- **Color label routing**
- **Rename logic including timestamp tag**
- **ROI layout matching and OCR pipeline**
- **DataMatrix parsing and validation rules**
- **Stain detection rules and replacement maps**

## Broken Stubs Fixed (Phase 1)

| File | Problem | Fix |
|---|---|---|
| `core/pasnet_validator.py` | `from babel_shark.pasnet_utilities.cli import main` — `babel_shark` namespace does not exist | Changed to `from .pasnet_utilities.cli import main` |
| `core/roi_metadata_extractor.py` | `from metadata_extractor_utilities.cli_main import main` — bare import fails in package context | Changed to `from .metadata_extractor_utilities.cli_main import main` |
| `core/slide_id_generator.py` `_scan_ts_iso_z_from_db` | `from db.session import SessionLocal` — wrong namespace | Changed to `from pathoryx_enterprise.db.session import get_session` + `with get_session()` |
| `core/slide_id_generator.py` `_sync_final_route_records_to_db` | `from db.models import FileRecord, EventLog` — `EventLog` does not exist in enterprise schema | Changed to enterprise imports + `EventStoreRepository.append()` |
| `core/stain_extractor.py` `run_roi_fallback_cli_for_single_image` | Called `roi_metadata_extractor.py` via subprocess — subprocess inherits broken import | Replaced with direct in-process `RoiMetadataExtractor` call |

## New Files

| File | Purpose |
|---|---|
| `services/babelshark/stage_runner.py` | Full enrichment pipeline orchestrator |
| `docs/BABELSHARK_PIPELINE.md` | Architecture reference |
| `docs/PIPELINE_STAGES.md` | Stage-by-stage reference |
| `docs/MIGRATION_NOTES.md` | This file |
| `docs/TROUBLESHOOTING.md` | Operational troubleshooting guide |
| `docs/babelshark_pipeline_gap_analysis.md` | Original gap analysis report |

## Modified Files

| File | Change |
|---|---|
| `core/database_manager.py` | Added `defer_trigger: bool = False` parameter to `register_collected_file()` |
| `core/collect_slides.py` | Added `enable_full_pipeline` feature flag + `BabelSharkStageRunner` call |
| `core/pasnet_validator.py` | Fixed import (see above) |
| `core/roi_metadata_extractor.py` | Fixed import (see above) |
| `core/slide_id_generator.py` | Fixed two legacy DB blocks (see above) |
| `core/stain_extractor.py` | Fixed ROI fallback (see above) |

## Backward Compatibility

The intake-only behavior (the smoke-tested baseline) is **fully preserved**:
- `enable_full_pipeline: false` (or omitted) → identical behavior to before migration
- The `defer_trigger` parameter to `register_collected_file` defaults to `False`
- No DB schema changes; no migration needed

## Enabling the Full Pipeline

1. Ensure all stage-specific config keys are present in the collector YAML
2. Set `enable_full_pipeline: true`
3. Enable individual stages via `pipeline_stages.*`
4. Start with `pasnet_validation: false` until PASNET credentials are configured
5. Use `dry_run: true` in `slide_id_generator` config to test routing without moving files
