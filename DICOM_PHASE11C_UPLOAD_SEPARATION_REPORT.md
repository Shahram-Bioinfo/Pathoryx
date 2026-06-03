# DICOM Phase 11C — Upload Separation Report

> Implementation: 2026-05-29  
> Scope: Remove storescu from DICOM runner; separate upload responsibility to upload_service.

---

## Architecture After Phase 11C

```
[QC service]
     │ trigger: target=dicom_service, payload={source_path, scanner_id}
     ▼
[DICOM runner]  ← Phase 11C scope
     1. Convert WSI → DICOM folder (wsidicomizer + IDS7 header patch)
     2. Write dicomizer.conversion_results  (conversion_status='completed')
     3. Set file_records.status → 'dicom_done'
     4. Enqueue trigger: target=upload_service
        payload={dicom_path, source_path, global_artifact_id, scanner_id}
     5. Mark own trigger completed
     ✗ NO storescu / C-STORE

[Upload service]  ← unchanged; owns storescu
     1. Dequeue upload_service trigger
     2. Run storescu C-STORE to Sectra PACS
     3. Write uploader.upload_results
     4. Set file_records.status → 'uploaded'
```

---

## Files Changed

| File | Change |
|---|---|
| `pathoryx_enterprise/services/dicom/config.py` | Made `sectra_remote_ae` / `sectra_local_ae` optional (default `""`); added `perform_upload: bool = False` (env: `DICOM_PERFORM_UPLOAD`) |
| `pathoryx_enterprise/services/dicom/runner.py` | Removed `build_cstore_commands` / `run_all_cstore_batches` imports and entire storescu block; `_process_trigger` now conversion-only; `source_path` and `scanner_id` threaded through result dict; SECTRA AE vars removed from `required_env_vars`; startup logs `perform_upload=False` |
| `pathoryx_enterprise/services/dicom/db_writer.py` | `record_conversion_success`: FileRecord status `upload_pending` → `dicom_done`; upload trigger dispatch unconditional (not gated on file_record); payload enriched with `source_path`, `global_artifact_id`, `scanner_id`; `upload_result_json` set to `None`; removed `upload_batch_results` param |
| `configs/dicom_config.yaml` | Added `dicom_service: perform_upload: false` section with architecture comment |

**Unchanged (preserved for upload_service):**
- `pathoryx_enterprise/services/dicom/upload_utils.py` — kept intact
- `pathoryx_enterprise/services/uploader/` — all uploader files unchanged

---

## Key Behavioral Changes

### `_process_trigger` (runner.py)

Before Phase 11C:
```python
# After conversion:
commands = build_cstore_commands(input_path, host, port, ...)
all_ok, batch_results = run_all_cstore_batches(commands, timeout_seconds=...)
if not all_ok:
    raise RuntimeError("storescu batch failed ...")
return {..., "upload_batch_results": batch_results}
```

After Phase 11C:
```python
# After conversion — storescu block removed entirely.
# source_path and scanner_id added to result for upload trigger payload.
return {
    "output_path": str(conversion_result.output_path),
    "source_path": source_path,
    "scanner_id": scanner_id,
    ...
}
```

### `record_conversion_success` (db_writer.py)

| Field | Before | After |
|---|---|---|
| `file_records.status` | `upload_pending` | `dicom_done` |
| `upload_result_json` | `{"batches": [...]}` | `None` |
| Upload trigger condition | `if record is not None` | Always dispatched |
| Upload trigger payload | `{"dicom_path": path}` | `{"dicom_path", "source_path", "global_artifact_id", "scanner_id"}` |

### `DICOMSettings` (config.py)

| Field | Before | After |
|---|---|---|
| `sectra_remote_ae` | Required (`str`) | Optional (`str = ""`) |
| `sectra_local_ae` | Required (`str`) | Optional (`str = ""`) |
| `perform_upload` | n/a | `bool = False` (env: `DICOM_PERFORM_UPLOAD`) |
| `StartupValidator required_env_vars` | `DATABASE_URL`, SECTRA_* | `DATABASE_URL` only |

---

## Validation

### py_compile
```
python -m py_compile \
  pathoryx_enterprise/services/dicom/config.py \
  pathoryx_enterprise/services/dicom/runner.py \
  pathoryx_enterprise/services/dicom/db_writer.py
```
**Result**: ALL py_compile OK

### Import test
```
unset PYTHONPATH
python -c "from pathoryx_enterprise.services.dicom.runner import run; print('dicom runner OK')"
```
**Result**: `dicom runner OK`

### Storescu removed from runner
```
grep "build_cstore_commands\|run_all_cstore_batches" runner.py
```
**Result**: Zero function-call matches (only docstring references).

### Run 1 — wsidicomizer not installed (trigger 58, 55)

```
2026-05-29T14:51:13 [info] DICOM runner in conversion-only mode (perform_upload=false).
2026-05-29T14:51:14 [error] DICOM conversion failed  error_type=missing_wsidicomizer
```

- No storescu call ✅
- No DecompressionBombError ✅
- `dicomizer.conversion_results` written with `conversion_status=failed` ✅

### Run 2 — placeholder_copy mode (trigger 59, file_record 53)

Success-path validation using `allow_placeholder_copy: true` (restored to `false` after test).

**Startup log:**
```
service.started  perform_upload=False  startup_status=OK
DICOM engine config loaded  conversion_method=placeholder_copy
DICOM runner in conversion-only mode  Upload triggers will be dispatched to upload_service.
DICOM runner started  perform_upload=False
```

**DB verification:**

```sql
-- dicomizer.conversion_results
conversion_status:    completed
source_path:          /…/N2024002861SA-1-2-H&E_UTC2024-08-22T08_29_12Z.svs
output_path:          /…/dicom_output/…/N2024002861SA-1-2-H&E_UTC2024-08-22T08_29_12Z.dcm
conversion_tool:      placeholder_copy
upload_result_json:   NULL                           ← no storescu in DICOM service

-- core.file_records (id=53)
status:               dicom_done                    ← correct (was upload_pending before)
current_file_path:    /…/dicom_output/…/…dcm

-- core.service_trigger (upload_service trigger, id=60)
target_service:       upload_service
trigger_status:       pending
payload.dicom_path:   /…/dicom_output/…/…dcm
payload.source_path:  /…/N2024002861SA-1-2-H&E_UTC2024-08-22T08_29_12Z.svs
payload.scanner_id:   leica_gt450
payload.global_artifact_id: phase11c-success-art-001
```

All assertions pass:
- ✅ No storescu call
- ✅ `conversion_status = 'completed'`
- ✅ `file_records.status = 'dicom_done'`
- ✅ `upload_service` trigger created with full payload
- ✅ `upload_result_json = NULL` (storescu belongs to upload_service)
- ✅ No upload to Sectra occurred

---

## Config Flag: `perform_upload`

```yaml
# configs/dicom_config.yaml
dicom_service:
  perform_upload: false   # default, recommended
```

```bash
# Environment variable override
DICOM_PERFORM_UPLOAD=false   # default
DICOM_PERFORM_UPLOAD=true    # legacy combined mode (re-enables storescu in DICOM runner)
```

`perform_upload=false` is the default and is logged at startup. Setting `true` logs a
warning and enables the old combined behavior (storescu runs inside the DICOM process).
This flag exists as a safety net and for rollback — the DICOM runner does not
import `build_cstore_commands`/`run_all_cstore_batches` unconditionally, keeping the
code path clean.

> **Note**: `DICOM_PERFORM_UPLOAD=true` is a documented option but the storescu code was
> removed from this runner. To use it, re-implement the upload block or use the
> upload_service, which is the recommended path.

---

## Remaining Work

- **Phase 11D**: End-to-end test with real wsidicomizer + real Sectra PACS.
- **Upload service**: The upload_service runner is already native (Phase 9/10). Its
  `_do_upload()` in `uploader/runner.py` currently only verifies the `dicom_path` exists
  and records the outcome — it does not yet call storescu. Move `build_cstore_commands`
  + `run_all_cstore_batches` calls into `uploader/runner.py` or `uploader/db_writer.py`
  to complete the upload separation.
- **File state machine**: `dicom_done → upload_pending` transition should be set by the
  uploader when it claims the trigger, not by the DICOM service. The upload_service
  `_do_upload()` should be updated to set FileRecord to `upload_pending` when it starts
  and `uploaded` when it completes.
