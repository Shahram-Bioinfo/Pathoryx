# BabelShark Pipeline Stages — Reference

All stages are implemented in `stage_runner.py::BabelSharkStageRunner`.
Each stage records: events in EventStore, a StepRun row, wall-clock timing,
RSS memory delta, and updates FileRecord.metadata_json.

---

## Stage 0 — Intake (always active)

**File:** `core/collect_slides.py`, `core/database_manager.py`

**What it does:**
- Scans watch folder(s) for new WSI files matching `wsi_types`
- Classifies each file as `new`, `rescan`, or `duplicate`
- Copies or moves the file to `staging_dir` atomically
- Extracts basic metadata via OpenSlide (scanner, magnification, MPP, scan time)
- Creates a `FileRecord` row in the enterprise DB
- In intake-only mode: dispatches `ServiceTrigger` to QC immediately
- In full-pipeline mode: defers the trigger; stage_runner dispatches it at the end

**Config keys:** `watch_dir` / `watch_dirs`, `staging_dir`, `wsi_types`, `operation_mode`

---

## Stage 1 — Label Extraction

**File:** `core/label_extractor.py` — `LabelExtractor.extract_label()`

**What it does:**
- Opens the WSI via OpenSlide
- Extracts the `label` associated image (or falls back to cropping the `macro`)
- Saves a cropped, optionally rotated PNG to `label_crops_dir`
- DICOM WSI folders: uses `_extract_label_from_dicom_folder()` to render one DICOM frame

**Config keys:** `label_crops_dir`, `label_crop_ratio`, `rotation_degrees`, `macro_tag`,
`rotate_associated_label`, `label_rotation_degrees_label`

**Output:** PNG file in `label_crops_dir` named `<slide_stem>.png`

**Dependency:** OpenSlide (pyopenslide), Pillow, pydicom (for DICOM WSI)

---

## Stage 2 — DataMatrix Reading

**File:** `core/datamatrix_reader.py` — `process_all_images()`

**What it does:**
- Scans all PNG images in `label_crops_dir`
- For each image: tries decoding a DataMatrix barcode at 4 scales (0.25×, 0.5×, 1×, 2×)
- Parses decoded string into: LabID, Year, CaseNumber, Pot, BlockID, Section
- Validates Year range (2020–2030), normalizes fields
- Failed images copied to `datamatrix_failed_folder`
- Writes results to `datamatrix_output_excel` and a log file

**Config keys:** `label_crops_dir`, `datamatrix_output_excel`, `datamatrix_log_file`,
`datamatrix_failed_folder`, `output_run_dir`

**Output:** Excel file with FileName, DataMatrix, LabID, Year, CaseNumber, Pot, BlockID, Section, Status

**Dependency:** opencv-python, pylibdmtx, pandas, xlsxwriter

---

## Stage 3 — Stain Extraction

**File:** `core/stain_extractor.py` — `run_pipeline()`

**What it does:**
- Initializes EasyOCR reader (English, CPU)
- For each label PNG in `label_crops_dir`:
  - Optionally crops the image (`ocr_crop_config`)
  - Runs OCR to extract text tokens
  - Applies replacement map (`stain_replace_map_path`)
  - Detects composite stains (`A+B` → `A&B`)
  - Matches tokens against `stain_list_path`
  - If result is exactly `H&E`: runs ROI fallback (stage_runner wires this in-process)
- Writes results to `stain_output_excel` and a PDF report

**Config keys:** `label_crops_dir`, `stain_list_path`, `stain_replace_map_path`,
`stain_output_excel`, `stain_output_pdf`, `ocr_crop_config`, `roi_pxl_offset`

**Output:** Excel with FileName, Stain, Stain_Origin, Raw_OCR_Words columns; PDF report

**Dependency:** easyocr, opencv-python, pandas, reportlab

---

## Stage 4 — ROI Fallback Extraction

**File:** `core/metadata_extractor_utilities/` — `RoiMetadataExtractor`, `cli_main.cmd_run()`

**What it does:**
- Processes images in `datamatrix_failed_folder` that DataMatrix could not decode
- Uses layout classification (ResNet open-set) to select the correct ROI JSON layout
- Crops each defined ROI region, preprocesses for OCR
- Runs EasyOCR + fallback variants (CLAHE, inversion) per ROI
- Parses LabID, Year, CaseNumber, Pot, BlockID, Section, Stain from ROI text
- Attempts cross-group case number reconstruction for multi-group layouts
- Writes CSV, XLSX, and PDF outputs

**Config keys:** `ROI_set_file` or `roiset_selector.roiset_root`, `datamatrix_failed_folder`,
`output_run_dir`, `pxl_offset`, `ocr_min_conf`

**Output:** `roi_results.xlsx`, `roi_results.pdf`, `roi_words_wide.csv`

**Dependency:** easyocr, opencv-python, torch (for layout model), pandas

---

## Stage 5 — PASNet Validation

**File:** `core/pasnet_utilities/validator.py` — `run_pre_rename()`

**What it does:**
- Loads DataMatrix and Stain Excel outputs
- Reconstructs CaseID / SlideID if missing
- Queries PASNET/LIS to verify: case existence, slide existence, stain consistency
- Applies rule-based validation (via `rules_search`)
- Generates an Excel report with sheets: Datamatrix, Fallback, Suspicious_DBSlides, overrides
- The `overrides` sheet feeds back into `slide_id_generator` to override stain or slide ID

**Config keys:** `pasnet_validator.enabled`, `pasnet_validator.report_xlsx_path`,
`pasnet_validator.suspicious_output_dir`, `pasnet_validator.report_sheet_overrides`

**Output:** Excel report with validation decisions and overrides

**Dependency:** PASNET/LIS database credentials (Windows Credential Manager or service account)

---

## Stage 6 — Slide ID Generation + Rename + Routing

**File:** `core/slide_id_generator.py` — `run_pipeline()`

**What it does:**
- Merges DataMatrix Excel + Stain Excel + optional Factor/Extra/Color-marker Excels
- Applies PASNet overrides (from the `overrides` sheet if stage 5 ran)
- Computes: DataMatrix, CaseID, SlideID = `{DataMatrix}-{Stain}`
- Appends extras / factor fields to SlideID if configured
- For each slide: selects source file, determines destination
- **Research routing** (highest priority): color label detection or case blacklist → routes to research destination with no rename
- **PASNet routing**: suspicious/flagged → routes to configured suspicious folder
- **Routine routing**: builds final filename `{SlideID}{UTC-timestamp}.{ext}`, moves to `{final_output_dir}/{CaseID}/`
- **Failed routing**: unreadable slides → `failed_output_dir`
- Syncs final path/filename/status back to the enterprise DB `FileRecord`
- Writes `slide_metadata.xlsx` with the complete merge + routing decisions

**Config keys:** `staging_dir`, `final_output_dir`, `failed_output_dir`, `datamatrix_output_excel`,
`stain_output_excel`, `metadata_excel_path`, `slide_id_generator.*`, `color_label_routing.*`,
`pasnet_validator.*`, `dry_run`, `timestamp_tag_enabled`

**Output:** Renamed/moved WSI file, `slide_metadata.xlsx`

**Dependency:** pandas, openpyxl, OpenSlide (for scan timestamp extraction)

---

## Data Flow Between Stages

```
WSI file (staged_path)
  │
  ▼ Stage 1
label_crops_dir/
  <slide_stem>.png          ←── label PNG
  │
  ├─▶ Stage 2 ──▶ datamatrix_results.xlsx
  │                  FileName | DataMatrix | LabID | Year | CaseNumber | Pot | BlockID | Section
  │                  dm_failed/ (images that failed DM decode)
  │
  ├─▶ Stage 3 ──▶ stain_results.xlsx
  │                  FileName | Stain | Stain_Origin | ...
  │
  ├─▶ Stage 4 ──▶ roi_results.xlsx       (from dm_failed/)
  │                  FileName | DataMatrix | Stain | ...
  │
  ├─▶ Stage 5 ──▶ pasnet_report.xlsx
  │                  overrides sheet ──▶ fed back into Stage 6
  │
  └─▶ Stage 6 ──▶ {final_output_dir}/{CaseID}/{SlideID}.ext
                   slide_metadata.xlsx (complete merge)
```
