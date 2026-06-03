# BabelShark Troubleshooting Guide

## Diagnosing Pipeline Failures

### Runtime logs only show intake (watch/copy/register) — no enrichment

**Symptom:** Logs show `[DB] Registered`, `[SUMMARY]` but no `[STAGE]` lines.

**Cause:** `enable_full_pipeline` is not set to `true` in the collector YAML.

**Fix:**
```yaml
enable_full_pipeline: true
pipeline_stages:
  label_extraction: true
  datamatrix: true
  stain_extraction: true
  roi_fallback: true
  slide_id_generation: true
  pasnet_validation: false
```

---

### Stage fails with `ModuleNotFoundError: No module named 'openslide'`

**Cause:** OpenSlide is not installed, or the DLL path is not configured on Windows.

**Fix (Linux):** `apt install openslide-tools python3-openslide` or `pip install openslide-python`

**Fix (Windows):** Add to your YAML:
```yaml
dll_paths:
  openslide_dll: "C:/OpenSlide/bin"
```

---

### Label extraction produces no PNGs

**Symptom:** `[STAGE] label_extraction done: 0 label(s)`

**Causes and fixes:**
1. WSI has no `label` or `macro` associated image → set `macro_tag` to the actual key name
2. File is a DICOM folder but not recognized → verify folder contains `*.dcm` files and no WSI files
3. `label_crop_ratio` is 0 → set to a positive float (e.g., `0.3`)

**Check:** Run `python -m pathoryx_enterprise.services.babelshark.core.label_extractor run --config ...`
standalone against your slide to confirm it works outside the enterprise context.

---

### DataMatrix stage produces all failed rows

**Symptom:** All slides go to `dm_failed/`, DataMatrix Excel has Status=failed everywhere.

**Causes:**
1. Label PNG is the wrong region — label extraction cropped the wrong area
2. DataMatrix barcode is not a standard DataMatrix (QR code, PDF417, etc.) — not supported
3. Barcode is printed in a resolution too low for `pylibdmtx` at any scale

**Check:** Inspect images in `label_crops_dir`. The physical label with the barcode should be visible.

---

### Stain extraction returns `H&E` for every slide

**Causes:**
1. `stain_list_path` is missing or empty
2. OCR is not reading the correct region — adjust `ocr_crop_config`
3. Font/contrast makes OCR fail — the ROI fallback should handle this if configured

**Debug:** Set `log_level: DEBUG` and watch the `[INIT]`, `[ROI]`, `[FINAL]` log lines.

---

### ROI fallback fails with `ValueError: Either ROI_set_file or roiset_selector.roiset_root must be set`

**Cause:** The `temp_config_roi.yaml` or `temp_config.yaml` file that the ROI extractor looks
for is missing or does not contain ROI configuration.

**Fix:** In the collector YAML, add either:
```yaml
ROI_set_file: /path/to/rois.json
```
or:
```yaml
roiset_selector:
  roiset_root: /path/to/roisets/
```

---

### Slide ID generation moves files to `failed/` for all slides

**Symptom:** No files appear in `final_output_dir`, everything lands in `failed/`.

**Causes:**
1. DataMatrix and ROI both failed → no valid SlideID → routine unreadable → `failed/`
2. `staging_dir` in slide_id_generator does not match where the file actually is
3. `final_output_dir` directory does not exist and cannot be created

**Check:** Inspect `slide_metadata.xlsx` — the `status` column shows the routing decision per slide.

---

### QC trigger not dispatched after full enrichment

**Symptom:** Slides appear as `intake_registered` in the DB forever, no QC trigger in `service_trigger`.

**Cause:** `defer_trigger: true` but the `run_enrichment_pipeline()` call failed before dispatching the trigger.

**Fix:** Check for `[PIPELINE] QC trigger dispatch failed` in logs. If the stage_runner crashed
before the trigger dispatch block, the FileRecord is stuck. To recover:

```sql
-- Find stuck records
SELECT internal_id, global_artifact_id, status, current_file_path
FROM core.file_records
WHERE status = 'intake_registered'
  AND created_at < now() - interval '1 hour';

-- Manually dispatch trigger (or re-queue through failed_watcher)
```

Or set `defer_trigger: false` while debugging (trigger fires at intake, QC gets the unstaged file).

---

### PASNet validation fails with connection error

**Symptom:** Stage log shows `[STAGE] pasnet_validation FAILED: ConnectionError: ...`

**Fix:** Either configure PASNET credentials correctly, or set `pasnet_validation: false`
in `pipeline_stages`. PASNet is optional — the pipeline proceeds without it.

---

## Restart Semantics

Because each stage writes results to disk (Excel files, PNGs) and the enterprise DB is
updated atomically, a crashed pipeline run can be investigated by checking:
- `run_output_dir/slide_{id}_{stem}/` for intermediate stage outputs
- `core.step_runs` table for which steps completed before the crash
- `core.pipeline_events` table for the event timeline

Currently there is no automatic re-start of a partially-completed enrichment pipeline.
Recovery is manual: fix the root cause, then delete the stuck FileRecord (if intake-only mode
is acceptable) or re-process the file by moving it back to the watch folder.

Future improvement: the `failed_watcher` service can be extended to detect and re-queue
enrichment pipelines that stalled mid-way.

---

## Observability

### Prometheus metrics

| Metric | Labels | Meaning |
|---|---|---|
| `pathoryx_files_processed_total` | `service=babelshark, stage=<name>` | Stages that completed successfully |
| `pathoryx_files_failed_total` | `service=babelshark, stage=<name>, error_type=<class>` | Stage failures by error type |
| `pathoryx_stage_latency_seconds` | `service=babelshark, stage=<name>` | Per-stage wall-clock histogram |
| `pathoryx_events_appended_total` | `event_type=babelshark.*` | Event store writes |
| `pathoryx_runner_heartbeat_age_seconds` | `runner_id, service=babelshark` | Runner liveness |

### Database queries

```sql
-- Active pipeline runs
SELECT pr.pipeline_name, pr.run_status, pr.started_at, fr.current_file_path
FROM core.pipeline_runs pr
JOIN core.file_records fr ON fr.internal_id = pr.file_record_internal_id
WHERE pr.service_name = 'babelshark'
  AND pr.run_status = 'running'
ORDER BY pr.started_at DESC;

-- Stage completion times for a specific slide
SELECT sr.step_name, sr.step_status, sr.duration_ms, sr.error_message
FROM core.step_runs sr
JOIN core.pipeline_runs pr ON pr.internal_id = sr.pipeline_run_internal_id
JOIN core.file_records fr ON fr.internal_id = pr.file_record_internal_id
WHERE fr.original_filename = 'MY_SLIDE.svs'
ORDER BY sr.started_at ASC;

-- Recent events for a slide
SELECT pe.event_type, pe.occurred_at, pe.event_payload
FROM core.pipeline_events pe
JOIN core.file_records fr ON fr.internal_id = pe.file_record_internal_id
WHERE fr.original_filename = 'MY_SLIDE.svs'
ORDER BY pe.event_version ASC;
```
