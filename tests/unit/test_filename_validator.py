"""
Unit tests for the centralized filename validation engine.

Tests cover:
  - Valid pathology filenames (with and without timestamp)
  - Forbidden input: control characters, hidden unicode, double extension,
    path traversal, empty
  - Clinical safety warnings: extension mismatch, stain synonym,
    timestamp policy
  - Stain canonicalization
  - Partial / recognisable case IDs
  - Fully unrecognised names
"""
import pytest

from pathoryx_enterprise.services.recovery_sentry.filename_validator import (
    STAIN_SYNONYMS,
    FilenameValidator,
)


class TestValidPathologyFilenames:
    def test_valid_with_timestamp(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs")
        assert r.classification == "valid"
        assert r.components is not None
        assert r.components.case_id == "N2024002863"
        assert r.components.pot == "SA"
        assert r.components.stain == "H&E"
        assert r.components.timestamp == "2024-08-22T08:36:39Z"
        assert r.errors == []
        assert all(w.code != "stain_synonym" for w in r.warnings)

    def test_valid_without_timestamp_gives_warning(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E.svs")
        assert r.classification == "partially_valid"
        assert r.components is not None
        assert any(w.code == "no_timestamp" for w in r.warnings)
        assert r.errors == []

    def test_valid_ndpi(self):
        r = FilenameValidator.validate("N2024004455SA-1-1-PAS_UTC2024-09-10T14_22_00Z.ndpi")
        assert r.classification == "valid"
        assert r.components.extension == ".ndpi"

    def test_valid_tiff(self):
        r = FilenameValidator.validate("N2020000001A-2-3-CD20.tiff")
        assert r.classification == "partially_valid"
        assert r.errors == []

    def test_normalized_filename_absent_when_stain_already_canonical(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E.svs")
        assert r.normalized_filename is None


class TestForbiddenInput:
    def test_empty_string(self):
        r = FilenameValidator.validate("")
        assert r.classification == "invalid"
        assert any(e.code == "empty" for e in r.errors)

    def test_whitespace_only(self):
        r = FilenameValidator.validate("   ")
        assert r.classification == "invalid"
        assert any(e.code == "empty" for e in r.errors)

    def test_path_separator_forward_slash(self):
        r = FilenameValidator.validate("../etc/passwd.svs")
        assert r.classification == "invalid"
        assert any(e.code in ("path_traversal", "dotdot") for e in r.errors)

    def test_path_separator_backslash(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E\\evil.svs")
        assert r.classification == "invalid"
        assert any(e.code == "path_traversal" for e in r.errors)

    def test_dotdot_traversal(self):
        r = FilenameValidator.validate("..N2024002863SA-1-1-H&E.svs")
        assert r.classification == "invalid"
        assert any(e.code == "dotdot" for e in r.errors)

    def test_control_character_null_byte(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E\x00.svs")
        assert r.classification == "invalid"
        assert any(e.code == "control_chars" for e in r.errors)

    def test_control_character_tab(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E\t.svs")
        assert r.classification == "invalid"
        assert any(e.code == "control_chars" for e in r.errors)

    def test_control_character_newline(self):
        r = FilenameValidator.validate("N2024002863SA\n-1-1-H&E.svs")
        assert r.classification == "invalid"
        assert any(e.code == "control_chars" for e in r.errors)

    def test_hidden_unicode_zero_width_space(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E​.svs")
        assert r.classification == "invalid"
        assert any(e.code == "hidden_unicode" for e in r.errors)

    def test_hidden_unicode_rtl_override(self):
        # U+202E RIGHT-TO-LEFT OVERRIDE
        r = FilenameValidator.validate("N2024002863SA-1-1‮H&E.svs")
        assert r.classification == "invalid"
        assert any(e.code == "hidden_unicode" for e in r.errors)

    def test_hidden_unicode_bom(self):
        r = FilenameValidator.validate("﻿N2024002863SA-1-1-H&E.svs")
        assert r.classification == "invalid"
        assert any(e.code == "hidden_unicode" for e in r.errors)

    def test_double_extension_svs_bak(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E.svs.bak")
        # .bak not in supported list → invalid_extension first
        assert r.classification == "invalid"

    def test_double_extension_svs_svs(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E.svs.svs")
        assert r.classification == "invalid"
        assert any(e.code == "double_extension" for e in r.errors)

    def test_double_extension_ndpi_svs(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E.ndpi.svs")
        assert r.classification == "invalid"
        assert any(e.code == "double_extension" for e in r.errors)

    def test_unsupported_extension_png(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E.png")
        assert r.classification == "invalid"
        assert any(e.code == "invalid_extension" for e in r.errors)

    def test_no_extension(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E")
        assert r.classification == "invalid"
        assert any(e.code == "no_extension" for e in r.errors)


class TestStainNormalization:
    @pytest.mark.parametrize("raw, canonical", [
        ("HE", "H&E"),
        ("he", "H&E"),
        ("H-E", "H&E"),
        ("H+E", "H&E"),
        ("hematoxylin", "H&E"),
        ("PAS-D", "PAS"),
        ("pasd", "PAS"),
        ("masson", "MT"),
        ("Masson", "MT"),
        ("ZN", "Ziehl"),
        ("Ziehl-Neelsen", "Ziehl"),
        ("KI67", "KI-67"),
    ])
    def test_synonym_table(self, raw, canonical):
        assert FilenameValidator.canonical_stain(raw) == canonical

    def test_stain_synonym_warning_and_normalized_filename(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-HE.svs")
        assert any(w.code == "stain_synonym" for w in r.warnings)
        assert r.normalized_filename == "N2024002863SA-1-1-H&E.svs"

    def test_stain_synonym_preserved_in_normalized_with_timestamp(self):
        r = FilenameValidator.validate(
            "N2024002863SA-1-1-HE_UTC2024-08-22T08_36_39Z.svs"
        )
        assert r.normalized_filename == "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"

    def test_canonical_stain_no_synonym(self):
        assert FilenameValidator.canonical_stain("PAS") == "PAS"
        assert FilenameValidator.canonical_stain("H&E") == "H&E"
        assert FilenameValidator.canonical_stain("CD20") == "CD20"


class TestExtensionMismatch:
    def test_mismatch_svs_vs_ndpi(self):
        r = FilenameValidator.validate(
            "N2024002863SA-1-1-H&E.ndpi",
            original_extension=".svs",
        )
        assert any(w.code == "extension_mismatch" for w in r.warnings)

    def test_no_mismatch_when_same(self):
        r = FilenameValidator.validate(
            "N2024002863SA-1-1-H&E.svs",
            original_extension=".svs",
        )
        assert all(w.code != "extension_mismatch" for w in r.warnings)

    def test_case_insensitive_extension_match(self):
        # original ".SVS" should match ".svs"
        r = FilenameValidator.validate(
            "N2024002863SA-1-1-H&E.svs",
            original_extension=".SVS",
        )
        assert all(w.code != "extension_mismatch" for w in r.warnings)

    def test_no_mismatch_check_when_original_absent(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E.ndpi")
        assert all(w.code != "extension_mismatch" for w in r.warnings)


class TestTimestampPolicy:
    def test_no_timestamp_default_advisory(self):
        r = FilenameValidator.validate("N2024002863SA-1-1-H&E.svs")
        ts_warn = next((w for w in r.warnings if w.code == "no_timestamp"), None)
        assert ts_warn is not None
        assert "auto-extracted" in ts_warn.message.lower()

    def test_no_timestamp_strict_policy(self):
        r = FilenameValidator.validate(
            "N2024002863SA-1-1-H&E.svs",
            config_requires_timestamp=True,
        )
        ts_warn = next((w for w in r.warnings if w.code == "no_timestamp"), None)
        assert ts_warn is not None
        assert "manual review" in ts_warn.message.lower()

    def test_timestamp_present_no_warning(self):
        r = FilenameValidator.validate(
            "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs",
            config_requires_timestamp=True,
        )
        assert all(w.code != "no_timestamp" for w in r.warnings)


class TestPartialAndUnrecognised:
    def test_recognisable_case_id_bad_structure(self):
        r = FilenameValidator.validate("N2024002863BROKEN.svs")
        assert r.classification == "partially_valid"
        assert r.components is not None
        assert r.components.case_id == "N2024002863"
        assert any(e.code == "invalid_structure" for e in r.errors)
        assert r.suggested_correction is not None

    def test_completely_unrecognised(self):
        r = FilenameValidator.validate("scan_001.svs")
        assert r.classification == "invalid"
        assert any(e.code == "unrecognized" for e in r.errors)

    def test_random_file_svs(self):
        r = FilenameValidator.validate("something.svs")
        assert r.classification == "invalid"
        assert any(e.code == "unrecognized" for e in r.errors)


class TestSynonymTableCompleteness:
    def test_all_synonym_values_are_strings(self):
        for k, v in STAIN_SYNONYMS.items():
            assert isinstance(k, str) and isinstance(v, str)

    def test_synonym_keys_are_lowercase(self):
        for k in STAIN_SYNONYMS:
            assert k == k.lower(), f"synonym key '{k}' must be lowercase"
