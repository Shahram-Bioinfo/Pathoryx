#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rules_search.py — Search + rules engine for pasnet_validator.

This module is intentionally "pure-ish":
- It should NOT do Pasnet connections, Excel writing, filesystem move/rename, or SQLite writes.
- It receives already-fetched case slide infos (df_case) and returns decisions.

Rules implemented:
- AE slash/dash/underscore equivalence:
    AE1_3 == AE1-3 == AE1/3  (and similar AE3-1 vs AE3/1)
- OS/OSK suffix equivalence (matching-only):
    CD45 == CD45*OS == CD45*OSK
    HSV1 == HSV1 *OSK  (DB may contain whitespace variants)
    LMP-OSK treated as LMP (matching-only)
- Robust SlideID parsing: stain part can include '/', '-', '_', '*', etc.
- Fallback heuristics:
    If pot/block look defaulted (A/1) OR pot-block search yields zero candidates,
    widen the search case-wide for a unique stain match (optionally section-aware
    if extracted section is reliable).

Policies:
- HE means confident label stain.
- H&E means default/unknown.
  * Datamatrix:
      - OK if DB confirms HE (HE+ treated as HE), OR
      - DB has a unique non-HE stain for that slide (new rule).
  * Fallback:
      - H&E only if DB confirms HE.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_CASE_RX = re.compile(r"^(?P<lab>[A-Za-z]+)(?P<year>\d{4})(?P<case>\d{1,6})$")


def to_pasnet_case_id(case_id: str) -> Optional[str]:
    """Convert raw case-id string to Pasnet case-id format, e.g. 'E2025054283' -> 'E/2025/054283'."""
    s = str(case_id or "").strip()
    m = _CASE_RX.match(s)
    if not m:
        return None
    lab = m.group("lab")
    year = m.group("year")
    case = int(m.group("case"))
    return f"{lab}/{year}/{case:06d}"


# Slide-ID example:
# R2019001686SA-1-2-HE
# casecore = R2019001686
# pot = SA
# block = 1
# section = 2
# stain = HE (optional, may include symbols)
_SLIDE_RX = re.compile(
    r"^(?P<lab>[A-Za-z]+)(?P<year>\d{4})(?P<case>\d{1,6})"
    r"(?P<pot>[A-Za-z0-9]+)"
    r"-(?P<block>[A-Za-z0-9]+)"
    r"-(?P<section>[A-Za-z0-9]+)"
    r"(?:-(?P<stain>.+))?$"   # IMPORTANT: allow '/', '-', '_', '*', etc.
)


def parse_slide_id_parts(slide_id: str) -> Dict[str, str]:
    """
    Parse a slide-id into its components.

    Returns empty dict on failure.
    """
    s = str(slide_id or "").strip()
    m = _SLIDE_RX.match(s)
    if not m:
        return {}
    return {k: (m.group(k) or "") for k in ["lab", "year", "case", "pot", "block", "section", "stain"]}


def canonicalize_pasn_stain(stain_raw: Optional[str]) -> str:
    """
    Canonicalize DB stain string into a stable, comparison-friendly form.

    Examples:
      '3-CD45 (IHC)'       -> 'CD45'
      '2-KI-67 (IHC)'      -> 'KI-67'
      '3-HE'               -> 'HE'
      '1-HE+'              -> 'HE+'
      'A-CD20 (IHC)'       -> 'CD20'
      'B-KI-67 (IHC)'      -> 'KI-67'
      'A-3-CD45 (IHC)'     -> 'CD45'
      'HSV1 *OSK (IHC)'    -> 'HSV1 *OSK'
      'CK7 + P63 (IHC)'    -> 'CK7 + P63'

    EXTRA CLEAN-UP (DB side only):
      - Remove detail tokens that should not affect stain identity:
          OSK*, OSK, (OSK), V600E, ZB
        Examples:
          'PR  OSK*'        -> 'PR'
          'SDHB (OSK)'      -> 'SDHB'
          'BRAF V600E'      -> 'BRAF'
          'PAS ZB'          -> 'PAS'
      - Also remove a bare '*' at the end:
          'PD-L1 *'         -> 'PD-L1'
    """
    if stain_raw is None:
        return ""
    s = str(stain_raw).strip()

    # remove trailing "(IHC)" and similar tags
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()

    # remove leading letter-prefix like "A-" / "B-" / "C-" (optionally repeated)
    s = re.sub(r"^\s*[A-Za-z]\s*-\s*", "", s).strip()

    # remove leading digit prefix like "3-"
    s = re.sub(r"^\s*\d+\s*-\s*", "", s).strip()

    # sometimes DB may have BOTH: "A-3-CD45"
    s = re.sub(r"^\s*[A-Za-z]\s*-\s*", "", s).strip()
    s = re.sub(r"^\s*\d+\s*-\s*", "", s).strip()

    # --- NEW: remove unwanted detail tokens anywhere in the string -----------
    # This will catch:
    #   "PR  OSK*", "(OSK)", "SDHB (OSK)", "BRAF V600E", "PAS ZB", ...
    s = re.sub(r"\(?\s*OSK\*\s*\)?", " ", s, flags=re.IGNORECASE)   # OSK*
    s = re.sub(r"\(?\s*OSK\s*\)?", " ", s, flags=re.IGNORECASE)     # OSK or (OSK)
    s = re.sub(r"\s*V600E\s*", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*ZB\s*", " ", s, flags=re.IGNORECASE)

    # --- NEW: remove bare trailing '*' (e.g. 'PD-L1 *' -> 'PD-L1') -----------
    s = re.sub(r"\s*\*\s*$", " ", s)

    # normalize internal whitespace after token removal
    s = re.sub(r"\s+", " ", s).strip()

    return s


def he_equivalent_for_compare(x: str) -> str:
    """Map HE+ -> HE for comparison with pipeline output (pipeline yields HE)."""
    t = str(x or "").strip()
    return "HE" if t == "HE+" else t


def extracted_confidence(stain: str) -> str:
    """
    Return 'CONFIDENT' unless extracted stain is H&E (which we treat as defaulted/unknown).
    """
    return "DEFAULTED" if str(stain or "").strip() == "H&E" else "CONFIDENT"


# --- OS/OSK handling (matching-only) -----------------------------------------

# Common real-world suffix patterns:
#   "CD45*OS" / "CD45*OSK"
#   "HSV1 *OSK" (space before *)
#   "LMP-OSK"
#   "CMV OSK" (rare but seen)
_OS_SUFFIX_RX = re.compile(r"(\s*\*(OSK|OS)\s*$|\s+(OSK)\s*$|-(OSK)\s*$)", re.IGNORECASE)


def _strip_os_suffix(s: str) -> str:
    """
    Remove OS/OSK suffix patterns for matching purposes.
    We keep the base stain; OS/OSK is considered a detail suffix.
    """
    return _OS_SUFFIX_RX.sub("", str(s or "").strip()).strip()


def _add_os_variants(out: set, s: str) -> None:
    """
    Add a small set of OS/OSK variants for matching-only.

    Keeps original casing as well as upper-case forms (added elsewhere).
    """
    base = _strip_os_suffix(s)
    out.add(base)

    # add canonical starred suffix forms
    if base:
        out.add(f"{base}*OS")
        out.add(f"{base}*OSK")


# --- AE cocktail handling -----------------------------------------------------

_AE_COCKTAIL_RX = re.compile(r"^(AE\d+)[_/-](\d+)$", re.IGNORECASE)

# --- Stain alias / synonym handling -----------------------------------------
#
# This map is intentionally small and extendable. Keys can be short labels or
# alternative spellings; values are canonical display names coming from DB.
#
_STAIN_ALIAS_MAP: Dict[str, str] = {
    "ZN": "Ziehl Neelsen",
    "Ziehl-Neelsen": "Ziehl Neelsen",
    "ArcherHeme": "Archer Heme",
    "CK5": "CK5/6",
    "AE1": "AE1/3",
    "KI-67": "Ki-67",
    "t-PDGFR": "PDGFR",
}


def _apply_stain_alias(s: str) -> str:
    """
    Map shorthand/alternative labels like 'ZN' to a canonical display name such
    as 'Ziehl Neelsen' for matching.

    This is applied symmetrically to both extracted + DB stains via
    stain_variants_for_compare, so matching is done on the DB-style name.
    """
    if not s:
        return s
    key = re.sub(r"[\s\.\-]+", " ", str(s)).strip().upper()
    for k, v in _STAIN_ALIAS_MAP.items():
        k_norm = re.sub(r"[\s\.\-]+", " ", k).strip().upper()
        if key == k_norm:
            return v
    return s


def stain_variants_for_compare(stain: str) -> List[str]:
    """
    Produce a small set of equivalent variants for matching.

    Rules:
    - Trim spaces; collapse internal whitespace.
    - Apply alias normalization (ZN -> Ziehl Neelsen, etc.).
    - Consider OS/OSK suffix optional (both with and without).
    - Treat '&' and '+' as equivalent between components:
        CK7&P63 == CK7 + P63
    - Treat '_' and '/' and '-' equivalently in many contexts:
        CK5_6 == CK5/6 == CK5-6
    - For AE cocktails: AE1_3 == AE1-3 == AE1/3 (case-insensitive for prefix).
    - Add upper-case variants to stabilize matching.
    """
    s = str(stain or "").strip()
    if not s:
        return [""]

    # collapse multiple spaces to single
    s = re.sub(r"\s+", " ", s).strip()

    # alias normalization (ZN -> Ziehl Neelsen, etc)
    s = _apply_stain_alias(s)

    out: set = set()

    # base form
    out.add(s)
    _add_os_variants(out, s)

    # normalize '&' -> ' + '
    s_combo = re.sub(r"\s*&\s*", " + ", s)
    out.add(s_combo)
    _add_os_variants(out, s_combo)

    # normalize separators: '_' and '/' to '-'
    s_norm = s_combo.replace("_", "-").replace("/", "-")
    out.add(s_norm)
    _add_os_variants(out, s_norm)

    # AE cocktail equivalence (ONLY AE*, to avoid KI-67 false equivalence)
    m = _AE_COCKTAIL_RX.match(s_norm)
    if m:
        p1 = m.group(1).upper()
        p2 = m.group(2)
        for form in (f"{p1}/{p2}", f"{p1}-{p2}", f"{p1}_{p2}"):
            out.add(form)
            _add_os_variants(out, form)

    # Upper-case baseline (common in stains)
    base_for_upper = list(out)
    for v in base_for_upper:
        vu = str(v).upper()
        out.add(vu)
        _add_os_variants(out, vu)

    # If upper form is AE cocktail too, add its variants
    m2 = _AE_COCKTAIL_RX.match(s_norm.upper())
    if m2:
        p1 = m2.group(1).upper()
        p2 = m2.group(2)
        for form in (f"{p1}/{p2}", f"{p1}-{p2}", f"{p1}_{p2}"):
            out.add(form)
            _add_os_variants(out, form)

    # final cleanup
    cleaned = {x.strip() for x in out if x is not None and str(x).strip() != ""}
    return sorted(cleaned)


def stains_match(extracted: str, db_cmp: str) -> bool:
    """
    Match using variant intersection.

    Inputs should already be canonicalized as needed (e.g. HE+ -> HE).
    """
    a = set(stain_variants_for_compare(extracted))
    b = set(stain_variants_for_compare(db_cmp))
    return len(a.intersection(b)) > 0


def ensure_slideid_has_stain(slide_id: str, stain: str) -> str:
    """Ensure final SlideID string always contains the stain suffix."""
    sid = str(slide_id or "").strip()
    st = str(stain or "").strip()
    if not sid or not st:
        return sid
    if sid.endswith(f"-{st}"):
        return sid
    return f"{sid}-{st}"


def candidates_compact_format(items: List[Tuple[str, str, str]], max_n: int = 15) -> str:
    """Compact human-readable representation of candidate slides for auditing."""
    shown = items[:max_n]
    parts = [f"{sid}::{st}::sec={sec}" for sid, st, sec in shown]
    if len(items) > max_n:
        parts.append(f"(+{len(items)-max_n} more)")
    return " | ".join(parts)


def _normalize_intlike(x: Any) -> str:
    """Normalize integer-like strings (e.g. '3.0' -> '3')."""
    s = str(x or "").strip()
    if re.match(r"^\d+\.0$", s):
        return s.split(".")[0]
    return s


def _section_is_reliable(section: str) -> bool:
    """
    Section can be noisy/placeholder (e.g., 500).
    We mark it reliable only if it's a small-ish integer and not empty.
    """
    s = str(section or "").strip()
    if not s:
        return False
    if not re.match(r"^\d+$", s):
        return False
    # conservative: treat >=500 as placeholder/unsafe
    return int(s) < 500


def _is_defaulted_pot_block(pot: str, block: str) -> bool:
    """
    Detect defaulted pot/block pairs (A/1) that do not convey real location.
    """
    return (str(pot or "").strip().upper() == "A") and (str(block or "").strip() == "1")


# --- compound stain helpers --------------------------------------------------

_COMPOUND_SEP_RX = re.compile(r"\s*\+\s*")


def _split_compound_stain(s: str) -> List[str]:
    """Split a compound stain like 'CK7 + P63' into individual components."""
    base = str(s or "").strip()
    if not base:
        return []
    parts = _COMPOUND_SEP_RX.split(base)
    return [p.strip() for p in parts if p.strip()]


def _single_component_matches_compound(extracted: str, db_stain_can: str) -> bool:
    """
    Return True if extracted (single) stain matches at least one component of
    a compound DB stain.

    Handles:
      - 'CK7' vs 'CK7 + P63'
      - 'AE1' or 'AE3' vs 'AE1/3' (and AE1-3, AE1_3, AE3-1, etc.)
    """
    ex = str(extracted or "").strip()
    db = str(db_stain_can or "").strip()
    if not ex or not db:
        return False

    # 1) '+'-separated compounds (CK7 + P63, CK7+P63, ...)
    components = _split_compound_stain(db)
    if len(components) >= 2:
        for comp in components:
            if stains_match(ex, comp):
                return True

    # 2) AE cocktails (AE1/3, AE1-3, AE1_3, AE3-1, ...)
    db_norm = db.replace("_", "-").replace("/", "-").strip().upper()
    m = _AE_COCKTAIL_RX.match(db_norm)
    if m:
        p1 = m.group(1)  # e.g. AE1
        p2 = m.group(2)  # e.g. "3"
        comp1 = p1              # "AE1"
        comp2 = f"AE{p2}"       # "AE3"
        if stains_match(ex, comp1) or stains_match(ex, comp2):
            return True

    return False


# --- Decisions ---------------------------------------------------------------

def decide_datamatrix(row: Dict[str, Any], case_exists: bool, df_case: pd.DataFrame) -> Dict[str, Any]:
    """
    Decision logic for the Datamatrix path.

    UPDATED (requested):
    - Instead of exact matching the full SlideID (which may include '-stain'),
      we first match the SlideID CORE (lab+year+case+pot-block-section) ignoring stain.
    - If exactly one slide matches this core, we then proceed to stain checks
      using existing rules (H&E policy + stain variants + compound handling).

    All other behavior remains unchanged.
    """
    extracted_slide_id = str(row.get("SlideID") or "").strip()
    extracted_stain = str(row.get("Stain") or "").strip()

    logger.debug("Datamatrix decision for slide_id=%s, stain=%s", extracted_slide_id, extracted_stain)

    result: Dict[str, Any] = {
        "pasnet_case_exists": "TRUE" if case_exists else "FALSE",
        "pasnet_slide_exists": "FALSE",
        "pasnet_stain_raw": None,
        "pasnet_stain_canonical": None,
        "final_stain": extracted_stain,
        "stain_result": "NOT_CHECKED",
        "final_decision": "SUSPICIOUS" if not case_exists else "OK",
        "decision_reason": "",
        "final_slide_id": None,
        "rename_source": "pipeline",
        "candidate_match_type": "EXACT_CORE",
        "pasnet_slide_id": None,
        "details": {},
    }

    if not case_exists:
        result["final_decision"] = "SUSPICIOUS"
        result["decision_reason"] = "case_not_found_in_pasnET"
        logger.debug("Datamatrix: case not found in Pasnet")
        return result

    if "slide_id" not in df_case.columns:
        result["final_decision"] = "SUSPICIOUS"
        result["decision_reason"] = "pasnet_case_df_missing_slide_id"
        logger.debug("Datamatrix: Pasnet df missing slide_id column")
        return result

    if "staining" not in df_case.columns:
        result["final_decision"] = "SUSPICIOUS"
        result["decision_reason"] = "pasnet_case_df_missing_staining"
        logger.debug("Datamatrix: Pasnet df missing staining column")
        return result

    # --- NEW: match SlideID by CORE (ignore stain) ----------------------------
    parts = parse_slide_id_parts(extracted_slide_id)

    def _core_from_parts(p: Dict[str, str]) -> str:
        if not p:
            return ""
        if not all(p.get(k) for k in ("lab", "year", "case", "pot", "block", "section")):
            return ""
        return f"{p['lab']}{p['year']}{p['case']}{p['pot']}-{p['block']}-{p['section']}"

    extracted_core = _core_from_parts(parts)

    def _core_of_sid(sid: str) -> str:
        p = parse_slide_id_parts(sid)
        return _core_from_parts(p)

    chosen_slide_id: Optional[str] = None

    if extracted_core:
        tmp = df_case.copy()
        tmp["__core__"] = tmp["slide_id"].astype(str).map(_core_of_sid)
        core_hits = tmp[tmp["__core__"] == extracted_core]

        result["details"]["extracted_core"] = extracted_core
        result["details"]["core_hit_count"] = int(len(core_hits))

        if len(core_hits) == 1:
            chosen_slide_id = str(core_hits.iloc[0]["slide_id"])
            result["pasnet_slide_exists"] = "TRUE"
            result["pasnet_slide_id"] = chosen_slide_id
            result["rename_source"] = "database"
        elif len(core_hits) == 0:
            result["final_decision"] = "SUSPICIOUS"
            result["decision_reason"] = "slide_core_not_found_in_pasnET_case_list"
            logger.debug("Datamatrix: core not found in Pasnet case list")
            return result
        else:
            result["final_decision"] = "SUSPICIOUS"
            result["decision_reason"] = "slide_core_ambiguous_multiple_hits"
            logger.debug("Datamatrix: core ambiguous (multiple hits) in Pasnet case list")
            return result
    else:
        # If we cannot parse a core, fall back to the old strict exact behavior.
        matched_exact = df_case[df_case["slide_id"].astype(str) == extracted_slide_id].copy()
        slide_exists = not matched_exact.empty
        result["pasnet_slide_exists"] = "TRUE" if slide_exists else "FALSE"
        result["candidate_match_type"] = "EXACT"

        if not slide_exists:
            result["final_decision"] = "SUSPICIOUS"
            result["decision_reason"] = "slide_not_found_in_pasnET_case_list"
            logger.debug("Datamatrix: slide not found in Pasnet case list (exact fallback)")
            return result

        chosen_slide_id = extracted_slide_id
        result["pasnet_slide_id"] = chosen_slide_id

    # Now work with ALL records for the chosen slide_id (just in case)
    matched = df_case[df_case["slide_id"].astype(str) == str(chosen_slide_id)].copy()

    if matched.empty:
        # defensive, should not happen
        result["final_decision"] = "SUSPICIOUS"
        result["decision_reason"] = "chosen_slide_id_missing_after_core_selection"
        logger.debug("Datamatrix: chosen slide_id unexpectedly missing after core selection")
        return result

    matched["stain_can"] = matched["staining"].astype(str).map(canonicalize_pasn_stain)
    matched["stain_cmp"] = matched["stain_can"].map(he_equivalent_for_compare)

    first = matched.iloc[0]
    stain_raw = str(first.get("staining") or "")
    stain_can = str(first.get("stain_can") or "")
    db_cmp_first = he_equivalent_for_compare(stain_can)

    result["pasnet_stain_raw"] = stain_raw
    result["pasnet_stain_canonical"] = stain_can

    # --- existing stain logic (unchanged) ------------------------------------
    if extracted_stain == "H&E":
        # 1) accept if any HE/HE+ exists for this slide
        if any(sc == "HE" for sc in matched["stain_cmp"]):
            result["final_stain"] = "HE"
            result["stain_result"] = "DB_CONFIRMED_DEFAULT"
            result["final_decision"] = "OK"
            result["decision_reason"] = "extracted_H&E_but_db_confirms_HE"
            logger.debug("Datamatrix: H&E confirmed as HE by DB")
        else:
            # 2) new: if there's exactly one unique non-HE stain for this slide
            unique_cmp = set(matched["stain_cmp"])
            if len(unique_cmp) == 1:
                only_cmp = next(iter(unique_cmp))
                if only_cmp != "HE":
                    final_stain = str(first["stain_can"] or "")
                    result["final_stain"] = final_stain
                    result["stain_result"] = "DB_CONFIRMED_DEFAULT_NON_HE"
                    result["final_decision"] = "OK"
                    result["decision_reason"] = "extracted_H&E_but_db_has_unique_non_HE"
                    logger.debug("Datamatrix: H&E upgraded to unique non-HE '%s' from DB", final_stain)
                else:
                    result["final_decision"] = "SUSPICIOUS"
                    result["stain_result"] = "MISMATCH"
                    result["decision_reason"] = "extracted_H&E_db_only_HE_inconsistent"
                    logger.debug("Datamatrix: H&E inconsistent HE-only situation")
            else:
                result["final_decision"] = "SUSPICIOUS"
                result["stain_result"] = "MISMATCH"
                result["decision_reason"] = "extracted_H&E_but_db_not_HE_and_not_unique"
                logger.debug("Datamatrix: H&E but DB stain set not unique and not HE")
    else:
        if stains_match(extracted_stain, db_cmp_first):
            result["final_decision"] = "OK"
            result["stain_result"] = "MATCH"
            result["decision_reason"] = "ok"
            logger.debug("Datamatrix: non-H&E stain matched exactly")
        elif _single_component_matches_compound(extracted_stain, stain_can):
            final_stain = stain_can
            result["final_stain"] = final_stain
            result["final_decision"] = "OK"
            result["stain_result"] = "MATCH_COMPONENT_OF_COMPOUND"
            result["decision_reason"] = "extracted_single_component_of_compound_for_slideid"
            logger.debug(
                "Datamatrix: non-H&E stain '%s' matched component of compound '%s'",
                extracted_stain, stain_can
            )
        else:
            result["final_decision"] = "SUSPICIOUS"
            result["stain_result"] = "MISMATCH"
            result["decision_reason"] = "stain_mismatch"
            logger.debug(
                "Datamatrix: non-H&E stain mismatch (extracted=%s, db=%s)",
                extracted_stain, db_cmp_first
            )

    result["final_slide_id"] = ensure_slideid_has_stain(str(chosen_slide_id), str(result["final_stain"]))
    return result


def decide_fallback(row: Dict[str, Any], case_exists: bool, df_case: pd.DataFrame) -> Dict[str, Any]:
    """
    Decision logic for the Fallback path (we trust SlideID less).

    NEW BEHAVIOR:
    - Stage 0: if the core SlideID (lab+year+case+pot-block-section) matches
      exactly one slide in Pasnet, and its stain matches or contains the
      extracted stain as a component of a compound, we accept that slide
      directly (EXACT_CORE / EXACT_CORE_COMPONENT), even if pot-block search
      is ambiguous.
    - Stage 3: for non-H&E stains, after pot/block and default heuristics,
      if still no match, perform a case-wide unique-by-stain search
      (CASE_UNIQUE_BY_STAIN). If a unique slide is found for that stain, we
      also expose corrected pot/block/section in details so callers can fix
      metadata.
    """
    extracted_slide_id = str(row.get("SlideID") or "").strip()
    extracted_stain = str(row.get("Stain") or "").strip()

    logger.debug("Fallback decision for slide_id=%s, stain=%s", extracted_slide_id, extracted_stain)

    parts = parse_slide_id_parts(extracted_slide_id)
    pot = str(row.get("Pot") or parts.get("pot") or "").strip()
    block = _normalize_intlike(row.get("BlockID") or parts.get("block") or "")
    section_ex = _normalize_intlike(row.get("Section") or parts.get("section") or "")

    candidate_key = f"{pot}-{block}"
    defaulted = _is_defaulted_pot_block(pot, block)

    result: Dict[str, Any] = {
        "pasnet_case_exists": "TRUE" if case_exists else "FALSE",
        "pasnet_total_slides_in_case": int(len(df_case)) if case_exists else 0,
        "candidate_key": candidate_key,
        "candidate_count": 0,
        "candidate_match_type": "NOT_FOUND",
        "best_candidate_slide_id": None,
        "best_candidate_section": None,
        "pasnet_best_stain_raw": None,
        "pasnet_best_stain_canonical": None,
        "final_slide_id": None,
        "final_stain": None,
        "final_decision": "SUSPICIOUS" if not case_exists else "SUSPICIOUS",
        "decision_reason": "",
        "candidates_compact": "",
        "rename_source": None,
        "details": {
            "extracted_section": section_ex,
            "extracted_confidence": extracted_confidence(extracted_stain),
            "defaulted_pot_block": defaulted,
            "heuristic_used": False,
            "heuristic_scope": None,
            "heuristic_trigger": None,  # DEFAULTED_POT_BLOCK | EMPTY_POT_BLOCK | NON_UNIQUE_POT_BLOCK | ...
            # optional corrections when we discover a better match:
            "corrected_pot": None,
            "corrected_block": None,
            "corrected_section": None,
        },
    }

    if not case_exists:
        result["decision_reason"] = "case_not_found_in_pasnET"
        logger.debug("Fallback: case not found in Pasnet")
        return result

    if "slide_id" not in df_case.columns or "staining" not in df_case.columns:
        result["decision_reason"] = "pasnet_case_df_missing_columns"
        logger.debug("Fallback: Pasnet df missing required columns")
        return result

    # --- STAGE 0: exact core SlideID match (lab/year/case/pot-block-section) ---
    if parts and all(parts.get(k) for k in ("lab", "year", "case", "pot", "block", "section")):
        core = f"{parts['lab']}{parts['year']}{parts['case']}{parts['pot']}-{parts['block']}-{parts['section']}"

        def _core_of_sid(sid: str) -> str:
            p = parse_slide_id_parts(sid)
            if not p:
                return ""
            return f"{p['lab']}{p['year']}{p['case']}{p['pot']}-{p['block']}-{p['section']}"

        tmp_core = df_case.copy()
        tmp_core["core"] = tmp_core["slide_id"].astype(str).map(_core_of_sid)
        core_hits = tmp_core[tmp_core["core"] == core]

        if len(core_hits) == 1:
            best = core_hits.iloc[0]
            stain_raw = str(best.get("staining") or "")
            stain_can = canonicalize_pasn_stain(stain_raw)
            db_cmp = he_equivalent_for_compare(stain_can)

            logger.debug("Fallback: core match candidate found with stain=%s", stain_can)

            if extracted_stain == "H&E":
                if stains_match("HE", db_cmp):
                    final_stain = "HE"
                    result["final_stain"] = final_stain
                    result["final_slide_id"] = ensure_slideid_has_stain(str(best["slide_id"]), final_stain)
                    result["pasnet_best_stain_raw"] = stain_raw
                    result["pasnet_best_stain_canonical"] = stain_can
                    result["best_candidate_slide_id"] = best["slide_id"]
                    result["best_candidate_section"] = parts.get("section")
                    result["candidate_match_type"] = "EXACT_CORE"
                    result["final_decision"] = "OK"
                    result["decision_reason"] = "exact_core_slideid_match_with_HE"
                    result["rename_source"] = "database"
                    logger.debug("Fallback: core match accepted with HE")
                    return result
            else:
                if stains_match(extracted_stain, db_cmp):
                    final_stain = extracted_stain
                    result["final_stain"] = final_stain
                    result["final_slide_id"] = ensure_slideid_has_stain(str(best["slide_id"]), final_stain)
                    result["pasnet_best_stain_raw"] = stain_raw
                    result["pasnet_best_stain_canonical"] = stain_can
                    result["best_candidate_slide_id"] = best["slide_id"]
                    result["best_candidate_section"] = parts.get("section")
                    result["candidate_match_type"] = "EXACT_CORE"
                    result["final_decision"] = "OK"
                    result["decision_reason"] = "exact_core_slideid_and_stain_match"
                    result["rename_source"] = "database"
                    logger.debug("Fallback: core match accepted with direct stain match")
                    return result
                elif _single_component_matches_compound(extracted_stain, stain_can):
                    # extracted is single, DB is compound; accept full compound
                    final_stain = stain_can
                    result["final_stain"] = final_stain
                    result["final_slide_id"] = ensure_slideid_has_stain(str(best["slide_id"]), final_stain)
                    result["pasnet_best_stain_raw"] = stain_raw
                    result["pasnet_best_stain_canonical"] = stain_can
                    result["best_candidate_slide_id"] = best["slide_id"]
                    result["best_candidate_section"] = parts.get("section")
                    result["candidate_match_type"] = "EXACT_CORE_COMPONENT"
                    result["final_decision"] = "OK"
                    result["decision_reason"] = "exact_core_slideid_and_single_component_of_compound"
                    result["rename_source"] = "database"
                    logger.debug(
                        "Fallback: core match accepted as component of compound (extracted=%s, db=%s)",
                        extracted_stain, stain_can,
                    )
                    return result

    # --- STAGE 1: normal pot-block candidate search ---
    if not (pot and block):
        result["decision_reason"] = "missing_pot_or_block_for_candidate_search"
        logger.debug("Fallback: missing pot or block for candidate search")
        return result

    token = f"{pot}-{block}-"
    cand = df_case[df_case["slide_id"].astype(str).str.contains(token, na=False)].copy()
    result["candidate_count"] = int(len(cand))

    compact_items: List[Tuple[str, str, str]] = []
    for _, rr in cand.iterrows():
        sid = str(rr["slide_id"])
        stain_raw = str(rr["staining"] or "")
        stain_can = canonicalize_pasn_stain(stain_raw)
        p = parse_slide_id_parts(sid)
        sec = p.get("section", "")
        compact_items.append((sid, he_equivalent_for_compare(stain_can), sec))
    result["candidates_compact"] = candidates_compact_format(compact_items)

    def _unique_hit_from_df(df_src: pd.DataFrame, target_stain_cmp: str, scope: str) -> Optional[Dict[str, Any]]:
        """
        Return best row dict if unique match by stain variants exists; else None.

        This helper does not modify outer state; it just selects a unique match
        under the given scope, if available.
        """
        if df_src is None or df_src.empty:
            return None
        tmp = df_src.copy()
        tmp["stain_can"] = tmp["staining"].astype(str).map(canonicalize_pasn_stain).map(he_equivalent_for_compare)

        # variant match: keep rows where any variant intersects
        mask = tmp["stain_can"].apply(lambda x: stains_match(target_stain_cmp, str(x)))
        hits = tmp[mask]
        if len(hits) != 1:
            return None
        best = hits.iloc[0]
        best_sid = str(best["slide_id"])
        best_raw = str(best["staining"] or "")
        best_can = canonicalize_pasn_stain(best_raw)
        best_sec = parse_slide_id_parts(best_sid).get("section", "")
        return {
            "best_sid": best_sid,
            "best_raw": best_raw,
            "best_can": best_can,
            "best_sec": best_sec,
            "scope": scope,
        }

    # Determine target compare stain
    target_cmp = "HE" if extracted_stain == "H&E" else extracted_stain

    # 1) normal pot-block path: unique stain match
    info = _unique_hit_from_df(cand, target_stain_cmp=target_cmp, scope="POT_BLOCK")
    if info is not None:
        result["candidate_match_type"] = "UNIQUE_BY_STAIN"
        result["best_candidate_slide_id"] = info["best_sid"]
        result["best_candidate_section"] = info["best_sec"]
        result["pasnet_best_stain_raw"] = info["best_raw"]
        result["pasnet_best_stain_canonical"] = info["best_can"]
        result["final_stain"] = "HE" if extracted_stain == "H&E" else extracted_stain
        result["final_slide_id"] = ensure_slideid_has_stain(info["best_sid"], str(result["final_stain"]))
        result["final_decision"] = "OK"
        result["decision_reason"] = (
            "unique_stain_match_in_pot_block"
            if extracted_stain != "H&E"
            else "H&E_db_confirms_HE_unique_in_pot_block"
        )
        result["rename_source"] = "database"
        logger.debug("Fallback: unique stain match in pot-block (scope=POT_BLOCK)")
        return result

    # No unique match in pot-block
    if cand.empty:
        result["candidate_match_type"] = "NOT_FOUND"
        result["decision_reason"] = "no_candidates_for_pot_block"
        logger.debug("Fallback: no candidates in pot-block")
    else:
        result["candidate_match_type"] = "AMBIGUOUS" if result["candidate_count"] > 0 else "NOT_FOUND"
        result["decision_reason"] = "no_unique_stain_match_in_pot_block"
        logger.debug("Fallback: ambiguous or non-unique candidates in pot-block (count=%d)",
                     result["candidate_count"])
        if result["candidate_count"] > 1:
            result["details"]["heuristic_trigger"] = "NON_UNIQUE_POT_BLOCK"

    # 2) heuristic widening (defaulted pot/block or empty pot-block)
    widen = bool(defaulted or cand.empty)
    if widen:
        result["details"]["heuristic_used"] = True
        result["details"]["heuristic_trigger"] = (
            "DEFAULTED_POT_BLOCK" if defaulted else "EMPTY_POT_BLOCK"
        )

        # case-wide, optionally section-aware
        df_scope = df_case
        scope_name = "CASE_WIDE"
        if _section_is_reliable(section_ex):
            def _sec_of_sid(sid: str) -> str:
                return parse_slide_id_parts(sid).get("section", "")
            tmp = df_case.copy()
            tmp["sec"] = tmp["slide_id"].astype(str).map(_sec_of_sid)
            df_scope = tmp[tmp["sec"].astype(str) == str(section_ex)].copy()
            scope_name = "CASE_WIDE_SECTION"

        result["details"]["heuristic_scope"] = scope_name

        info2 = _unique_hit_from_df(df_scope, target_stain_cmp=target_cmp, scope=scope_name)
        if info2 is not None:
            result["candidate_match_type"] = "HEURISTIC_UNIQUE_BY_STAIN"
            if defaulted:
                result["candidate_key"] = f"{candidate_key} (defaulted)"
            else:
                result["candidate_key"] = f"{candidate_key} (widened)"

            result["best_candidate_slide_id"] = info2["best_sid"]
            result["best_candidate_section"] = info2["best_sec"]
            result["pasnet_best_stain_raw"] = info2["best_raw"]
            result["pasnet_best_stain_canonical"] = info2["best_can"]
            result["final_stain"] = "HE" if extracted_stain == "H&E" else extracted_stain
            result["final_slide_id"] = ensure_slideid_has_stain(info2["best_sid"], str(result["final_stain"]))
            result["final_decision"] = "OK"
            result["decision_reason"] = f"heuristic_unique_match_{scope_name.lower()}"
            result["rename_source"] = "database"
            logger.debug("Fallback: heuristic unique match at scope=%s", scope_name)
            return result

    # 3) case-wide unique-by-stain for non-H&E
    if extracted_stain and extracted_stain != "H&E":
        info3 = _unique_hit_from_df(df_case, target_stain_cmp=target_cmp, scope="CASE_WIDE_STAIN_ONLY")
        if info3 is not None:
            result["details"]["heuristic_used"] = True
            if not result["details"]["heuristic_trigger"]:
                result["details"]["heuristic_trigger"] = "NON_UNIQUE_OR_WRONG_POT_BLOCK"
            result["details"]["heuristic_scope"] = "CASE_WIDE_STAIN_ONLY"
            result["candidate_match_type"] = "CASE_UNIQUE_BY_STAIN"
            result["best_candidate_slide_id"] = info3["best_sid"]
            result["best_candidate_section"] = info3["best_sec"]
            result["pasnet_best_stain_raw"] = info3["best_raw"]
            result["pasnet_best_stain_canonical"] = info3["best_can"]
            result["final_stain"] = extracted_stain
            result["final_slide_id"] = ensure_slideid_has_stain(info3["best_sid"], extracted_stain)
            result["final_decision"] = "OK"
            result["decision_reason"] = "case_wide_unique_stain_match_non_HE"
            result["rename_source"] = "database"

            # also expose corrected pot/block/section so caller can fix metadata
            p_best = parse_slide_id_parts(info3["best_sid"])
            if p_best:
                corrected_pot = p_best.get("pot") or None
                corrected_block = p_best.get("block") or None
                corrected_section = p_best.get("section") or None
                result["details"]["corrected_pot"] = corrected_pot
                result["details"]["corrected_block"] = corrected_block
                result["details"]["corrected_section"] = corrected_section
                if corrected_pot and corrected_block:
                    result["candidate_key"] = f"{corrected_pot}-{corrected_block}"
                logger.debug(
                    "Fallback: case-wide unique stain match; corrected pot/block/section to %s/%s/%s",
                    corrected_pot, corrected_block, corrected_section,
                )
            else:
                logger.debug("Fallback: case-wide unique stain match but unable to parse best_sid for corrections")

            return result

    # final: remain suspicious
    logger.debug("Fallback: final decision remains SUSPICIOUS (reason=%s)", result["decision_reason"])
    return result
