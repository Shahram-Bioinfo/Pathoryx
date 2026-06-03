"""
SlideID parser for RecoverySentry.

Validates and parses the standard Pathoryx slide filename format:
  <CaseID><Pot>-<Block>-<Section>-<Stain>[_UTC<timestamp>].<ext>

Examples:
  N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs
  N2024002863SA-1-1-H&E.svs
  N2024004455SA-1-1-PAS_UTC2024-08-22T08_36_39Z.ndpi

This is a standalone validator — it does NOT use or modify the BabelShark
slide_id_generator.py, which generates slide IDs via label/OCR pipeline.
This parser validates/parses already-named files.

Timestamp format in filenames (colons replaced with underscores for Windows compat):
  UTC{YYYY}-{MM}-{DD}T{HH}_{MM}_{SS}Z
  e.g. UTC2024-08-22T08_36_39Z

ISO-Z format used internally and in DB:
  {YYYY}-{MM}-{DD}T{HH}:{MM}:{SS}Z
  e.g. 2024-08-22T08:36:39Z
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# CaseID: N + 10 digits (e.g. N2024002863)
_CASE_ID = r"(N\d{10})"
# Pot: one or more uppercase letters (e.g. SA, A, B)
_POT = r"([A-Z]+)"
# Block/Section: one or more digits
_NUM = r"(\d+)"
# Stain: letters, digits, &, +, - but not starting with _ or digit (e.g. H&E, PAS, IHC-CD3)
_STAIN = r"([A-Za-z][A-Za-z0-9&+\-]*)"
# Timestamp tag (optional): _UTC followed by date-time with underscores for colons
_TS_SUFFIX = r"(?:_(UTC\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}Z))?"

SLIDE_ID_RE = re.compile(
    rf"^{_CASE_ID}{_POT}-{_NUM}-{_NUM}-{_STAIN}{_TS_SUFFIX}$"
)

SUPPORTED_EXTENSIONS = frozenset({
    ".svs", ".ndpi", ".mrxs", ".tiff", ".tif", ".scn", ".czi", ".vsi", ".bif",
})

# How the timestamp tag looks in a filename (underscores instead of colons)
_FILENAME_TS_RE = re.compile(
    r"^UTC(\d{4})-(\d{2})-(\d{2})T(\d{2})_(\d{2})_(\d{2})Z$"
)


@dataclass(frozen=True)
class ParsedSlideID:
    """Structured result of parsing a slide filename."""
    case_id: str
    pot: str
    block: str
    section: str
    stain: str
    timestamp_tag: Optional[str]      # filename-safe tag, e.g. "UTC2024-08-22T08_36_39Z"
    timestamp_iso_z: Optional[str]    # ISO-Z form, e.g. "2024-08-22T08:36:39Z"
    extension: str                    # lowercase, e.g. ".svs"
    slide_id_base: str                # without timestamp and extension
    slide_id_with_ts: str             # with timestamp if present, without extension
    final_filename: str               # complete filename ready for final/

    @property
    def has_timestamp(self) -> bool:
        return self.timestamp_tag is not None


def parse_slide_id(filename: str) -> Optional[ParsedSlideID]:
    """
    Parse a WSI filename into structured SlideID components.

    Returns None if the filename does not match the expected pattern.
    The caller is responsible for handling None as an invalid/ambiguous slide.
    """
    p = Path(filename)
    stem = p.stem
    ext = p.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        return None

    m = SLIDE_ID_RE.match(stem)
    if not m:
        return None

    case_id, pot, block, section, stain, ts_tag = m.groups()

    slide_id_base = f"{case_id}{pot}-{block}-{section}-{stain}"
    slide_id_with_ts = f"{slide_id_base}_{ts_tag}" if ts_tag else slide_id_base
    final_filename = f"{slide_id_with_ts}{ext}"
    ts_iso_z = _filename_ts_to_iso_z(ts_tag) if ts_tag else None

    return ParsedSlideID(
        case_id=case_id,
        pot=pot,
        block=block,
        section=section,
        stain=stain,
        timestamp_tag=ts_tag,
        timestamp_iso_z=ts_iso_z,
        extension=ext,
        slide_id_base=slide_id_base,
        slide_id_with_ts=slide_id_with_ts,
        final_filename=final_filename,
    )


def iso_z_to_filename_ts(iso_z: str) -> str:
    """
    Convert ISO-Z timestamp to filename-safe tag.

    "2024-08-22T08:36:39Z" → "UTC2024-08-22T08_36_39Z"
    """
    # Replace colons in the time portion with underscores
    # The date portion uses hyphens which are already filename-safe
    core = iso_z.rstrip("Z")
    date_part, time_part = core.split("T")
    time_safe = time_part.replace(":", "_")
    return f"UTC{date_part}T{time_safe}Z"


def build_final_filename(parsed: ParsedSlideID, iso_z: Optional[str] = None) -> str:
    """
    Build the canonical final filename.

    If parsed already has a timestamp: uses it.
    If iso_z is provided: appends it as a filename-safe tag.
    """
    if parsed.has_timestamp:
        return parsed.final_filename
    if iso_z:
        ts_tag = iso_z_to_filename_ts(iso_z)
        return f"{parsed.slide_id_base}_{ts_tag}{parsed.extension}"
    return parsed.final_filename


def _filename_ts_to_iso_z(ts_tag: str) -> Optional[str]:
    """
    Convert filename timestamp tag to ISO-Z string.

    "UTC2024-08-22T08_36_39Z" → "2024-08-22T08:36:39Z"
    """
    m = _FILENAME_TS_RE.match(ts_tag)
    if not m:
        return None
    yyyy, mm, dd, hh, mi, ss = m.groups()
    try:
        dt = datetime(
            int(yyyy), int(mm), int(dd),
            int(hh), int(mi), int(ss),
            tzinfo=timezone.utc,
        )
        return dt.isoformat().replace("+00:00", "Z")
    except ValueError:
        return None
