# BabelShark Pipeline Gap Analysis
**Date:** 2026-05-28
**Status:** RESOLVED — All phases implemented. See MIGRATION_NOTES.md.
**Analyst:** Claude Sonnet 4.6
**Scope:** Enterprise runtime vs. legacy BabelShark full pipeline

---

## Executive Summary

The new enterprise runtime executes only **stages 1–3 of an 11-stage pipeline**. Every stage
from label extraction onward is present in the codebase as isolated CLI modules but is **never
called by the runtime**. The pipeline halts at `collect_slides()` and immediately dispatches a
trigger to QC — skipping all enrichment, validation, renaming, and routing that legacy
BabelShark performed before handing a slide off.

---

## Stage-by-Stage Status

| Stage | Component | Status | Evidence |
|---|---|---|---|
| 1. Watch | `collect_slides.py` | Connected | Called by `runner.py:89` |
| 2. Copy/Move | `collect_slides.py` | Connected | `atomic_copy/atomic_move` in loop |
| 3. Basic metadata extraction | `metadata_intake.py` | Connected | `extract_and_normalize_metadata()` at `collect_slides.py:189` |
| 4. DB registration | `database_manager.py` | Connected | `register_collected_file()` at `collect_slides.py:198` |
| 5. Trigger dispatch | `db_writer.py` | Connected | `mark_intake_complete()` inside `register_collected_file()` |
| 6. **Label extraction** | `label_extractor.py` | **Disconnected** | `LabelExtractor` / `run_extraction()` never imported or called anywhere in the runtime |
| 7. **DataMatrix reading** | `datamatrix_reader.py` | **Disconnected** | `run_with_config()` / `process_all_images()` never called |
| 8. **Stain extraction** | `stain_extractor.py` | **Disconnected** | `run_pipeline()` never called |
| 9. **ROI extraction** | `roi_metadata_extractor.py` | **Disconnected + Broken** | Wrapper only; bare import `from metadata_extractor_utilities.cli_main import main` fails in enterprise env; `stain_extractor.py` calls it via subprocess — also broken |
| 10. **Slide ID generation** | `slide_id_generator.py` | **Disconnected + Broken** | `run_pipeline()` never called; uses legacy imports `from db.session import SessionLocal` and `from db.models import FileRecord, EventLog` — wrong namespace, `EventLog` removed from enterprise schema |
| 11. **PASNet validation** | `pasnet_validator.py` | **Disconnected + Broken** | Wrapper imports `from babel_shark.pasnet_utilities.cli import main` — `babel_shark` namespace does not exist in enterprise env; always a hard `ImportError` |
| 12. **Rename** | `slide_id_generator.py` | **Disconnected** | Part of `run_pipeline()`, never executed |
| 13. **Routing / Final move** | `slide_id_generator.py` | **Disconnected** | Same |

---

## Root Cause

**The runtime calls exactly one function: `collect_slides(conf, logger)`.**

```
runner.py:89 → collect_slides(conf, bs_logger)
                └── metadata_intake.extract_and_normalize_metadata()   [basic OpenSlide props only]
                └── database_manager.register_collected_file()
                        └── BabelSharkDBWriter.mark_intake_complete()  [trigger → QC immediately]
```

The pipeline **stops here**. `collect_slides()` was designed as an intake/staging step, not as
an orchestrator for the full enrichment pipeline. The remaining 8 stages were intended to run
as separate sequential processes driven by either a legacy shell runner script or a
Prefect/Nextflow workflow — neither of which was ported into the enterprise runtime.

---

## Broken Components (Beyond "Just Not Called")

### 1. `pasnet_validator.py` — Hard import failure

```python
# services/babelshark/core/pasnet_validator.py:9
from babel_shark.pasnet_utilities.cli import main
```

The `babel_shark` top-level package does not exist in the enterprise installation. This file is
a broken stub — importing it raises `ModuleNotFoundError` immediately. The actual implementation
lives at `core/pasnet_utilities/` and is accessible via relative import.

**Fix:** `from .pasnet_utilities.cli import main`

---

### 2. `roi_metadata_extractor.py` — Hard import failure

```python
# services/babelshark/core/roi_metadata_extractor.py:9
from metadata_extractor_utilities.cli_main import main
```

Bare import, not relative. Works only if `metadata_extractor_utilities` is explicitly on
`sys.path`. Fails in the enterprise package context. The actual package is at
`core/metadata_extractor_utilities/`.

**Fix:** `from .metadata_extractor_utilities.cli_main import main`

---

### 3. `slide_id_generator.py` — Legacy DB imports will fail and corrupt state

```python
# slide_id_generator.py:389
from db.session import SessionLocal
from db.models import FileRecord

# slide_id_generator.py:976-978
from db.session import SessionLocal
from db.models import FileRecord, EventLog
```

Three separate issues:

- Wrong module path (`db.session` vs `pathoryx_enterprise.db.session`)
- `SessionLocal` does not exist — enterprise uses `get_session()` context manager
- `EventLog` was replaced by `EventStore` in the enterprise migration; importing it
  raises `ImportError`

Both calls are inside functions that catch all exceptions silently
(`_scan_ts_iso_z_from_db` returns `None` on failure;
`_sync_final_route_records_to_db` returns an error dict). This means if slide ID
generation were reached, file routing would execute but **no DB state would update**,
leaving FileRecords permanently stuck at `intake_registered`.

---

### 4. `stain_extractor.py` — ROI subprocess fallback is broken

```python
# stain_extractor.py:330-363
cli_path = Path(__file__).resolve().parent / "roi_metadata_extractor.py"
cmd = [sys.executable, str(cli_path), "run", "--config", ...]
res = subprocess.run(cmd, ...)
```

Calls `roi_metadata_extractor.py` as a subprocess which immediately fails with the import
error described above. The subprocess returns non-zero; the fallback silently no-ops. Stain
results fall back to primary OCR only — no ROI double-check — with no error surfaced in logs.

---

## What `metadata_intake.py` Is vs. What It Replaces

`metadata_intake.py` (currently active) is a **lightweight OpenSlide property reader** —
it extracts scanner vendor, magnification, MPP, and scan timestamps from WSI file headers.
It is **not a replacement** for:

| Legacy Stage | What it does | `metadata_intake.py` equivalent |
|---|---|---|
| `label_extractor.py` | Visual crop of the physical label image from the WSI associated images | None — file-header read only |
| `datamatrix_reader.py` | Computer-vision barcode decode from label PNG | None |
| `stain_extractor.py` | EasyOCR text recognition for stain type from label PNG | None |
| `roi_metadata_extractor.py` | ROI-based fallback OCR for slides where DataMatrix fails | None |

These are sequential stages that depend on each other's outputs:
`label PNG → DataMatrix Excel → Stain Excel → SlideID Excel → routed file`.

None of them are substituted by `metadata_intake.py`.

---

## Migration Plan

### Guiding Principle

Do not modify `collect_slides()`, `database_manager.py`, `db_writer.py`, or the runner loop.
Introduce a `BabelSharkStageRunner` that takes a staged file already registered in the DB and
executes the remaining stages, writing results back through the enterprise DB/event system.

---

### Phase 1 — Fix broken stubs (no behavior change, no risk)

**Target files:** `pasnet_validator.py`, `roi_metadata_extractor.py`, `slide_id_generator.py`

1. `pasnet_validator.py:9` — change to relative import `from .pasnet_utilities.cli import main`
2. `roi_metadata_extractor.py:9` — change to relative import
   `from .metadata_extractor_utilities.cli_main import main`
3. `slide_id_generator.py` — replace both legacy DB blocks:
   - `from db.session import SessionLocal` → `from pathoryx_enterprise.db.session import get_session`
   - `from db.models import FileRecord, EventLog` →
     `from pathoryx_enterprise.db.models.core import FileRecord` +
     `from pathoryx_enterprise.db.repositories.event_store import EventStoreRepository`
   - Replace `SessionLocal()` session pattern with `get_session()` context manager

These are dead code today (never called), so the fixes are safe and unblock Phase 2.

---

### Phase 2 — Build `BabelSharkStageRunner` (orchestration layer)

Create `pathoryx_enterprise/services/babelshark/stage_runner.py`:

```python
class BabelSharkStageRunner:
    def run_label_extraction(self, staged_path, config) -> Path          # label PNG dir
    def run_datamatrix(self, label_png_dir, config) -> pd.DataFrame
    def run_stain_extraction(self, label_png_dir, config) -> pd.DataFrame
    def run_roi_extraction(self, failed_dm_dir, config) -> pd.DataFrame
    def run_pasnet_validation(self, dm_df, stain_df, config) -> pd.DataFrame
    def run_slide_id_generation(self, dm_df, stain_df, config) -> List[RouteRecord]
    def run_enrichment_pipeline(self, staged_path, file_record_id) -> None
```

Each step:
- Calls the existing legacy function **directly** (not via subprocess)
- Catches exceptions and emits a structured failure event via `EventStoreRepository`
- Updates `FileRecord.status` and appends to `metadata_json` via `BabelSharkDBWriter`
- Creates a `StepRun` record so the pipeline is observable and restartable
- Returns its output so the next step can consume it in-process

---

### Phase 3 — Wire stages into `collect_slides()` post-registration

Add a single optional call after `db.register_collected_file()` returns:

```python
# In collect_slides(), after registration (the only change to this file):
if conf.get("enable_full_pipeline", False):
    from .stage_runner import BabelSharkStageRunner
    stage_runner = BabelSharkStageRunner(conf, logger)
    stage_runner.run_enrichment_pipeline(dest, registration["record_id"])
```

The `enable_full_pipeline` flag gates the new behavior — existing deployments are unaffected
until explicitly opted in.

**Trigger dispatch timing change:** Currently `mark_intake_complete()` fires inside
`register_collected_file()` immediately after intake, dispatching to QC before any enrichment.
In full-pipeline mode this trigger must be deferred to the end of `run_enrichment_pipeline()`
so QC receives a renamed, validated file. This is controlled by setting
`BABELSHARK_DEFER_TRIGGER=true` — `BabelSharkDBWriter.mark_intake_complete()` already
accepts all required parameters; only the call site changes.

---

### Phase 4 — Stage-level feature flags

Enable stages independently via config to allow gradual validation in staging:

```yaml
enable_full_pipeline: true
pipeline_stages:
  label_extraction: true
  datamatrix: true
  stain_extraction: true
  roi_fallback: true
  slide_id_generation: true
  pasnet_validation: false   # enable only when PASNET credentials are configured
```

---

### Phase 5 — Decouple heavy stages into async workers (future)

Label extraction, DataMatrix reading, and stain extraction are CPU/IO intensive. Once Phase 3
is validated in production, each stage can be moved to a separate service that consumes
`ServiceTrigger` events from the database — the same pattern already used between BabelShark
and QC. This is a structural improvement, not a prerequisite for restoring functionality.

---

## Summary Table

| Stage | Phase 1 | Phase 2 | Phase 3 | Breaks Existing? |
|---|---|---|---|---|
| Label extraction | — | Wrap `LabelExtractor` | Flag-gated | No |
| DataMatrix reading | — | Wrap `run_with_config()` | Flag-gated | No |
| Stain extraction | Fix subprocess → direct call | Wrap `run_pipeline()` | Flag-gated | No |
| ROI extraction | Fix import | Wrap CLI entrypoint | Via stain wrapper | No |
| Slide ID generation | Fix DB imports | Wrap `run_pipeline()` | Flag-gated, defer trigger | No |
| PASNet validation | Fix import | Wrap `main()` | Flag-gated | No |
| Rename + routing | Part of slide_id_generator | — | Same | No |
| DB/event system | No change | No change | Additive only | **No** |
| QC trigger dispatch | No change | Moved to pipeline end | Conditional on flag | No |

The existing DB schema, event system, runner loop, and trigger dispatch are **not modified**
in any phase. All changes are additive or isolated behind feature flags.
