from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

try:
    import openslide
    OPENSLIDE_AVAILABLE = True
except Exception:
    openslide = None
    OPENSLIDE_AVAILABLE = False


SCANNER_ID_CANDIDATE_KEYS = [
    "aperio.ScanScope ID",
    "aperio.Scanscope ID",
    "hamamatsu.NDP.S/N",
    "hamamatsu.Product",
    "tiff.Model",
]

SCANNER_MODEL_CANDIDATE_KEYS = [
    "aperio.ScannerType",
    "aperio.Scanner Type",
    "tiff.Model",
    "hamamatsu.Product",
]

SCANNER_VENDOR_CANDIDATE_KEYS = [
    "openslide.vendor",
    "tiff.Make",
    "tiff.Software",
]


def load_yaml(path: str | Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def first_non_empty(properties: Dict[str, Any], keys: List[str]) -> Tuple[Optional[str], Optional[str]]:
    for key in keys:
        value = properties.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return key, text
    return None, None


def normalize_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def extract_wsi_metadata(file_path: str | Path) -> Dict[str, Any]:
    path = Path(file_path).resolve()

    result: Dict[str, Any] = {
        "input_type": "wsi_file",
        "path": str(path),
        "name": path.name,
        "stem": path.stem,
        "suffix": path.suffix.lower(),
        "openslide_available": OPENSLIDE_AVAILABLE,
        "openslide_detected": False,
        "properties": {},
    }

    if not OPENSLIDE_AVAILABLE:
        return result

    try:
        slide = openslide.OpenSlide(str(path))
        result["openslide_detected"] = True

        props = dict(slide.properties)
        result["properties"] = props
        result["openslide_vendor"] = props.get("openslide.vendor")
        result["magnification"] = props.get("aperio.AppMag") or props.get("openslide.objective-power")
        result["mpp_x"] = props.get("openslide.mpp-x")
        result["mpp_y"] = props.get("openslide.mpp-y")
        result["dimensions"] = slide.dimensions
        result["level_count"] = slide.level_count
        result["scanner_id_raw"] = props.get("aperio.ScannerID") or props.get("hamamatsu.Source")
        result["scanner_model_raw"] = props.get("aperio.ScanScope ID") or props.get("openslide.vendor")
        result["slide_id_raw"] = props.get("aperio.Filename") or path.stem

        slide.close()

    except Exception as exc:
        result["openslide_error"] = str(exc)

    return result


def extract_metadata(file_path: str | Path) -> Dict[str, Any]:
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in {".svs", ".ndpi", ".tiff", ".tif", ".scn", ".mrxs", ".bif"}:
        return extract_wsi_metadata(path)

    return {
        "input_type": "unknown",
        "path": str(path.resolve()),
        "name": path.name,
        "stem": path.stem,
        "suffix": suffix,
        "properties": {},
    }


def detect_scanner_family(
    vendor_raw: Optional[str],
    model_raw: Optional[str],
    scanner_id_raw: Optional[str],
    properties: Dict[str, Any],
) -> str:
    haystack = " | ".join(
        [
            vendor_raw or "",
            model_raw or "",
            scanner_id_raw or "",
            json.dumps(properties, ensure_ascii=False),
        ]
    ).lower()

    if "hamamatsu" in haystack:
        return "hamamatsu"
    if "glissando" in haystack:
        return "glissando"
    if "gt450" in haystack:
        return "leica_aperio"
    if "aperio" in haystack:
        return "leica_aperio"
    return "unknown"


def extract_magnification(properties: Dict[str, Any]) -> Optional[float]:
    candidates = [
        properties.get("aperio.AppMag"),
        properties.get("hamamatsu.Objective.Lens.Magnificant"),
        properties.get("hamamatsu.SourceLens"),
        properties.get("openslide.objective-power"),
    ]

    for value in candidates:
        if value is None:
            continue
        try:
            return float(str(value).strip().replace(",", "."))
        except Exception:
            continue
    return None


def extract_mpp(properties: Dict[str, Any]) -> Optional[float]:
    candidates = [
        properties.get("aperio.MPP"),
        properties.get("openslide.mpp-x"),
        properties.get("openslide.mpp-y"),
    ]

    for value in candidates:
        if value is None:
            continue
        try:
            return float(str(value).strip().replace(",", "."))
        except Exception:
            continue
    return None


def extract_scan_time(properties: Dict[str, Any]) -> Dict[str, Optional[str]]:
    date_key, date_value = first_non_empty(
        properties,
        ["aperio.Date", "hamamatsu.Created", "tiff.DateTime"],
    )
    time_key, time_value = first_non_empty(
        properties,
        ["aperio.Time", "AcquisitionTime", "StudyTime"],
    )
    tz_key, tz_value = first_non_empty(
        properties,
        ["aperio.Time Zone"],
    )

    return {
        "scan_date_key": date_key,
        "scan_date_raw": date_value,
        "scan_time_key": time_key,
        "scan_time_raw": time_value,
        "scan_tz_key": tz_key,
        "scan_tz_raw": tz_value,
    }


def extract_slide_id(properties: Dict[str, Any], file_stem: str) -> Dict[str, Optional[str]]:
    key, value = first_non_empty(
        properties,
        ["hamamatsu.Reference", "aperio.Slide", "ContainerIdentifier", "LabelText"],
    )

    if value is not None:
        return {
            "slide_id_source_key": key,
            "slide_id_raw": value,
        }

    return {
        "slide_id_source_key": "file_stem",
        "slide_id_raw": file_stem,
    }


def resolve_scanner_name(
    scanner_id_raw: Optional[str],
    model_raw: Optional[str],
    family: str,
    cfg: Dict[str, Any],
) -> Optional[str]:
    scanner_cfg = cfg.get("scanner_identity", {}) if isinstance(cfg.get("scanner_identity"), dict) else {}

    alias_by_raw_id = scanner_cfg.get("alias_by_raw_id", {}) if isinstance(scanner_cfg.get("alias_by_raw_id"), dict) else {}
    alias_by_model = scanner_cfg.get("alias_by_model", {}) if isinstance(scanner_cfg.get("alias_by_model"), dict) else {}
    alias_by_family = scanner_cfg.get("alias_by_family", {}) if isinstance(scanner_cfg.get("alias_by_family"), dict) else {}

    if scanner_id_raw and scanner_id_raw in alias_by_raw_id:
        return str(alias_by_raw_id[scanner_id_raw]).strip()
    if model_raw and model_raw in alias_by_model:
        return str(alias_by_model[model_raw]).strip()
    if family in alias_by_family:
        return str(alias_by_family[family]).strip()
    if scanner_id_raw:
        return scanner_id_raw
    if model_raw:
        return model_raw
    return None


def normalize_metadata(raw_metadata: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    properties = raw_metadata.get("properties", {}) if isinstance(raw_metadata.get("properties"), dict) else {}

    vendor_key, vendor_raw = first_non_empty(properties, SCANNER_VENDOR_CANDIDATE_KEYS)
    scanner_id_key, scanner_id_raw = first_non_empty(properties, SCANNER_ID_CANDIDATE_KEYS)
    model_key, model_raw = first_non_empty(properties, SCANNER_MODEL_CANDIDATE_KEYS)

    vendor_raw = normalize_text(vendor_raw)
    scanner_id_raw = normalize_text(scanner_id_raw)
    model_raw = normalize_text(model_raw)

    scanner_family = detect_scanner_family(
        vendor_raw=vendor_raw,
        model_raw=model_raw,
        scanner_id_raw=scanner_id_raw,
        properties=properties,
    )

    scanner_name = resolve_scanner_name(
        scanner_id_raw=scanner_id_raw,
        model_raw=model_raw,
        family=scanner_family,
        cfg=cfg,
    )

    slide_info = extract_slide_id(properties, file_stem=str(raw_metadata.get("stem", "")))
    scan_time_info = extract_scan_time(properties)

    return {
        "path": raw_metadata.get("path"),
        "name": raw_metadata.get("name"),
        "suffix": raw_metadata.get("suffix"),
        "input_type": raw_metadata.get("input_type"),
        "scanner_vendor_key": vendor_key,
        "scanner_vendor_raw": vendor_raw,
        "scanner_id_key": scanner_id_key,
        "scanner_id_raw": scanner_id_raw,
        "scanner_model_key": model_key,
        "scanner_model_raw": model_raw,
        "scanner_family": scanner_family,
        "scanner_name": scanner_name,
        "magnification": extract_magnification(properties),
        "mpp": extract_mpp(properties),
        **slide_info,
        **scan_time_info,
        "associated_images": raw_metadata.get("associated_images", []),
        "openslide_detected": raw_metadata.get("openslide_detected"),
        "vendor_reported_by_openslide": raw_metadata.get("vendor") or raw_metadata.get("openslide_vendor"),
        "raw_properties": properties,
    }


def extract_and_normalize_metadata(
    file_path: str | Path,
    config: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    raw = extract_metadata(file_path)
    normalized = normalize_metadata(raw, config)
    return raw, normalized