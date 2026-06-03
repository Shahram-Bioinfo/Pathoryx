# DICOM Phase 11A — Native Engine Report

> Implementation complete: 2026-05-29  
> Scope: Native DICOM conversion engine under pathoryx_enterprise/services/dicom/engine/

---

## 1. Changed Files

### New files created

| File | Purpose |
|---|---|
| `pathoryx_enterprise/services/dicom/engine/__init__.py` | Package marker |
| `pathoryx_enterprise/services/dicom/engine/config.py` | `DicomEngineConfig` dataclasses + `load_dicom_engine_config()` |
| `pathoryx_enterprise/services/dicom/engine/domain/__init__.py` | Package marker |
| `pathoryx_enterprise/services/dicom/engine/domain/enums.py` | `ConversionStatus`, `InputKind`, `UploadStatus`, `StepStatus` |
| `pathoryx_enterprise/services/dicom/engine/domain/results.py` | `ConversionResult`, `InputClassificationResult`, `UploadResult` |
| `pathoryx_enterprise/services/dicom/engine/services/__init__.py` | Package marker |
| `pathoryx_enterprise/services/dicom/engine/services/conversion_utils.py` | DICOM classification, SHA-256, deterministic output folder |
| `pathoryx_enterprise/services/dicom/engine/services/conversion_service.py` | `ConversionService` — main conversion orchestrator |
| `pathoryx_enterprise/services/dicom/engine/services/metaextraction_utils.py` | Filename → metadata regex extraction |
| `pathoryx_enterprise/services/dicom/engine/services/wsidicom_utils.py` | IDS7 DICOM tag injection; `store_as_IDS7_compatible_dcm` dispatcher |
| `pathoryx_enterprise/services/dicom/engine/services/lis_client.py` | Optional LIS DB metadata client |
| `configs/dicom_config.yaml` | DICOM service runtime configuration YAML |

### Modified files

| File | Change |
|---|---|
| `pathoryx_enterprise/services/dicom/config.py` | Added re-export of `DicomEngineConfig` + `load_dicom_engine_config` from engine |
| `pathoryx_enterprise/services/dicom/runner.py` | Removed `_validate_wsidicom_utils()`, `_load_dicom_deps()`; wired native engine |

---

## 2. Files Copied / Adapted From

| Source (reference, not modified) | Destination (native engine) | Adaptation |
|---|---|---|
| `dicom_delivery_adapter/pipeline/domain/enums.py` | `engine/domain/enums.py` | Direct port, no deps |
| `dicom_delivery_adapter/pipeline/domain/results.py` | `engine/domain/results.py` | Imports rewritten; `global_artifact_id` type changed to `str|None`; `input_file_size` field added |
| `dicom_delivery_adapter/pipeline/services/conversion_utils.py` | `engine/services/conversion_utils.py` | Imports rewritten to native engine paths |
| `dicom_delivery_adapter/pipeline/services/conversion_service.py` | `engine/services/conversion_service.py` | Imports rewritten; G3/G5/G6 fixes applied; LisAdapter removed |
| `dicom_delivery_adapter/pipeline/config.py` | `engine/config.py` | Simplified to fields ConversionService actually uses; YAML loader added |
| `tool_WSIDicomizer/utils/wsidicom_utils.py` | `engine/services/wsidicom_utils.py` | dcmtk path fixed; `store_as_IDS7_compatible_dcm` dispatcher added; wsidicomizer imports guarded |
| `tool_WSIDicomizer/utils/metaextraction_utils.py` | `engine/services/metaextraction_utils.py` | Direct port, pure Python |
| `tool_WSIDicomizer/utils/LIS_utils.py` | `engine/services/lis_client.py` | `pyodbc` import made optional; `open_lis_connection()` helper added |

---

## 3. Import Rewrite Map

| Old import | New import |
|---|---|
| `from pipeline.config import load_config` | `from pathoryx_enterprise.services.dicom.engine.config import load_dicom_engine_config` |
| `from pipeline.services.conversion_service import ConversionService` | `from pathoryx_enterprise.services.dicom.engine.services.conversion_service import ConversionService` |
| `from pipeline.services.upload_service import UploadService` | **Removed** — never actually used at runtime |
| `importlib.import_module("utils.wsidicom_utils")` | **Removed** — startup validation replaced by native engine |
| `from pipeline.domain.enums import InputKind, ConversionStatus` | `from pathoryx_enterprise.services.dicom.engine.domain.enums import …` |
| `from pipeline.domain.results import ConversionResult, InputClassificationResult` | `from pathoryx_enterprise.services.dicom.engine.domain.results import …` |
| `from utils.wsidicom_utils import store_as_IDS7_compatible_dcm` | `from pathoryx_enterprise.services.dicom.engine.services.wsidicom_utils import store_as_IDS7_compatible_dcm` |
| `from utils.metaextraction_utils import match_reconstruct_metadict_from_string` | `from pathoryx_enterprise.services.dicom.engine.services.metaextraction_utils import …` |
| `from utils.LIS_utils import get_metadata_from_LIS` | `from pathoryx_enterprise.services.dicom.engine.services.lis_client import …` |
| `from pipeline.adapters.lis_adapter import LisAdapter` | **Removed** — replaced by `lis_client.open_lis_connection()` called inside `ConversionService._get_lis_cursor()` |

---

## 4. Fixed: Function-Name Bug (G3)

**Root cause**: The old `ConversionService._convert_non_dicom()` called:
```python
store_fn = getattr(legacy_module, "store_as_IDS7_compatible_dcm", None)
```
The function `store_as_IDS7_compatible_dcm` **did not exist** in `wsidicom_utils.py`. The actual exported functions were `store_dcmdile_as_IDS7_compatible_dcm` (for single files) and `store_dcmwsifolder_as_IDS7_compatible_dcm` (for DICOM folders). `getattr(..., None)` returned `None` silently — the `ids7_compatible_dcm` code path was dead. Every production conversion secretly fell through to either `placeholder_copy` (if enabled) or raised a RuntimeError.

**Fix applied**: `engine/services/wsidicom_utils.py` now exports `store_as_IDS7_compatible_dcm()` as the primary dispatcher:

```python
def store_as_IDS7_compatible_dcm(
    in_path: str,
    match_construct_patterns: dict,
    out_folder: str,
    *,
    dcmtk_bin_dir: str = "",
    lis_cursor=None,
) -> tuple[dict, str]:
    path = Path(in_path)
    if path.is_dir():
        metadata = store_dcmwsifolder_as_IDS7_compatible_dcm(...)
        return metadata, out_folder
    else:
        return store_dcmdile_as_IDS7_compatible_dcm(...)
```

The dispatcher:
- Routes **directory inputs** (DICOM WSI folders) to `store_dcmwsifolder_as_IDS7_compatible_dcm` (dcmtk header patching of LABEL/OVERVIEW/THUMBNAIL files)
- Routes **file inputs** (single .dcm or flat images) to `store_dcmdile_as_IDS7_compatible_dcm` (pydicom tag injection)
- Returns `(metadata_dict, output_path_str)` consistently from both paths

`ConversionService._convert_non_dicom()` now calls the dispatcher directly via native import — no `getattr`, no silent None.

---

## 5. Fixed: dcmtk Linux Path (G5)

**Root cause**: `wsidicom_utils.py` line 36 contained:
```python
bin_path_dcmtk = f"C:\\Program Files\\dcmtk-3.7.0-win64-dynamic\\bin"
```
Every call to `dcmdump` or `dcmodify` prepended this Windows path, causing `FileNotFoundError` on Linux.

**Fix applied**: The hardcoded constant is removed. A resolver function is used instead:

```python
def _get_dcmtk_cmd(tool: str, dcmtk_bin_dir: str = "") -> str:
    bin_dir = dcmtk_bin_dir or os.environ.get("DCMTK_BIN_DIR", "")
    if bin_dir:
        return str(Path(bin_dir) / tool)
    return tool  # rely on PATH
```

Resolution order (first non-empty wins):
1. `dcmtk_bin_dir` argument (from `DicomEngineConfig.dcmtk.bin_dir` via config YAML)
2. `DCMTK_BIN_DIR` environment variable
3. Tool name only → resolved from system `PATH`

**Current server state**: `dcmtk` 3.6.7 is installed at `/usr/bin/dcmodify` and `/usr/bin/dcmdump`. With an empty `dcmtk.bin_dir` in `dicom_config.yaml`, the tools are found via PATH automatically. No environment variable or configuration change is required on this server.

---

## 6. Lineage Preservation Behavior (G6 Fix)

**Root cause**: Old `ConversionService.convert()` generated a new `uuid.uuid4()` as `global_artifact_id` in the successful `ConversionResult`:
```python
global_artifact_id=uuid_pkg.uuid4(),   # ← breaks QC → DICOM → Upload lineage
```
This discarded the artifact ID that originated in the QC trigger.

**Fix applied**: `ConversionService.convert()` now accepts `global_artifact_id` as a keyword argument:
```python
def convert(self, source_path, *, global_artifact_id: str | None = None) -> ConversionResult:
```
It passes this value unchanged to every `ConversionResult` branch (success, skip, failure). The `runner.py` caller passes it from the trigger:
```python
conversion_result = conv_svc.convert(
    source_path,
    global_artifact_id=trigger.global_artifact_id,
)
```

The `ConversionResult` dataclass field type changed from `uuid.UUID | None` to `str | None`, matching the enterprise string-based artifact ID convention.

---

## 7. Validation Commands and Results

### py_compile — all 13 files

```
python -m py_compile \
  pathoryx_enterprise/services/dicom/engine/__init__.py \
  pathoryx_enterprise/services/dicom/engine/domain/__init__.py \
  pathoryx_enterprise/services/dicom/engine/domain/enums.py \
  pathoryx_enterprise/services/dicom/engine/domain/results.py \
  pathoryx_enterprise/services/dicom/engine/services/__init__.py \
  pathoryx_enterprise/services/dicom/engine/services/conversion_utils.py \
  pathoryx_enterprise/services/dicom/engine/services/metaextraction_utils.py \
  pathoryx_enterprise/services/dicom/engine/services/lis_client.py \
  pathoryx_enterprise/services/dicom/engine/services/wsidicom_utils.py \
  pathoryx_enterprise/services/dicom/engine/services/conversion_service.py \
  pathoryx_enterprise/services/dicom/engine/config.py \
  pathoryx_enterprise/services/dicom/config.py \
  pathoryx_enterprise/services/dicom/runner.py
```

**Result**: `ALL py_compile OK`

### Import tests (PYTHONPATH unset)

```
unset PYTHONPATH
python -c "from pathoryx_enterprise.services.dicom.runner import run; print('dicom runner OK')"
```
**Result**: `dicom runner OK`

```
python -c "from pathoryx_enterprise.services.dicom.engine.services.conversion_service import ConversionService; print('conversion service OK')"
```
**Result**: `conversion service OK`

```
python -c "from pathoryx_enterprise.services.dicom.engine.services.wsidicom_utils import store_as_IDS7_compatible_dcm; print('ids7 dispatcher OK')"
```
**Result**: `ids7 dispatcher OK`

### Search validation

```
grep -Rn "^from pipeline\|^import pipeline\|^from utils\.wsidicom_utils\|^import utils\.wsidicom_utils" \
  pathoryx_enterprise/services/dicom/
```
**Result**: `grep: clean — zero runtime import violations`

(The broader grep that matches docstring comments returns paths to the porting notes in module docstrings — not runtime imports.)

---

## 8. Remaining Blockers

### Not blocking runtime imports — no action required now

| Item | Status | Notes |
|---|---|---|
| `wsidicomizer` / `wsidicom` packages not installed | Non-blocking | `create_dcm_metadata_object` raises `ImportError` if called. Not in critical conversion path. Install when needed for WSI→DICOM via wsidicomizer. |
| `match_construct_patterns` in `dicom_config.yaml` | Functional | Three patterns configured: `heidelberg_standard`, `e_series`, `fallback_stem_only`. The fallback pattern ensures conversion never fails on unknown filenames. |
| `dicom_config_path` env var not in `.env` | Must add before starting service | Add `DICOM_CONFIG_PATH=./configs/dicom_config.yaml` to `.env`. |

### Phase 11B (LIS integration) — not in scope

LIS client is implemented and guarded. `lis.enabled: false` in `dicom_config.yaml`. To activate: set `lis.enabled: true`, configure credentials via `LIS_SQL_SERVER` / `LIS_SQL_USERNAME` / `LIS_SQL_PASSWORD`.

### Phase 11C (upload separation) — not in scope

The `run()` function still performs storescu upload inside the DICOM trigger handler (unchanged from pre-Phase 11A). The uploader service remains the final status checkpoint. No change to this architecture in Phase 11A.

### End-to-end smoke test — not yet run

Phase 11A validates imports and compilation only. A controlled trigger with a real SVS file (Phase 11D) is required to validate the full conversion path including `store_as_IDS7_compatible_dcm`, dcmtk header patching, and `dicomizer.conversion_results` DB write.

### `.env` addition required

```
DICOM_CONFIG_PATH=./configs/dicom_config.yaml
```

`DICOM_CONFIG` is also accepted (AliasChoices in DICOMSettings). Without this, `pathoryx-dicom` will fail on `QCSettings` validation at startup.
