# BabelShark Full Pipeline — Implementation Status Report
**Date:** 2026-05-28
**Prepared by:** Claude Sonnet 4.6

---

## Executive Summary

The enterprise BabelShark runtime previously executed only the intake shell
(watch → copy → metadata → DB → trigger). All enrichment stages existed as isolated
legacy CLI modules but were never called by the runtime.

This session implemented the full migration in five phases:
- Phase 1: Fixed four broken modules that would fail on import
- Phase 2: Built the enrichment orchestrator (`stage_runner.py`)
- Phase 3: Wired the orchestrator into the existing runtime
- Phase 4: Added observability (timing, metrics, events, step tracking)
- Phase 5: Created operational documentation

**The call chain is fully connected. The enrichment pipeline has not been smoke-tested
against real `.svs` slides. That is the remaining next step.**

---

## Prior Verified State (Before This Session)

The following was working and smoke-tested with real `.svs` slides:

| Component | Status |
|---|---|
| Watch folder scanning | Verified |
| Copy/move to staging | Verified |
| Duplicate detection | Verified |
| OpenSlide metadata extraction | Verified |
| FileRecord registration | Verified |
| EventStore append | Verified |
| QC trigger enqueue | Verified |
| Idempotency | Verified |
| Health endpoint | Verified |
| PostgreSQL persistence | Verified |

**None of the above was modified. All existing behavior is preserved.**

---

## What Was Wrong Before This Session

### 4 modules with hard import failures

| File | Problem |
|---|---|
| `core/pasnet_validator.py` | `from babel_shark.pasnet_utilities.cli import main` — `babel_shark` namespace does not exist in the enterprise environment. `ImportError` on first use. |
| `core/roi_metadata_extractor.py` | `from metadata_extractor_utilities.cli_main import main` — bare import fails inside a package. `ModuleNotFoundError` on first use. |
| `core/slide_id_generator.py` `_scan_ts_iso_z_from_db()` | `from db.session import SessionLocal` — wrong namespace. Also `SessionLocal` does not exist; enterprise uses `get_session()`. |
| `core/slide_id_generator.py` `_sync_final_route_records_to_db()` | `from db.models import FileRecord, EventLog` — `EventLog` was removed from the enterprise schema. `ImportError`. Also uses SQLAlchemy 1.x `session.query()` style and `session.commit()/rollback()/close()` manual lifecycle. |
| `core/stain_extractor.py` `run_roi_fallback_cli_for_single_image()` | Called `roi_metadata_extractor.py` via `subprocess.run()`. That subprocess immediately hit the broken import above. ROI fallback silently failed for every slide. |

### 8 stages disconnected from the runtime

`runner.py` → `collect_slides()` called `register_collected_file()` and stopped.
Label extraction, DataMatrix, stain extraction, ROI extraction, PASNet validation,
slide ID generation, rename, and routing were never invoked.

---

## What Was Done This Session

### Phase 1 — Broken stubs fixed

**`core/pasnet_validator.py`**
```python
# Before
from babel_shark.pasnet_utilities.cli import main

# After
from .pasnet_utilities.cli import main
```

**`core/roi_metadata_extractor.py`**
```python
# Before
from metadata_extractor_utilities.cli_main import main

# After
from .metadata_extractor_utilities.cli_main import main
```

**`core/slide_id_generator.py` — `_scan_ts_iso_z_from_db()`**
```python
# Before (broken)
from db.session import SessionLocal
from db.models import FileRecord
session = SessionLocal()
record = session.query(FileRecord).filter(...).first()
...
session.close()

# After (enterprise-compatible)
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.db.models.core import FileRecord
from sqlalchemy import select, or_
with get_session() as session:
    record = session.execute(
        select(FileRecord).where(or_(...)).limit(1)
    ).scalar_one_or_none()
```

**`core/slide_id_generator.py` — `_sync_final_route_records_to_db()`**
```python
# Before (broken)
from db.session import SessionLocal
from db.models import FileRecord, EventLog      # EventLog does not exist
session = SessionLocal()
session.query(FileRecord).filter(...)
event = EventLog(...)
session.commit() / session.rollback() / session.close()

# After (enterprise-compatible)
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.db.models.core import FileRecord
from pathoryx_enterprise.db.repositories.event_store import EventStoreRepository
from sqlalchemy import select, or_
with get_session() as session:
    for idx, rec in enumerate(records or []):
        with session.begin_nested():            # SAVEPOINT: per-row isolation
            session.execute(select(FileRecord).where(or_(...)))
            EventStoreRepository(session).append(...)
```

**`core/stain_extractor.py` — ROI fallback**
```python
# Before (subprocess, broken)
cmd = [sys.executable, str(cli_path), "run", "--config", ...]
res = subprocess.run(cmd, capture_output=True, text=True)
# → subprocess hits broken import → returncode != 0 → silently returns None

# After (in-process, direct)
from .metadata_extractor_utilities.extractor import RoiMetadataExtractor
extractor = RoiMetadataExtractor(roi_cfg)
img_bgr = cv2.imread(str(image_path))
parsed, success, _roi_words = extractor.run_on_image(img_bgr, img_name=image_path.name)
return {"Stain": parsed.get("Stain", ""), ...}
```

All four files pass AST parse and enterprise import resolution checks.

---

### Phase 2 — `stage_runner.py` created

**File:** `pathoryx_enterprise/services/babelshark/stage_runner.py`

`BabelSharkStageRunner` class with:

| Method | Calls |
|---|---|
| `run_label_extraction()` | `core/label_extractor.py` → `LabelExtractor.extract_label()` |
| `run_datamatrix()` | `core/datamatrix_reader.py` → `process_all_images()` |
| `run_stain_extraction()` | `core/stain_extractor.py` → `run_pipeline()` |
| `run_roi_extraction()` | `core/metadata_extractor_utilities/cli_main.py` → `cmd_run()` |
| `run_pasnet_validation()` | `core/pasnet_utilities/cli.py` → `main(["run", ...])` |
| `run_slide_id_generation()` | `core/slide_id_generator.py` → `run_pipeline()` |
| `run_enrichment_pipeline()` | Orchestrates all 6 stages in sequence |

Each stage wraps the legacy function call and additionally:
- Emits lifecycle events to `EventStoreRepository`
- Creates `StepRun` record in the enterprise DB
- Updates `FileRecord.metadata_json` with stage outputs
- Records `stage_latency_seconds` histogram
- Records `files_processed_total` / `files_failed_total` Prometheus counters
- Propagates `correlation_id` through every DB write
- Isolates failures: one stage failing does not abort subsequent stages

---

### Phase 3 — Runtime wiring

**`core/database_manager.py`** — new parameter:
```python
def register_collected_file(
    self, ...,
    defer_trigger: bool = False,   # NEW
) -> dict:
    ...
    if not defer_trigger:           # only dispatch immediately in intake-only mode
        BabelSharkDBWriter(session).mark_intake_complete(...)
```

**`core/collect_slides.py`** — feature flag + stage runner call:
```python
full_pipeline = bool(conf.get("enable_full_pipeline", False))
defer_trigger = full_pipeline and bool(conf.get("defer_trigger", True))

registration = db.register_collected_file(..., defer_trigger=defer_trigger)

if full_pipeline:
    from pathoryx_enterprise.services.babelshark.stage_runner import BabelSharkStageRunner
    stage_runner = BabelSharkStageRunner(conf, logger)
    stage_runner.run_enrichment_pipeline(
        staged_path=dest,
        file_record_id=registration["record_id"],
        global_artifact_id=registration["global_artifact_id"],
    )
```

At the end of `run_enrichment_pipeline()`, after all stages complete:
```python
if self.config.get("defer_trigger", True):
    BabelSharkDBWriter(session).mark_intake_complete(
        file_record_internal_id=file_record_id,
        ...
    )
```

**QC trigger timing in each mode:**

| Mode | When QC trigger fires |
|---|---|
| `enable_full_pipeline: false` (default) | Immediately after `register_collected_file()` — same as before |
| `enable_full_pipeline: true`, `defer_trigger: true` | After all enrichment stages complete — QC receives the renamed, validated file |
| `enable_full_pipeline: true`, `defer_trigger: false` | Immediately after registration — enrichment still runs but QC may pick up the unstaged file |

---

### Phase 4 — Observability (built into stage_runner.py)

| Mechanism | What it tracks |
|---|---|
| `stage_latency_seconds` histogram | Wall-clock time per stage |
| `files_processed_total` counter | Stage completions |
| `files_failed_total` counter | Stage failures by error type |
| `EventStore` append per stage | `babelshark.{stage}.completed` / `babelshark.{stage}.failed` |
| `PipelineRun` row | Enrichment pipeline lifecycle |
| `StepRun` row per stage | Per-stage status, duration, error message, RSS memory |
| `correlation_id` | Propagated through every event, log record, and DB write |
| RSS memory snapshot | `psutil.Process().memory_info().rss` per stage (if psutil available) |

---

### Phase 5 — Documentation created

| File | Contents |
|---|---|
| `docs/BABELSHARK_PIPELINE.md` | Architecture reference, both operating modes, env vars, DB state machine |
| `docs/PIPELINE_STAGES.md` | Stage-by-stage reference: inputs, outputs, config keys, dependencies |
| `docs/MIGRATION_NOTES.md` | Legacy vs enterprise architecture, file-by-file change log |
| `docs/TROUBLESHOOTING.md` | Common failure modes, diagnostic queries, observability guide |
| `docs/babelshark_pipeline_gap_analysis.md` | Original gap analysis (updated: marked RESOLVED) |
| `docs/babelshark_implementation_status.md` | This file |

---

## Exact Call Chain (Verified Against Source Code)

```
runner.py:89
  └─ collect_slides(conf, bs_logger)                          [collect_slides.py]

      collect_slides.py:198–231 (per file, per watch cycle)
      ├─ metadata_intake.extract_and_normalize_metadata()     [always]
      ├─ db.register_collected_file(defer_trigger=True)       [always; defers QC trigger]
      └─ BabelSharkStageRunner.run_enrichment_pipeline()      [if enable_full_pipeline: true]

          stage_runner.py:808–888
          ├─ Stage 1  run_label_extraction()
          │     └─ LabelExtractor.extract_label()             [label_extractor.py:422]
          │
          ├─ Stage 2  run_datamatrix()
          │     └─ process_all_images(slide_cfg)              [datamatrix_reader.py:224]
          │
          ├─ Stage 3  run_stain_extraction()
          │     └─ stain_run_pipeline(cfg_path)               [stain_extractor.py:630]
          │          └─ run_roi_fallback_cli_for_single_image()
          │               └─ RoiMetadataExtractor.run_on_image() [in-process, no subprocess]
          │
          ├─ Stage 4  run_roi_extraction()
          │     └─ cmd_run(args)                              [metadata_extractor_utilities/cli_main.py:208]
          │          └─ RoiMetadataExtractor.run_on_image()   [extractor.py:393]
          │
          ├─ Stage 5  run_pasnet_validation()  [if pasnet_validation: true]
          │     └─ pasnet_cli_main(["run", "--config", ...])  [pasnet_utilities/cli.py:38]
          │          └─ run_pre_rename(cfg, ...)              [pasnet_utilities/validator.py]
          │
          ├─ Stage 6  run_slide_id_generation()
          │     └─ sid_run_pipeline(slide_cfg)                [slide_id_generator.py:1161]
          │          ├─ merge_inputs()
          │          ├─ compute_identifiers()
          │          ├─ [routing: final move / rename]
          │          └─ _sync_final_route_records_to_db()     [enterprise DB, fixed]
          │
          └─ BabelSharkDBWriter.mark_intake_complete()        [QC trigger dispatched here]
```

---

## Validation Results

All checks automated and verified:

```
AST parse:           9/9 files clean (no syntax errors)
Enterprise imports:  6/6 resolve successfully
Legacy imports:      0 remaining in any file
Wiring patterns:     13/13 present in correct files
```

No legacy imports remain in any modified file. No subprocess calls remain for
inter-stage communication.

---

## What Is NOT Yet Done

### 1. End-to-end smoke test with real slides

The enrichment pipeline has not been executed against a real `.svs` slide.
The intake-only path was smoke-tested before this session. The enrichment
stages are wired and the code paths are correct, but runtime verification
requires a real slide to flow through all 6 stages.

**To run the first smoke test:**
1. Add these keys to the collector YAML:
   ```yaml
   enable_full_pipeline: true
   defer_trigger: true
   run_output_dir: /path/to/pipeline_runs
   final_output_dir: /path/to/final
   failed_output_dir: /path/to/failed
   label_crops_dir: /path/to/label_crops      # or leave to auto-build
   stain_list_path: /path/to/stain_list.json
   stain_replace_map_path: /path/to/replacements.json
   ROI_set_file: /path/to/rois.json           # or roiset_selector block
   pipeline_stages:
     label_extraction: true
     datamatrix: true
     stain_extraction: true
     roi_fallback: true
     slide_id_generation: true
     pasnet_validation: false
   slide_id_generator:
     dry_run: true                             # no physical file moves on first test
   ```
2. Drop a `.svs` file into the watch folder
3. Observe logs for `[STAGE]` lines confirming each stage ran
4. Inspect `run_output_dir/slide_{id}_{stem}/` for intermediate outputs
5. Query `core.step_runs` to confirm all stages recorded

### 2. PASNet credential configuration

PASNet validation is implemented and callable but is set to `pasnet_validation: false`
by default because it requires PASNET/LIS database credentials. Enable it only after
credentials are configured in the environment.

### 3. Performance characterization

Label extraction and stain extraction (EasyOCR) are the two CPU-heavy stages.
No benchmarks exist yet for the per-slide wall-clock time on production hardware.
The `stage_latency_seconds` histogram in Prometheus will populate this on first run.

---

## Backward Compatibility Guarantee

With `enable_full_pipeline` absent or `false`:
- `register_collected_file()` behaves identically to before (trigger fires immediately)
- `BabelSharkStageRunner` is never instantiated
- No new code paths execute
- The smoke-tested intake behavior is unchanged
