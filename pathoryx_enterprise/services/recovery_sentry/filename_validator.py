"""
Centralized WSI filename validation engine.

Shared by: dashboard validate-filename endpoint, execute_technician_rename pre-flight.
Deterministic. No filesystem or database access.

Validation categories (in evaluation order):
  FORBIDDEN INPUT
    empty / whitespace-only                    error:empty
    path separators  (/ \\)                    error:path_traversal
    parent-traversal (..)                      error:dotdot
    control characters (U+0000–001F, U+007F)   error:control_chars
    hidden unicode (Cf category: BOM, ZWSP,
      directional overrides, etc.)             error:hidden_unicode
    double extension  (.svs.bak)               error:double_extension
    unsupported extension                      error:invalid_extension

  FORMAT
    SlideID pattern mismatch                   error:invalid_structure | error:unrecognized
    stain synonym (HE → H&E)                   warning:stain_synonym + normalized_filename

  CLINICAL SAFETY
    timestamp absent                           warning:no_timestamp
    extension differs from original WSI        warning:extension_mismatch
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .slide_id_parser import SUPPORTED_EXTENSIONS, parse_slide_id

# ---------------------------------------------------------------------------
# Stain synonym table
# Keys: lowercase non-canonical form → canonical clinical spelling
# ---------------------------------------------------------------------------

STAIN_SYNONYMS: dict[str, str] = {
    "he": "H&E",
    "h-e": "H&E",
    "h+e": "H&E",
    "haematoxylin": "H&E",
    "hematoxylin": "H&E",
    "haematoxylin-eosin": "H&E",
    "hematoxylin-eosin": "H&E",
    "pas-d": "PAS",
    "pasd": "PAS",
    "masson": "MT",
    "massons": "MT",
    "masson-trichrome": "MT",
    "zn": "Ziehl",
    "ziehl-neelsen": "Ziehl",
    "ziehlneelsen": "Ziehl",
    "ki67": "KI-67",
    "grocotts": "Grocott",
    "grocott-methenamine": "Grocott",
    "elastin-van-gieson": "EVG",
}

# CaseID prefix for partial-match diagnosis
_CASE_ID_PREFIX_RE = re.compile(r"^(N\d{10})")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ValidationIssue:
    code: str
    message: str

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


@dataclass
class ParsedComponents:
    case_id: Optional[str]
    pot: Optional[str]
    block: Optional[str]
    section: Optional[str]
    stain: Optional[str]
    timestamp: Optional[str]
    extension: Optional[str]

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "pot": self.pot,
            "block": self.block,
            "section": self.section,
            "stain": self.stain,
            "timestamp": self.timestamp,
            "extension": self.extension,
        }


@dataclass
class FilenameValidationResult:
    filename: str
    classification: str           # valid | partially_valid | invalid
    components: Optional[ParsedComponents]
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    suggested_correction: Optional[str] = None
    normalized_filename: Optional[str] = None  # set when stain synonym is applied

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "classification": self.classification,
            "components": self.components.to_dict() if self.components else None,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "suggested_correction": self.suggested_correction,
            "normalized_filename": self.normalized_filename,
        }


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class FilenameValidator:
    """
    Stateless WSI filename validator. Thread-safe; safe to call per keystroke.
    """

    @classmethod
    def validate(
        cls,
        filename: str,
        *,
        original_extension: Optional[str] = None,
        config_requires_timestamp: bool = False,
    ) -> FilenameValidationResult:
        """
        Validate a proposed WSI filename.

        Args:
            filename: Proposed filename string (may be untrusted input).
            original_extension: Extension of the source WSI file, e.g. '.svs'.
                When provided, a mismatch triggers warning:extension_mismatch.
            config_requires_timestamp: True when the recovery config has
                add_timestamp_if_missing=False — absence is flagged more strongly.

        Returns:
            FilenameValidationResult (never raises).
        """
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        stripped = (filename or "").strip()

        result = FilenameValidationResult(
            filename=stripped,
            classification="invalid",
            components=None,
        )

        # ── 1. Empty ──────────────────────────────────────────────────────────
        if not stripped:
            result.errors = [ValidationIssue("empty", "Filename cannot be empty")]
            return result

        # ── 2. Path traversal ─────────────────────────────────────────────────
        if "/" in stripped or "\\" in stripped:
            result.errors = [ValidationIssue(
                "path_traversal",
                "Filename must not contain directory separators (/ or \\)"
            )]
            return result

        if ".." in stripped:
            result.errors = [ValidationIssue("dotdot", "Filename must not contain '..'")]
            return result

        if stripped != Path(stripped).name:
            result.errors = [ValidationIssue(
                "path_traversal",
                "Filename contains path components; only a plain filename is accepted"
            )]
            return result

        # ── 3. Control characters ─────────────────────────────────────────────
        if re.search(r"[\x00-\x1f\x7f]", stripped):
            result.errors = [ValidationIssue(
                "control_chars",
                "Filename contains control characters — remove non-printable characters "
                "before submitting"
            )]
            return result

        # ── 4. Hidden unicode (Unicode Cf category: BOM, ZWSP, directional) ──
        for ch in stripped:
            if unicodedata.category(ch) == "Cf":
                name = unicodedata.name(ch, "unknown")
                result.errors = [ValidationIssue(
                    "hidden_unicode",
                    f"Filename contains a hidden formatting character "
                    f"(U+{ord(ch):04X} \"{name}\"). "
                    "Delete and retype the filename to remove it."
                )]
                return result

        # ── 5. Extension ──────────────────────────────────────────────────────
        p = Path(stripped)
        ext = p.suffix.lower()
        stem = p.stem

        if not ext:
            result.errors = [ValidationIssue(
                "no_extension",
                "Filename must include a file extension (e.g. .svs, .ndpi)"
            )]
            return result

        # Double extension: stem itself ends with a supported extension
        inner_ext = Path(stem).suffix.lower()
        if inner_ext in SUPPORTED_EXTENSIONS:
            result.errors = [ValidationIssue(
                "double_extension",
                f"Double extension detected ('{inner_ext}{ext}'). "
                "Remove the inner extension so only one remains."
            )]
            return result

        if ext not in SUPPORTED_EXTENSIONS:
            result.errors = [ValidationIssue(
                "invalid_extension",
                f"'{ext}' is not a supported WSI format. "
                f"Accepted: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )]
            return result

        # ── 6. SlideID pattern ────────────────────────────────────────────────
        parsed = parse_slide_id(stripped)

        if parsed is not None:
            canonical_stain = STAIN_SYNONYMS.get(parsed.stain.lower(), parsed.stain)
            normalized_filename: Optional[str] = None

            if canonical_stain != parsed.stain:
                ts_part = f"_{parsed.timestamp_tag}" if parsed.has_timestamp else ""
                norm_stem = (
                    f"{parsed.case_id}{parsed.pot}"
                    f"-{parsed.block}-{parsed.section}-{canonical_stain}{ts_part}"
                )
                normalized_filename = f"{norm_stem}{parsed.extension}"
                warnings.append(ValidationIssue(
                    "stain_synonym",
                    f"Non-canonical stain name '{parsed.stain}' — "
                    f"standard clinical spelling is '{canonical_stain}'",
                ))

            if not parsed.has_timestamp:
                if config_requires_timestamp:
                    warnings.append(ValidationIssue(
                        "no_timestamp",
                        "Timestamp absent — this configuration requires a UTC timestamp. "
                        "The system will attempt to extract scan time from WSI metadata; "
                        "if unavailable, the file will be held for manual review.",
                    ))
                else:
                    warnings.append(ValidationIssue(
                        "no_timestamp",
                        "Timestamp absent — scan acquisition time will be auto-extracted "
                        "from WSI metadata during processing.",
                    ))

            if original_extension and ext != original_extension.lower():
                warnings.append(ValidationIssue(
                    "extension_mismatch",
                    f"Extension '{ext}' differs from the original file's extension "
                    f"'{original_extension.lower()}'. A mismatch can disrupt scanner "
                    f"vendor detection and DICOM conversion.",
                ))

            result.classification = "valid" if parsed.has_timestamp else "partially_valid"
            result.components = ParsedComponents(
                case_id=parsed.case_id,
                pot=parsed.pot,
                block=parsed.block,
                section=parsed.section,
                stain=parsed.stain,
                timestamp=parsed.timestamp_iso_z,
                extension=parsed.extension,
            )
            result.warnings = warnings
            result.normalized_filename = normalized_filename
            return result

        # ── 7. Partial match / diagnostic ─────────────────────────────────────
        m = _CASE_ID_PREFIX_RE.match(stem)
        if m:
            case_id = m.group(1)
            result.classification = "partially_valid"
            result.components = ParsedComponents(
                case_id=case_id,
                pot=None, block=None, section=None,
                stain=None, timestamp=None, extension=ext,
            )
            errors.append(ValidationIssue(
                "invalid_structure",
                f"Case ID '{case_id}' is recognisable, but the remaining structure is "
                f"invalid. Required: {case_id}<POT>-<BLOCK>-<SECTION>-<STAIN>"
                f"[_UTC<timestamp>]{ext}",
            ))
            result.suggested_correction = f"{case_id}SA-1-1-H&E{ext}"
        else:
            errors.append(ValidationIssue(
                "unrecognized",
                "Filename does not match the Palantir slide ID format. "
                "Required: N<10-digit case ID><POT>-<BLOCK>-<SECTION>-<STAIN>"
                "[_UTC<timestamp>].<ext>  (e.g. N2024002863SA-1-1-H&E.svs)",
            ))

        result.errors = errors
        result.warnings = warnings
        return result

    @staticmethod
    def canonical_stain(stain: str) -> str:
        """Return the canonical stain name, or the input unchanged if not a known synonym."""
        return STAIN_SYNONYMS.get(stain.lower(), stain)
