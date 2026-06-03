"""IDS7-compatible DICOM conversion utilities.

Ported from tool_WSIDicomizer/utils/wsidicom_utils.py.

Phase 11A fixes:
  1. dcmtk Windows path removed — resolved via DCMTK_BIN_DIR env or system PATH.
  2. store_as_IDS7_compatible_dcm() dispatcher added (was missing; old ConversionService
     always got None from getattr, silently falling back to placeholder_copy).

Phase 11A SVS fix:
  3. WSI files (.svs, .ndpi, .mrxs, …) are now routed through wsidicomizer CLI first
     (Step 1: WSI → DICOM folder), then IDS7 header patching is applied in-place
     (Step 2: patch LABEL/OVERVIEW/THUMBNAIL files).
     PIL.Image.open() is never called on WSI files — DecompressionBombError eliminated.
  4. WsidicomzerNotAvailableError raised (not RuntimeError) when wsidicomizer CLI is
     absent, so ConversionService can set failure_context.error_type = "missing_wsidicomizer".

Conversion routing table:
  Input is a directory               → patch_ids7_headers_for_dicom_folder() (in-place)
  Input is a WSI file (.svs etc.)    → convert_wsi_to_dicom_folder_with_wsidicomizer()
                                        then patch_ids7_headers_for_dicom_folder()
  Input is a single .dcm or image    → store_dcmdile_as_IDS7_compatible_dcm() (pydicom/PIL)
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

import pydicom
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence

from pathoryx_enterprise.services.dicom.engine.services.metaextraction_utils import (
    match_reconstruct_metadict_from_string,
)
from pathoryx_enterprise.services.dicom.engine.services.lis_client import (
    get_metadata_from_LIS,
)

logger = logging.getLogger(__name__)

# wsidicomizer / wsidicom are pip packages not installed in the enterprise venv.
# Only create_dcm_metadata_object requires them — not in the critical conversion path.
try:
    from wsidicomizer.metadata import WsiDicomizerMetadata as _WsiDicomizerMetadata
    _WSIDICOMIZER_AVAILABLE = True
except ImportError:
    _WSIDICOMIZER_AVAILABLE = False


# ─── WSI file detection ────────────────────────────────────────────────────────

# Extensions that are unambiguously WSI (pyramidal whole-slide) formats.
# These must NEVER be sent to PIL.Image.open() — they cause DecompressionBombError
# and produce incorrect output even when the bomb check is disabled.
_WSI_EXTENSIONS_DEFINITE = frozenset({
    ".svs", ".ndpi", ".mrxs", ".vms", ".vmu", ".scn", ".bif", ".czi",
})

# Flat image formats (safe for PIL).
_FLAT_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".gif"})

# TIFFs >= this size are treated as pyramidal WSI (safe default: 50 MB).
_TIFF_WSI_SIZE_THRESHOLD_BYTES = 50 * 1024 * 1024


def is_wsi_file(path: Path) -> bool:
    """
    Return True if path is a whole-slide image that must be converted via wsidicomizer.

    Routing:
      - .svs / .ndpi / .mrxs / .vms / .vmu / .scn / .bif / .czi → always WSI
      - .jpg / .jpeg / .png / .bmp / .gif                         → never WSI (flat images)
      - .tif / .tiff                                              → WSI if file > 50 MB
      - .dcm and everything else                                  → not WSI
    """
    ext = path.suffix.lower()
    if ext in _WSI_EXTENSIONS_DEFINITE:
        return True
    if ext in _FLAT_IMAGE_EXTENSIONS:
        return False
    if ext in (".tif", ".tiff"):
        try:
            return path.stat().st_size > _TIFF_WSI_SIZE_THRESHOLD_BYTES
        except OSError:
            return True  # conservative: treat as WSI when stat fails
    return False  # .dcm, unknown extensions


# ─── Custom exception ─────────────────────────────────────────────────────────

class WsidicomzerNotAvailableError(RuntimeError):
    """
    Raised when wsidicomizer CLI is not found on PATH or WSIDICOMIZER_BIN.

    ConversionService catches this specifically and sets
    failure_context["error_type"] = "missing_wsidicomizer" in the ConversionResult.
    """
    error_type = "missing_wsidicomizer"


# ─── dcmtk path resolution ────────────────────────────────────────────────────

def _get_dcmtk_cmd(tool: str, dcmtk_bin_dir: str = "") -> str:
    """
    Resolve full path for a dcmtk command-line tool.

    Resolution order:
      1. dcmtk_bin_dir argument (from config.dcmtk.bin_dir)
      2. DCMTK_BIN_DIR environment variable
      3. tool name only — system PATH (/usr/bin/dcmodify on this server)
    """
    bin_dir = dcmtk_bin_dir or os.environ.get("DCMTK_BIN_DIR", "")
    if bin_dir:
        return str(Path(bin_dir) / tool)
    return tool


# ─── IDS7 required DICOM tags ────────────────────────────────────────────────
#   (2200,0002) LabelText
#   (0040,0512) ContainerIdentifier
#   (0040,0560)[0].(0040,0600) SpecimenShortDescription "Staining: …"
#   (0008,0050) AccessionNumber
#   (0020,0010) StudyID
#   (0010,0010) PatientName   (optional, LIS-enriched)
#   (0010,0020) PatientID     (optional, LIS-enriched)

_IDS7_IMAGE_TYPES = {"LABEL", "OVERVIEW", "THUMBNAIL"}


# ─── Step 1: WSI → DICOM folder via wsidicomizer CLI ─────────────────────────

def convert_wsi_to_dicom_folder_with_wsidicomizer(
    input_path: str,
    output_dir: str,
    *,
    executable: str = "wsidicomizer",
    workers: int | None = None,
    timeout_seconds: int = 7200,
) -> str:
    """
    Convert a WSI file to a DICOM folder using the wsidicomizer CLI.

    Mirrors dicomized_import.py DicomizedWSIimport.convert_to_dicom() lines 293-316.

    Returns output_dir path on success.
    Raises WsidicomzerNotAvailableError if the CLI is not found.
    Raises RuntimeError if conversion fails (non-zero exit code or timeout).
    """
    if not shutil.which(executable):
        raise WsidicomzerNotAvailableError(
            f"wsidicomizer CLI not found: {executable!r}. "
            "Install with: pip install wsidicomizer  "
            "or set wsidicomizer.executable in dicom_config.yaml."
        )

    os.makedirs(output_dir, exist_ok=True)

    cmd = [executable, "--input", input_path, "--output", output_dir]
    if workers is not None:
        cmd += ["-w", str(workers)]

    logger.info("wsidicomizer start: %s → %s", input_path, output_dir)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"wsidicomizer timed out after {timeout_seconds}s for {input_path!r}"
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"wsidicomizer failed (rc={result.returncode}) for {input_path!r}: "
            f"{result.stderr[:1000]}"
        )

    logger.info("wsidicomizer done: %s → %s", input_path, output_dir)
    return output_dir


# ─── Step 2: Patch IDS7 DICOM headers in a DICOM WSI folder ──────────────────

def patch_ids7_headers_for_dicom_folder(
    dicom_folder: str,
    match_construct_patterns: dict,
    *,
    dcmtk_bin_dir: str = "",
    lis_cursor=None,
) -> dict:
    """
    Apply IDS7/Sectra DICOM header patches in-place to a DICOM WSI folder.

    This is always Step 2 — called after wsidicomizer (or after detecting an
    existing DICOM folder). Uses dcmtk dcmdump + dcmodify to inject required
    IDS7 tags into LABEL/OVERVIEW/THUMBNAIL image-type files only.
    Large tile files are untouched.

    Returns out_metadata_dict extracted from the folder name.
    """
    return store_dcmwsifolder_as_IDS7_compatible_dcm(
        in_folder=dicom_folder,
        match_construct_patterns=match_construct_patterns,
        out_folder=dicom_folder,         # in-place: both paths are the same
        enrich_header_with_patient_infos=(lis_cursor is not None),
        cursor=lis_cursor,
        dcmtk_bin_dir=dcmtk_bin_dir,
    )


# ─── Main dispatcher ──────────────────────────────────────────────────────────

def store_as_IDS7_compatible_dcm(
    in_path: str,
    match_construct_patterns: dict,
    out_folder: str,
    *,
    dcmtk_bin_dir: str = "",
    lis_cursor=None,
    wsidicomizer_executable: str = "wsidicomizer",
    wsidicomizer_workers: int | None = None,
    wsidicomizer_timeout: int = 7200,
) -> tuple[dict, str]:
    """
    Three-path dispatcher: convert in_path to IDS7-compatible DICOM.

    Path A — Directory (existing DICOM WSI folder):
      Patch IDS7 headers in-place (dcmtk). out_folder ignored.

    Path B — WSI file (.svs, .ndpi, .mrxs, …):
      1. wsidicomizer CLI → DICOM folder under out_folder/stem/
      2. IDS7 header patching in-place on the generated DICOM folder.
      Raises WsidicomzerNotAvailableError if CLI is not found.

    Path C — Single .dcm file or flat image (.jpg, .png, …):
      pydicom tag injection (no PIL for WSI — DecompressionBombError fix).

    Returns (metadata_dict, output_path_str).
    """
    path = Path(in_path)

    # ── Path A: existing DICOM folder ────────────────────────────────────────
    if path.is_dir():
        metadata = patch_ids7_headers_for_dicom_folder(
            in_path, match_construct_patterns,
            dcmtk_bin_dir=dcmtk_bin_dir,
            lis_cursor=lis_cursor,
        )
        return metadata, in_path

    # ── Path B: WSI file → wsidicomizer → patch ───────────────────────────
    if is_wsi_file(path):
        dicom_output = os.path.join(out_folder, path.stem)
        convert_wsi_to_dicom_folder_with_wsidicomizer(
            in_path,
            dicom_output,
            executable=wsidicomizer_executable,
            workers=wsidicomizer_workers,
            timeout_seconds=wsidicomizer_timeout,
        )
        metadata = patch_ids7_headers_for_dicom_folder(
            dicom_output, match_construct_patterns,
            dcmtk_bin_dir=dcmtk_bin_dir,
            lis_cursor=lis_cursor,
        )
        return metadata, dicom_output

    # ── Path C: single DICOM file or flat image ───────────────────────────
    return store_dcmdile_as_IDS7_compatible_dcm(
        in_file=in_path,
        match_construct_patterns=match_construct_patterns,
        out_folder=out_folder,
        dcmtk_bin_dir=dcmtk_bin_dir,
    )


# ─── Single-file conversion (pydicom path — NOT for WSI) ─────────────────────

def store_dcmdile_as_IDS7_compatible_dcm(
    in_file: str,
    match_construct_patterns: dict,
    out_folder: str,
    *,
    dcmtk_bin_dir: str = "",
) -> tuple[dict, str]:
    """
    Convert/patch a single .dcm file or flat image to IDS7-compatible DICOM.

    FOR FLAT IMAGES ONLY (.dcm, .jpg, .png, .bmp …).
    DO NOT pass WSI files here — use store_as_IDS7_compatible_dcm() which routes
    WSI files through wsidicomizer before calling this function.

    Returns (out_metadata_dict, out_file_path).
    """
    os.makedirs(out_folder, exist_ok=True)

    if in_file.lower().endswith(".dcm"):
        ds = pydicom.dcmread(in_file)
        case_id_string = str(getattr(ds, "PatientID", None) or "")
        ds.PatientID = None
    else:
        case_id_string = os.path.basename(in_file).split(".")[0]
        out_path_temp = os.path.join(out_folder, f"{case_id_string}.dcm")
        ds = convert_img_to_dcm_object(in_file, out_path_temp, modality="SM")

    out_metadata_dict = match_reconstruct_metadict_from_string(
        case_id_string, match_construct_patterns
    )

    # Inject IDS7 required tags
    ds.add_new(tag=(0x2200, 0x0002), VR="UT", value=out_metadata_dict["slide_id"])
    ds.ContainerIdentifier = out_metadata_dict["slide_id"]

    specimen_item = Dataset()
    stain = out_metadata_dict.get("staining") or "unknown"
    specimen_item.SpecimenShortDescription = f"Staining: {stain}"
    ds.SpecimenDescriptionSequence = Sequence([specimen_item])

    ds.AccessionNumber = out_metadata_dict["accession_number"]
    ds.StudyID = out_metadata_dict["study_id"]

    safe_name = case_id_string.replace("/", "_")
    out_file = os.path.join(out_folder, f"{safe_name}.dcm")
    ds.save_as(out_file)

    return out_metadata_dict, out_file


# ─── DICOM WSI folder in-place patching ──────────────────────────────────────

def store_dcmwsifolder_as_IDS7_compatible_dcm(
    in_folder: str,
    match_construct_patterns: dict,
    out_folder: str,
    *,
    enrich_header_with_patient_infos: bool = True,
    cursor=None,
    dcmtk_bin_dir: str = "",
) -> dict:
    """
    Patch DICOM headers of LABEL/OVERVIEW/THUMBNAIL files in a DICOM WSI folder.

    Designed for in-place use: pass the same path for in_folder and out_folder
    (which is how patch_ids7_headers_for_dicom_folder() calls it). If the paths
    differ, the DCM files must already exist in out_folder before calling this.

    Uses dcmtk (dcmdump + dcmodify). Large tile files are skipped — only the small
    metadata image-type files are patched, keeping processing time short.

    Returns out_metadata_dict extracted from in_folder's basename.
    """
    if not os.path.isdir(in_folder):
        raise ValueError(f"in_folder must be a directory: {in_folder!r}")

    os.makedirs(out_folder, exist_ok=True)
    case_id_string = os.path.basename(in_folder)
    out_metadata_dict = match_reconstruct_metadict_from_string(
        case_id_string, match_construct_patterns
    )

    # Optional LIS patient info enrichment
    patient_info: dict | None = None
    if enrich_header_with_patient_infos and cursor is not None:
        try:
            study_id = out_metadata_dict.get("study_id", "")
            if study_id:
                patient_infos = get_metadata_from_LIS(
                    [study_id], ["GUID", "FirstName", "LastName"], cursor
                )
                if patient_infos:
                    first_key = next(iter(patient_infos))
                    if patient_infos[first_key]:
                        patient_info = patient_infos[first_key]
                        out_metadata_dict["patient_info"] = patient_info
        except Exception as exc:
            logger.warning("LIS enrichment failed (non-fatal): %s", exc)

    dcmdump_cmd = _get_dcmtk_cmd("dcmdump", dcmtk_bin_dir)
    dcmodify_cmd = _get_dcmtk_cmd("dcmodify", dcmtk_bin_dir)

    stain = out_metadata_dict.get("staining") or "unknown"
    slide_id = out_metadata_dict["slide_id"]
    accession = out_metadata_dict["accession_number"]
    study_id = out_metadata_dict["study_id"]

    dcm_files = sorted(
        [f for f in os.listdir(in_folder) if f.lower().endswith(".dcm")],
        key=lambda x: os.path.getsize(os.path.join(in_folder, x)),
    )

    processed = 0
    for dcm_file in dcm_files:
        src_file = os.path.join(in_folder, dcm_file)
        out_file = os.path.join(out_folder, dcm_file)

        # Read image type from src_file — only patch LABEL/OVERVIEW/THUMBNAIL
        try:
            dump_result = subprocess.run(
                [dcmdump_cmd, "+P", "0008,0008", src_file],
                capture_output=True, text=True, check=False,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"dcmdump not found at {dcmdump_cmd!r}. "
                "Install dcmtk (apt-get install dcmtk) or set DCMTK_BIN_DIR."
            )

        if dump_result.returncode != 0 or "ERROR" in dump_result.stderr:
            logger.warning("dcmdump failed for %s — skipping", dcm_file)
            continue

        if not any(t in dump_result.stdout for t in _IDS7_IMAGE_TYPES):
            continue  # skip main tile files

        modify_args = [
            "-i", f"(2200,0002)={slide_id}",
            "-i", f"(0040,0512)={slide_id}",
            "-i", f"(0008,0050)={accession}",
            "-i", f"(0020,0010)={study_id}",
            "-i", f"(0040,0560)[0].(0040,0600)=Staining: {stain}",
        ]
        if patient_info:
            modify_args += [
                "-i", f"(0010,0010)={patient_info.get('LastName','')}^{patient_info.get('FirstName','')}",
                "-i", f"(0010,0020)={patient_info.get('GUID','')}",
            ]

        try:
            mod_result = subprocess.run(
                [dcmodify_cmd] + modify_args + [out_file],
                capture_output=True, text=True, check=False,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"dcmodify not found at {dcmodify_cmd!r}. "
                "Install dcmtk (apt-get install dcmtk) or set DCMTK_BIN_DIR."
            )

        if mod_result.returncode != 0 or "ERROR" in mod_result.stderr:
            raise RuntimeError(
                f"dcmodify failed for {dcm_file}: {mod_result.stderr[:500]}"
            )

        processed += 1
        if processed >= len(_IDS7_IMAGE_TYPES):
            break  # all three metadata image types done

    return out_metadata_dict


# ─── Flat image → DICOM object (non-WSI only) ─────────────────────────────────

def convert_img_to_dcm_object(img_path: str, out_path: str, modality: str = "SM"):
    """
    Wrap a flat image (JPEG/PNG/BMP — NOT WSI) in a DICOM dataset.

    Returns a pydicom FileDataset (not yet saved to disk).
    modality: "SM" (VL Microscopic Image) or "DX" (Digital X-Ray).
    """
    import datetime

    import numpy as np
    from PIL import Image
    from pydicom.dataset import FileDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    assert modality in ("SM", "DX"), f"modality must be 'SM' or 'DX', got {modality!r}"

    img = _load_and_normalize_image(img_path)
    pixel_array = np.array(img)
    rows, cols, samples = pixel_array.shape

    now = datetime.datetime.now()
    series_date = now.strftime("%Y%m%d")
    series_time = now.strftime("%H%M%S")
    sop_class = (
        "1.2.840.10008.5.1.4.1.1.77.1.2" if modality == "SM"
        else "1.2.840.10008.5.1.4.1.1.1.1"
    )

    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = sop_class
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = generate_uid()

    ds = FileDataset(out_path, {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.PatientName = None
    ds.PatientID = None
    ds.SeriesInstanceUID = generate_uid()
    ds.SeriesNumber = 1
    ds.SeriesDate = series_date
    ds.SeriesTime = series_time
    ds.Modality = modality
    ds.InstitutionName = "Pathoryx"
    ds.SeriesDescription = "Slide Image"
    ds.StudyInstanceUID = generate_uid()
    ds.StudyDate = series_date
    ds.StudyTime = series_time
    ds.StudyID = "1"
    ds.Rows = rows
    ds.Columns = cols
    ds.SamplesPerPixel = samples
    ds.PhotometricInterpretation = "RGB"
    ds.PlanarConfiguration = 0
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = pixel_array.tobytes()
    return ds


def _load_and_normalize_image(path: str):
    from PIL import Image
    img = Image.open(path)
    if img.mode in ("P", "LA", "RGBA"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    elif img.mode == "I;16":
        img = img.point(lambda x: x / 256).convert("L").convert("RGB")
    return img


# ─── wsidicomizer metadata builder (optional, not in main conversion path) ────

def create_dcm_metadata_object(*args, **kwargs):
    """
    Build a WsiDicomizerMetadata object. Requires wsidicomizer pip package.
    Not used in the main IDS7 conversion path — store_as_IDS7_compatible_dcm
    uses wsidicomizer CLI instead of the Python API.
    """
    if not _WSIDICOMIZER_AVAILABLE:
        raise ImportError(
            "wsidicomizer package required. Install: pip install wsidicomizer wsidicom"
        )
    raise NotImplementedError(
        "create_dcm_metadata_object requires wsidicomizer — install the package."
    )
