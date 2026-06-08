# DICOM / Uploader Phase 0 Audit Report

> Generated: 2026-05-29  
> Scope: Read-only inspection. No code changes were made.

---

## 1. Current Enterprise DICOM / Uploader Structure

### File tree

```
pathoryx_enterprise/services/dicom/
  __init__.py
  config.py          DICOMSettings (Pydantic BaseSettings, env-vars only)
  db_writer.py       DICOMDBWriter — persist result, update FileRecord, dispatch to uploader
  main.py            Entry-point: loads settings, calls run()
  runner.py          Main loop — trigger dequeue, conversion, upload, heartbeat
  upload_utils.py    Native storescu helpers (build_cstore_commands, run_all_cstore_batches)

pathoryx_enterprise/services/uploader/
  __init__.py
  circuit_breaker.py CircuitBreaker (CLOSED/OPEN/HALF_OPEN, thread-safe)
  config.py          UploaderSettings (Pydantic BaseSettings, env-vars only)
  db_writer.py       UploaderDBWriter — persist result, update FileRecord, event
  main.py            Entry-point
  runner.py          Main loop — trigger dequeue, verify DICOM path, record outcome

pathoryx_enterprise/db/models/
  dicomizer.py       ConversionResult ORM model  (schema: dicomizer)
  uploader.py        UploadResult ORM model       (schema: uploader)
  core.py            FileRecord, ServiceTrigger, PipelineRun, StepRun, RunnerRegistration
  events.py          PipelineEvent (append-only event store)
```

### Service entry points

| Service | Entry-point | target_service in trigger |
|---|---|---|
| `pathoryx-dicom` | `pathoryx_enterprise.services.dicom.main:main` | `dicom_service` |
| `pathoryx-uploader` | `pathoryx_enterprise.services.uploader.main:main` | `upload_service` |

---

## 2. Current Runtime Imports and External Dependencies

### pathoryx_enterprise/services/dicom/runner.py

```python
# ── External dependencies (NOT yet native) ──────────────────────────────────

# Dependency 1 — old WSIDicomizer helper (validated at startup, loaded lazily)
importlib.import_module("utils.wsidicom_utils")           # tool_WSIDicomizer/utils/
# Required in PYTHONPATH: /home/shahram/tool_WSIDicomizer

# Dependency 2 — old dicom_delivery_adapter pipeline (loaded via _load_dicom_deps)
from pipeline.config import load_config
from pipeline.services.conversion_service import ConversionService
from pipeline.services.upload_service import UploadService  # imported but discarded (assigned to _)
# Required in PYTHONPATH: /home/shahram/Palantir/services/dicom_delivery_adapter
```

**Key observations:**

- `_validate_wsidicom_utils()` is called at startup. It calls
  `importlib.import_module("utils.wsidicom_utils")`. If `tool_WSIDicomizer` is not on
  `sys.path`, the service refuses to start.
- `_load_dicom_deps()` imports `load_config` and `ConversionService` from the old
  adapter. The `UploadService` import is present but the return value is discarded
  (`dicom_config, ConversionServiceCls, _ = _load_dicom_deps(...)`). **The old
  UploadService is never called at runtime.**
- The storescu upload (`build_cstore_commands`, `run_all_cstore_batches`) is already
  fully **native** in `upload_utils.py`.

### pathoryx_enterprise/services/uploader/runner.py

```python
# No external pipeline.* or utils.* imports.
# 100% native already.
```

The uploader service is **already standalone**. It verifies the DICOM output path exists,
records the result, and updates `FileRecord.status → uploaded`. No PYTHONPATH dependency.

---

## 3. Current DB Tables / Models

### dicomizer.conversion_results

| Column | Type | Notes |
|---|---|---|
| `internal_id` | BIGINT PK | |
| `idempotency_key` | TEXT UNIQUE | deterministic — prevents double inserts |
| `file_record_internal_id` | BIGINT FK → core.file_records | SET NULL on delete |
| `pipeline_run_internal_id` | BIGINT FK → core.pipeline_runs | SET NULL on delete |
| `trigger_internal_id` | BIGINT | not FK (migration 0002 addition) |
| `global_run_id` | TEXT | |
| `global_artifact_id` | TEXT | indexed |
| `correlation_id` | TEXT | |
| `source_path` | TEXT | input WSI path |
| `output_path` | TEXT | output DICOM path/folder |
| `output_format` | TEXT | "dicom", "dcm_directory", etc. |
| `conversion_status` | TEXT | indexed; "completed", "skipped_already_dicom", "failed" |
| `was_already_dicom` | BOOL | |
| `conversion_required` | BOOL | |
| `conversion_tool` | TEXT | tool name string |
| `conversion_tool_version` | TEXT | |
| `input_file_size_bytes` | BIGINT | |
| `output_file_size_bytes` | BIGINT | |
| `input_checksum_sha256` | TEXT | |
| `output_checksum_sha256` | TEXT | |
| `duration_seconds` | FLOAT | |
| `failure_context` | JSONB | |
| `metadata_summary` | JSONB | |
| `upload_result_json` | JSONB | storescu batch results |
| `runner_id` | TEXT | migration 0002 |
| `host_id` | TEXT | migration 0002 |
| `service_version` | TEXT | migration 0002 |
| `processed_at` | TIMESTAMPTZ | migration 0002 |

**ORM model vs live DB**: fully in sync. All columns present and matched.

### uploader.upload_results

| Column | Type | Notes |
|---|---|---|
| `internal_id` | BIGINT PK | |
| `idempotency_key` | TEXT UNIQUE | |
| `file_record_internal_id` | BIGINT FK → core.file_records | |
| `pipeline_run_internal_id` | BIGINT FK → core.pipeline_runs | |
| `trigger_internal_id` | BIGINT | migration 0002 |
| `global_run_id` | TEXT | |
| `global_artifact_id` | TEXT | indexed |
| `correlation_id` | TEXT | |
| `source_path` | TEXT | |
| `target_system` | TEXT | |
| `target_endpoint` | TEXT | DICOM path passed from dicom_service |
| `upload_status` | TEXT | indexed; "uploaded", "failed" |
| `upload_method` | TEXT | "storescu" (migration 0002) |
| `final_outcome` | TEXT | |
| `file_size` | BIGINT | migration 0002 |
| `duration_seconds` | FLOAT | |
| `retry_count` | INT | |
| `response_summary` | JSONB | |
| `failure_context` | JSONB | |
| `runner_id` | TEXT | migration 0002 |
| `host_id` | TEXT | migration 0002 |
| `service_version` | TEXT | migration 0002 |
| `processed_at` | TIMESTAMPTZ | migration 0002 |

**ORM model vs live DB**: fully in sync.

### FileRecord status machine (relevant states)

```
qc_passed → dicom_pending → dicom_running → dicom_done | dicom_failed
dicom_done → upload_pending → upload_running → uploaded | upload_failed
```

The CHECK constraint on `core.file_records.status` already includes all these values.

---

## 4. Current service_trigger Usage

### Trigger flow

```
[QC service] → enqueues → core.service_trigger (target='dicom_service', stage='dicom')
  ↓
[DICOM runner] dequeues → converts → uploads via storescu
  ↓ on success
[DICOMDBWriter] → enqueues → core.service_trigger (target='upload_service', stage='upload')
  ↓
[Uploader runner] dequeues → verifies dicom_path → records outcome
```

### Trigger payload shapes

**QC → DICOM trigger** (`trigger_payload_json`):
```json
{ "source_path": "/path/to/slide.svs", "scanner_id": "..." }
```
Source: set by QC service after a slide passes QC. The `source_path` is the original WSI.

**DICOM → Upload trigger** (`trigger_payload_json`):
```json
{ "dicom_path": "/path/to/output/dicom/folder" }
```
Source: `DICOMDBWriter.record_conversion_success()` — set to the DICOM output path.

### Dequeue mechanism

Both services use `TriggerRepository.dequeue_next(target_service=SERVICE_NAME)` which
issues `SELECT … FOR UPDATE SKIP LOCKED`. Safe under concurrent workers.

### Idempotency

Both `ConversionResult` and `UploadResult` have a deterministic `idempotency_key`
computed from `(table_name, trigger_id, output_path/status)`. Duplicate inserts from
retries are silently skipped.

---

## 5. Old Adapter Behaviors That Must Be Preserved

### From dicom_delivery_adapter / ConversionService

| Behavior | Location | Must preserve |
|---|---|---|
| Input classification (DICOM vs non-DICOM, file vs folder) | `conversion_utils.classify_input_as_dicom_or_not` | Yes — determines was_already_dicom |
| Skip-if-already-DICOM fast path | `ConversionService.convert()` | Yes |
| Missing file fast path → `ConversionResult(status=failed)` | `ConversionService.convert()` | Yes |
| SHA-256 checksum of input before conversion | `conversion_utils.compute_sha256` | Yes — stored in `input_checksum_sha256` |
| Deterministic output folder layout | `conversion_utils.deterministic_output_folder` | Yes — `output_root / slide_id / stem` |
| WSI → IDS7-compatible DICOM conversion | `ConversionService._convert_non_dicom` → `utils.wsidicom_utils.store_as_IDS7_compatible_dcm` | **Critical** — core product function |
| Output folder size calculation | `ConversionService._safe_size` | Yes |
| `conversion_tool` / `conversion_tool_version` metadata | `ConversionService._convert_non_dicom` return value | Yes — stored in DB |
| Failure does NOT raise — returns `ConversionResult(status=failed)` | `ConversionService.convert()` try/except | Yes — runner inspects status, not exception |

### From old UploadService

The old `UploadService` is **NOT used at runtime**. The enterprise runner already calls
`upload_utils.build_cstore_commands` + `run_all_cstore_batches` directly. Nothing to
preserve from the old `UploadService`.

---

## 6. Old WSIDicomizer Helper Behaviors That Must Be Preserved

### wsidicom_utils.py (tool_WSIDicomizer/utils/)

The enterprise DICOM service references `utils.wsidicom_utils.store_as_IDS7_compatible_dcm`
at runtime. The file exports two closely related functions:

| Function | Input | Purpose |
|---|---|---|
| `store_dcmdile_as_IDS7_compatible_dcm` | single .dcm or image file | Patch IDS7 DICOM headers via pydicom |
| `store_dcmwsifolder_as_IDS7_compatible_dcm` | existing DICOM WSI folder | Patch IDS7 headers via dcmtk (dcmdump/dcmodify); optionally enrich with LIS patient data |
| `create_dcm_metadata_object` | metadata fields | Build wsidicomizer WsiDicomizerMetadata object |
| `convert_img_to_dcm_object` | image path | Create DICOM object from a flat image (JPG/PNG) |

**Critical gap**: `ConversionService._convert_non_dicom` calls
`getattr(legacy_module, "store_as_IDS7_compatible_dcm", None)` — but **this exact
function name does not exist** in `wsidicom_utils.py`. The file exports
`store_dcmdile_as_IDS7_compatible_dcm` and `store_dcmwsifolder_as_IDS7_compatible_dcm`
(different names). This means `store_fn` is **always `None`**, causing the `ids7_compatible_dcm`
code path to silently fall through to `placeholder_copy` or raise. This is a latent
production bug that must be resolved during migration.

### IDS7 DICOM tag requirements (from wsidicom_utils.py)

These DICOM tags are injected to make slides importable into Sectra IDS7:

| Tag | Attribute | Source |
|---|---|---|
| `(2200,0002)` UT LabelText | slide_id | filename pattern extraction |
| `(0040,0512)` LO ContainerIdentifier | slide_id | filename pattern extraction |
| `(0040,0560)[0].(0040,0600)` SpecimenShortDescription | "Staining: {stain}" | filename |
| `(0008,0050)` SH AccessionNumber | accession_number | filename |
| `(0020,0010)` SH StudyID | study_id | filename |
| `(0010,0010)` PatientName | from LIS | optional, LIS query |
| `(0010,0020)` PatientID | from LIS (GUID) | optional, LIS query |

### metaextraction_utils.py

Provides filename-to-metadata regex extraction used in wsidicom_utils. Must be preserved
as a native module. Pure Python, no external service dependency except config-driven
regex patterns.

### LIS_utils.py

Provides `get_metadata_from_LIS(case_id_list, metadata_to_collect, cursor)`. Queries the
Nexus SQL Server LIS via `pyodbc`. **Optional** enrichment — `store_dcmwsifolder_as_IDS7_compatible_dcm`
only calls it when `enrich_header_with_patient_infos=True` and a `cursor` is passed.

**Preservation rule**: The enterprise engine must support LIS enrichment as an optional
feature controlled by config. If LIS credentials/cursor are not configured, the conversion
must succeed without LIS data.

### wsidicom_utils.py: Platform issue

Line 36: `bin_path_dcmtk = f"C:\\Program Files\\dcmtk-3.7.0-win64-dynamic\\bin"` is a
hardcoded Windows path. The `store_dcmwsifolder_as_IDS7_compatible_dcm` function uses it
to call `dcmdump` and `dcmodify`. On Linux this must be configurable (e.g., `DCMTK_BIN_PATH`
env var or config field) or use system-installed dcmtk.

---

## 7. Gaps Between Old Behavior and Enterprise Implementation

| # | Gap | Severity | Location |
|---|---|---|---|
| G1 | `_load_dicom_deps()` still imports `pipeline.*` at startup | **Critical** | `dicom/runner.py:73-86` |
| G2 | `_validate_wsidicom_utils()` requires `tool_WSIDicomizer` on PYTHONPATH | **Critical** | `dicom/runner.py:55-70` |
| G3 | `ConversionService` references `store_as_IDS7_compatible_dcm` which **does not exist** in wsidicom_utils.py — function is always `None` | **Critical** (latent production bug) | `dicom_delivery_adapter/pipeline/services/conversion_service.py:104-105` |
| G4 | No `dicom_config.yaml` exists in `configs/` — the old adapter config format is undocumented for enterprise deploy | High | `configs/` directory |
| G5 | `wsidicom_utils.py` has hardcoded Windows dcmtk path — fails on Linux deployment | High | `tool_WSIDicomizer/utils/wsidicom_utils.py:36` |
| G6 | `ConversionService._convert_non_dicom` generates a **new** `uuid` as `global_artifact_id` — discards the lineage ID from the trigger | High | `dicom_delivery_adapter/…/conversion_service.py:83` |
| G7 | `metaextraction_utils.py` and `LIS_utils.py` have no enterprise equivalents | High | missing `dicom/engine/` |
| G8 | `ConversionService` uses `pipeline.adapters.lis_adapter.LisAdapter` — not available natively | Medium | `dicom_delivery_adapter/…/conversion_service.py:10,18` |
| G9 | No native `engine/` directory under `services/dicom/` — all conversion logic still external | High | `pathoryx_enterprise/services/dicom/` |
| G10 | `DICOMDBWriter.record_conversion_success()` does not write `source_path` to `ConversionResult.source_path` column | Low | `dicom/db_writer.py:88-110` |
| G11 | Uploader `_do_upload()` has no retry logic — delegates entirely to circuit breaker pattern; old `UploadService` had per-request retry with backoff | Low | `uploader/runner.py:49-86` |
| G12 | `dicomizer.conversion_results` missing `step_run_internal_id` column (present in qc model, not added for dicomizer) | Low | migration 0001/0002 |

---

## 8. Recommended Phased Implementation Plan

### Phase 11A — Native conversion engine (no behavior change)

**Goal**: Copy and rewrite the conversion logic into enterprise engine; remove `pipeline.*` and `utils.*` imports from runner.py.

1. Create `pathoryx_enterprise/services/dicom/engine/`:
   ```
   engine/__init__.py
   engine/config.py              (AppConfig dataclass for DICOM — port of old pipeline/config.py)
   engine/domain/__init__.py
   engine/domain/enums.py        (ConversionStatus, InputKind, UploadStatus — port)
   engine/domain/results.py      (ConversionResult, InputClassificationResult — port)
   engine/services/__init__.py
   engine/services/conversion_utils.py  (classify_input_as_dicom_or_not, compute_sha256, deterministic_output_folder — port, stdlib only)
   engine/services/conversion_service.py (ConversionService — port with corrected function name)
   engine/services/metaextraction_utils.py (match_reconstruct_metadict_from_string — port, stdlib only)
   engine/services/wsidicom_utils.py (store_as_IDS7_compatible_dcm — fixed name, configurable dcmtk path)
   engine/services/lis_client.py (get_metadata_from_LIS — optional, LIS_enabled guard)
   ```

2. Fix G3 immediately: the correct function to call is `store_dcmdile_as_IDS7_compatible_dcm`
   for single files and `store_dcmwsifolder_as_IDS7_compatible_dcm` for DICOM WSI folders.
   Rename the dispatch in `conversion_service.py` accordingly.

3. Fix G5: replace hardcoded `bin_path_dcmtk` with `os.environ.get("DCMTK_BIN_PATH", "/usr/bin")`.

4. Fix G6: pass `global_artifact_id` from the trigger through to `ConversionResult` instead of
   generating a new UUID.

5. Rewrite `dicom/runner.py`:
   - Remove `_validate_wsidicom_utils()`.
   - Remove `_load_dicom_deps()`.
   - Import directly from `pathoryx_enterprise.services.dicom.engine.*`.

6. Create `configs/dicom_config.yaml` (maps to engine AppConfig).

7. Validate: `python -c "from pathoryx_enterprise.services.dicom.runner import run; print('OK')"`
   with `PYTHONPATH` unset.

### Phase 11B — Native metaextraction + LIS optional

**Goal**: Make LIS enrichment work optionally through enterprise config.

1. Add `DICOMSettings` fields: `lis_enabled`, `lis_sql_server`, `lis_sql_username`, `lis_sql_password`.
2. `ConversionService` reads from settings — passes `cursor=None` if LIS disabled.
3. Validate: conversion completes without LIS; enriches headers when LIS is enabled.

### Phase 11C — Uploader separation (architecture cleanup)

**Goal**: Make the upload (storescu) a separate stage from conversion.

Currently the DICOM runner does conversion **and** upload in one transaction. The uploader
runner then only verifies the path exists. Cleaner architecture:

- DICOM service: convert WSI → DICOM folder only.
- Upload service: perform storescu C-STORE from the DICOM folder.

This eliminates the double-write risk and makes each stage retryable independently.

**Requires**: Move `build_cstore_commands` / `run_all_cstore_batches` usage from
`dicom/runner.py` into `uploader/runner.py`. The uploader needs Sectra host/port/AE
settings (currently only in `DICOMSettings`). Add `UploaderSettings` fields for Sectra.

### Phase 11D — Validation and end-to-end smoke test

1. Run `grep -Rn "from pipeline\|import pipeline\|qc_adapter\|wsidicom_utils\|dicom_delivery_adapter\|tool_WSIDicomizer" pathoryx_enterprise/services/dicom pathoryx_enterprise/services/uploader`
2. Create one controlled test trigger pointing to a real SVS.
3. Verify `dicomizer.conversion_results` row written, `uploader.upload_results` row written.
4. Verify `core.file_records.status = 'uploaded'`.

---

## 9. DB Migration Needs

### Needed migrations

| Migration | Change | Priority |
|---|---|---|
| `0006_dicom_conversion_results_source_path_fix` | Already exists — the `source_path` column is in the DB but not populated by the current db_writer. The db_writer should write `trigger.trigger_payload_json.get("source_path")` to `ConversionResult.source_path`. Code fix, no schema change needed. | Low |
| `0006_dicom_conversion_results_step_run` | Add `step_run_internal_id` BIGINT nullable to `dicomizer.conversion_results` (parity with qc model) | Low |
| No new tables needed | dicomizer and uploader schemas are fully provisioned in migration 0001 + 0002 | — |

### No new schemas required

All required schemas (`dicomizer`, `uploader`) are already created in migration 0001.
All required columns were added in migrations 0001 + 0002.

---

## 10. Risks and Rollback Plan

### Risk R1 — Broken conversion function name (G3, Critical)

**Risk**: `store_as_IDS7_compatible_dcm` never existed. Current production deployments
that rely on `ids7_compatible_dcm` mode silently fall back to `placeholder_copy`, producing
non-compliant DICOM files that may be rejected by Sectra IDS7.

**Rollback**: Not applicable — the bug exists in the old adapter too. The migration fixes it.
**Mitigation**: In the engine `conversion_service.py`, dispatch to the correct
`store_dcmdile_as_IDS7_compatible_dcm` or `store_dcmwsifolder_as_IDS7_compatible_dcm`
based on input type.

### Risk R2 — PYTHONPATH removal breaks existing deployments

**Risk**: Removing PYTHONPATH dependency means any deployment script that relies on it
will need updating.

**Rollback**: The `_load_dicom_deps()` + `_validate_wsidicom_utils()` functions remain
in place until Phase 11A is complete and validated. Migration is atomic: swap both
functions in one commit.
**Mitigation**: Feature-flag via env var `USE_NATIVE_DICOM_ENGINE=true` during transition.

### Risk R3 — LIS query rate limit

**Risk**: The LIS_utils.py header comment explicitly warns: "Try to keep the number of
database requests low!" The Nexus LIS system is a high-priority production system.

**Mitigation**: LIS lookup must be opt-in (`LIS_ENABLED=true`), batched per conversion run,
and results cached per session. Never call for every file individually in a tight loop.

### Risk R4 — dcmtk dependency on Linux

**Risk**: `wsidicom_utils.py` calls `dcmdump` and `dcmodify` from a hardcoded Windows path.
On the Linux server this will fail with `FileNotFoundError`.

**Rollback**: If dcmtk is not available, fall back to pure-pydicom header patching
(`store_dcmdile_as_IDS7_compatible_dcm` logic).
**Mitigation**: Add `DCMTK_BIN_PATH` env var with default `/usr/bin`. Install dcmtk via
apt on the server (`apt-get install dcmtk`).

### Risk R5 — Upload separation (Phase 11C) changes trigger dispatch

**Risk**: Moving storescu from the DICOM runner to the uploader runner changes when the
upload actually happens. If a DICOM conversion completes but the uploader trigger is never
dequeued (service down), the PACS does not receive the file.

**Mitigation**: The existing circuit breaker in the uploader handles PACS unavailability.
Implement Phase 11C only after Phase 11A is stable. Keep combined mode as a config option.

### Risk R6 — global_artifact_id overwritten (G6)

**Risk**: Old ConversionService generates `uuid.uuid4()` as `global_artifact_id`, breaking
artifact lineage from QC → DICOM → Upload.

**Rollback**: Column already exists; old rows will have broken lineage. Fix is forward-only.
**Mitigation**: In the engine `ConversionService.convert()`, accept `global_artifact_id`
as a constructor argument (from trigger) and pass it through to `ConversionResult`.

---

## Summary Table

| Area | Status | Blocker for runtime independence |
|---|---|---|
| Uploader service | ✅ Already native | None |
| DICOM upload utils (storescu) | ✅ Already native | None |
| DICOM DB writer | ✅ Native | None |
| DICOM runner loop / heartbeat / health | ✅ Native | None |
| DICOM conversion engine | ❌ Still imports `pipeline.*` | Phase 11A |
| wsidicom_utils dependency | ❌ Requires PYTHONPATH to tool_WSIDicomizer | Phase 11A |
| metaextraction_utils | ❌ Not ported | Phase 11A |
| LIS client | ❌ Not ported (optional) | Phase 11B |
| DICOM config YAML | ❌ Missing from configs/ | Phase 11A |
| Function name bug (G3) | ❌ Latent production bug | Phase 11A (fix during port) |
| dcmtk Linux path | ❌ Hardcoded Windows path | Phase 11A (fix during port) |
| DB schema | ✅ Fully provisioned | None |
| Artifact lineage (global_artifact_id) | ⚠️ Broken in old adapter | Phase 11A (fix during port) |
