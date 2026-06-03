"""Filename-to-metadata regex extraction for IDS7 DICOM tag injection.

Ported from tool_WSIDicomizer/utils/metaextraction_utils.py.
Pure Python stdlib — no external dependencies.
"""
from __future__ import annotations

import re


class MetadataExtractionException(Exception):
    pass


def match_metadict_from_string(pattern: str, in_string: str) -> dict | None:
    """Extract metadata dict from a string or filename using a named-group regex."""
    if "." in in_string:
        in_string = ".".join(in_string.split(".")[:-1])
    match = re.search(pattern, in_string)
    return match.groupdict() if match else None


def construct_string_from_metadict(format_string: str, metadata: dict) -> str:
    """Build a string from a metadata dict using a format string like {case_id}S{specimen}."""
    return format_string.format(**metadata)


def match_reconstruct_metadict_from_string(
    in_string: str,
    match_construct_patterns: dict,
) -> dict:
    """
    Extract and reconstruct a metadata dict from a filename using configured patterns.

    match_construct_patterns format::

        {
          "pattern_name": {
            "match": "<regex with named groups>",
            "zerofill": {"field_name": fill_length, ...},   # optional
            "default":  {"field_name": "default_value", ...}, # optional
            "construct": {
              "output_field": ["format_string1", "format_string2", ...],
              ...
            }
          },
          ...
        }

    Raises MetadataExtractionException if no pattern matches.
    """
    if not match_construct_patterns:
        raise MetadataExtractionException(
            f"No match_construct_patterns configured — cannot extract metadata from '{in_string}'"
        )

    out_metadata_dict: dict = {}
    for pattern_name, pattern_cfg in match_construct_patterns.items():
        in_metadata_dict = match_metadict_from_string(pattern_cfg["match"], in_string)
        if not in_metadata_dict:
            continue

        # Apply zero-padding
        if pattern_cfg.get("zerofill"):
            for tag, fill_len in pattern_cfg["zerofill"].items():
                val = in_metadata_dict.get(tag)
                if val and len(val) < fill_len:
                    in_metadata_dict[tag] = val.zfill(fill_len)

        # Apply defaults for missing/None values
        if pattern_cfg.get("default"):
            for key, default_val in pattern_cfg["default"].items():
                if not in_metadata_dict.get(key):
                    in_metadata_dict[key] = default_val

        # Construct output fields
        for tag_name, construction_patterns in pattern_cfg["construct"].items():
            out_metadata_dict[tag_name] = None
            for fmt in construction_patterns:
                try:
                    value = construct_string_from_metadict(fmt, in_metadata_dict)
                    if value and "None" not in value:
                        out_metadata_dict[tag_name] = value
                        break
                except (KeyError, ValueError):
                    continue
            if not out_metadata_dict[tag_name]:
                raise MetadataExtractionException(
                    f"Could not extract '{tag_name}' from '{in_string}' "
                    f"using pattern '{pattern_name}'. "
                    f"in_metadata_dict: {in_metadata_dict}"
                )
        break

    if not out_metadata_dict:
        raise MetadataExtractionException(
            f"No pattern matched '{in_string}' in match_construct_patterns."
        )

    return out_metadata_dict
