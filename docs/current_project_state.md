# Palantir Enterprise — Current Project State

> **Update this file** at the end of every significant coding session.
> Keep it current; a stale entry is worse than no entry.
> **Last updated:** 2026-06-10 (Phase 4.5A)

---

## Quick Status

| Area | Status |
|------|--------|
| Database schema | ✅ Complete — 8 migrations applied (0001–0008) |
| BabelShark intake-only | ✅ Production-verified with real WSI files |
| BabelShark full enrichment pipeline | 🟡 Code-complete, not smoke-tested end-to-end |
| QC service | ✅ Stable — ModelRegistry singleton, 4 ML models |
| DICOM native engine (Phase 11A+11C) | 🟡 Import-verified; real SVS conversion not run |
| Uploader storescu | ❌ Not wired — `_do_upload()` does not call storescu |
| RecoverySentry | ✅ Fully implemented, 314 unit tests passing |
| Dashboard backend (7 pages) | ✅ Build clean, API endpoints verified |
| Dashboard frontend build | ✅ 0 TypeScript errors, 1908 modules, 181 kB bundle |
| LCARS UI visual milestone | ✅ Reached (commit 129e31d) |
| LCARS dual-theme architecture | ✅ Phase 4.5A complete — 3-way theme switcher live |
| Windows config variants | ✅ Present (`*.windows.yaml`) |
| Windows end-to-end test | ❌ Not yet run |
| Upload dry-run safety gates | ✅ All gates locked (dry_run=true, c_store=false) |
| Unit tests | ✅ 314/314 passing |

---

## Latest Completed Phases

### Phase 3.8A — Global Rename (2026-06-05, commit 20037f7)
- All user-facing strings, docs, and UI labels changed from "Pathoryx Enterprise" to "Palantir".
- Python package name `pathoryx_enterprise` and DB name `pathoryx` retained for migration safety.

### Phase 3.6 — (2026-06-05, commit 65267ac)
- (Details in git history — refer to commit message for specifics.)

### Phase 3.3 — Professional Label Image Viewer in Recovery Drawer (commit 7f0c9d9)
- `TechnicianReviewDrawer` component completed with label image preview.
- `GET /dashboard/api/recovery/files/{id}/label-image` endpoint verified.

### Phase 3 — RecoverySentry finalization (2026-06-05)
- Retired `services/failed_watcher/` (runner, watcher, requeue_service, config removed).
- `change_detector.py` canonical location moved to `services/recovery_sentry/`.
- `pathoryx-failed-watcher` CLI stub exits with code 1.
- `docker/Dockerfile.recovery_sentry` created.
- 314 unit tests passing (300 pre-existing + 14 new for Phase 3).
- Review state machine finalized and documented.

### Phase 2 — Codebase cleanup (2026-06-05)
- Deleted stale backup files (`.bak`), reports, and R history.
- `configs/dicom_config_windows.yaml` → renamed to `dicom_config_production.yaml`.
- `DATABASE_SETUP.md` DB name corrected to `pathoryx`.
- Dashboard frontend build verified clean.

### Phase 11C — DICOM/Uploader separation (2026-05-29)
- storescu removed from DICOM runner.
- DICOM service now enqueues `upload_service` trigger with full payload.
- `FileRecord.status` after conversion: `dicom_done` (was `upload_pending`).
- `DICOMSettings`: `sectra_remote_ae`/`sectra_local_ae` made optional; `perform_upload: false` added.

### Phase 11A — Native DICOM engine (2026-05-29)
- All `pipeline.*` and `utils.*` external imports removed from DICOM runner.
- Native engine in `services/dicom/engine/` created (13 files).
- Fixed G3 (wrong function name — `store_as_IDS7_compatible_dcm` dispatcher added).
- Fixed G5 (hardcoded Windows dcmtk path → `_get_dcmtk_cmd()` resolver).
- Fixed G6 (global_artifact_id lineage preserved through ConversionResult).

### BabelShark Full Pipeline (2026-05-28)
- Fixed 4 broken import stubs (pasnet_validator, roi_metadata_extractor, slide_id_generator, stain_extractor).
- `stage_runner.py` created with 6-stage enrichment orchestrator.
- Wired into `collect_slides()` behind `enable_full_pipeline` feature flag.
- Full observability: EventStore, StepRun, Prometheus metrics, correlation_id.

---

## Active Features (Working in Production)

- BabelShark intake-only pipeline (watch → copy → register → QC trigger)
- QC ML inference (4 models; accept/reject decision)
- DICOM conversion (import-verified; wsidicomizer not installed yet)
- RecoverySentry auto-recovery and manual review workflow
- Dashboard: all 7 pages, SSE real-time stream, technician rename, label preview
- Prometheus metrics on all services (9091–9097)
- Structured JSON logging via structlog
- OpenTelemetry tracing hooks (no-op if endpoint not set)

---

## Current Blockers

### B1 — Uploader storescu not wired [CRITICAL]
**What:** `uploader/runner.py._do_upload()` does not call `build_cstore_commands` or `run_all_cstore_batches`. Real PACS upload will never execute.
**Needed:**
1. Move Sectra host/port/AE settings from `DICOMSettings` → `UploaderSettings`.
2. In `_do_upload()`: call `upload_utils.build_cstore_commands()` + `run_all_cstore_batches()`.
3. Fix `FileRecord` state: uploader sets `upload_pending` on trigger claim, `uploaded` on success.
**Files:** `services/uploader/runner.py`, `services/uploader/config.py`, `configs/dicom_config.yaml`.

### B2 — wsidicomizer not installed [HIGH]
**What:** DICOM conversion will fail with `missing_wsidicomizer` error.
**Needed:** `pip install wsidicom wsidicomizer` (and verify they are available in conda env on Windows).

### B3 — BabelShark enrichment not smoke-tested [HIGH]
**What:** The 6-stage enrichment pipeline has never been run against a real `.svs` file.
**Needed:** Enable `enable_full_pipeline: true` in a test config; drop a real slide; verify all 6 stages complete and `core.step_runs` populated.

### B4 — Windows end-to-end test not done [HIGH]
**What:** No full pipeline run has been executed on the Windows host.
**Needed:** Complete Windows Migration Checklist phases 6–7.

---

## Current Deployment Status

| Component | Linux Dev | Windows |
|-----------|-----------|---------|
| Python package install | ✅ | Not tested |
| Database migrations | ✅ (0001–0008 applied) | Not tested |
| BabelShark intake-only | ✅ | Not tested |
| QC service | ✅ | Not tested |
| DICOM service | 🟡 (import only) | Not tested |
| Uploader | 🟡 (no storescu) | Not tested |
| RecoverySentry | ✅ | Not tested |
| Dashboard backend | ✅ | Not tested |
| Dashboard frontend build | ✅ | Not tested |

**Upload safety status (as of last review):**

| Gate | Value | Safe |
|------|-------|------|
| `upload.dry_run` | `true` | ✅ |
| `cstore.upload_via_c_store` | `false` | ✅ |
| `dicom_service.perform_upload` | `false` | ✅ |
| `pasnet_validation` | `false` | ✅ |
| `slide_id_generator.dry_run` | `true` | ✅ |

---

## Latest LCARS Work

### Phase 4.5A — Dual-Theme Architecture (2026-06-10)
- `ThemeProvider` extended: `'dark' | 'light' | 'lcars'` (was `'dark' | 'light'`). `setTheme(t)` added to context.
- New CSS: `src/styles/lcars-shell.css` — `.theme-lcars` overrides all shell CSS custom properties to LCARS orange/navy palette. Imported in `main.tsx`.
- When LCARS active: `<html>` gets both `dark` and `theme-lcars` classes. Sidebar inset-shadow creates orange left rail. Nav links get right-pill border-radius.
- `TopBar` theme button replaced with 3-button pill: Sun (clinical light) | Moon (clinical dark) | Cpu (LCARS). Selection persists in `localStorage('pathoryx-theme')`.
- Existing `.lcars-core` and `.lcars-fs` page namespaces (Computer Core pages) are unchanged and self-contained.
- Build: 1919 modules, 0 TypeScript errors, 55 kB CSS bundle.
- Backup: `backups/dashboard-ui/20260610_115652/`

### Previous LCARS milestone
- Visual milestone reached at commit `129e31d` ("stable lcars milestone before account cooldown").
- LCARS CSS namespaces already built in `index.css` under `.lcars-core` and `.lcars-fs`.

---

## Latest API Changes

- Added `POST /dashboard/api/recovery/files/{id}/open-folder` (opens folder in OS file manager).
- Added `GET /dashboard/api/recovery/files/{id}/label-preview` (preview availability check).
- Added `GET /dashboard/api/recovery/files/{id}/audit-trail` (full change + event history).
- `GET /dashboard/api/recovery/files/{id}/label-image` — serves label PNG from BabelShark label dir.
- SSE stream endpoint: `GET /dashboard/api/stream` — 7 change-detection queries, 5 s poll.

---

## Latest DB Changes

- Migration `0008_upload_tracking_schema` — added `upload_tracking` schema.
- Migration `0007_recovery_sentry_columns` — added all TechnicianChange columns including `partial_sha256`, `inode_number`, slide_id, case_id, extension fingerprint columns.

---

## Pending Work (Prioritized)

### P0 — Must complete before production

1. **Wire storescu into Uploader** (B1 above)
2. **Install wsidicomizer** and run first real DICOM conversion (B2)
3. **BabelShark enrichment smoke test** with real `.svs` file (B3)
4. **Windows end-to-end test** — full pipeline on Windows host (B4)

### P1 — Near-term

5. RBAC for Dashboard (auth context already stubbed in `Sidebar.tsx` and `AuthContext.tsx`)
6. Verify model weights accessible on Windows (`models_weights\*.pth`)
7. Update `RUNBOOK.md` — port 8085 reference should be 8087 (RecoverySentry)

### P2 — Medium-term

8. LIS enrichment activation (configure credentials, test with Nexus SQL Server)
9. PASNET validation activation (configure credentials)
10. Prometheus/Grafana dashboard configuration
11. CI pipeline with Docker Compose + integration tests

### P3 — Future

12. Per-stage async worker services (decouple enrichment stages)
13. Multi-site support in Dashboard
14. AI Copilot panel (`CopilotPanel` slot in `Shell.tsx`)
15. DICOM C-FIND verification back from PACS after upload

---

## Next Recommended Tasks

For the next engineering session, the highest-value tasks in order:

1. **Wire storescu in Uploader** — critical path to production. ~2 hours of focused work. Files: `services/uploader/runner.py`, `services/uploader/config.py`.
2. **Install wsidicomizer** and run first real DICOM conversion smoke test.
3. **BabelShark full pipeline smoke test** — drop a real SVS with `enable_full_pipeline: true`.
4. **Windows Migration Checklist Phase 6–7** — smoke test + first pipeline run.
