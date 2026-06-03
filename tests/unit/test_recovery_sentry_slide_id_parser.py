"""
Unit tests for RecoverySentry slide ID parser.

Tests cover:
  - Valid SlideID with timestamp (Case 1)
  - Valid SlideID without timestamp (Case 2)
  - Invalid filename patterns → None
  - Timestamp format conversion
  - Final filename construction
"""
import pytest

from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import (
    ParsedSlideID,
    build_final_filename,
    iso_z_to_filename_ts,
    parse_slide_id,
)


class TestParseSlideId:
    def test_valid_with_timestamp(self):
        result = parse_slide_id("N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs")
        assert result is not None
        assert result.case_id == "N2024002863"
        assert result.pot == "SA"
        assert result.block == "1"
        assert result.section == "1"
        assert result.stain == "H&E"
        assert result.timestamp_tag == "UTC2024-08-22T08_36_39Z"
        assert result.timestamp_iso_z == "2024-08-22T08:36:39Z"
        assert result.extension == ".svs"
        assert result.has_timestamp is True
        assert result.final_filename == "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"

    def test_valid_without_timestamp(self):
        result = parse_slide_id("N2024002863SA-1-1-H&E.svs")
        assert result is not None
        assert result.case_id == "N2024002863"
        assert result.stain == "H&E"
        assert result.timestamp_tag is None
        assert result.timestamp_iso_z is None
        assert result.has_timestamp is False
        assert result.slide_id_base == "N2024002863SA-1-1-H&E"
        assert result.final_filename == "N2024002863SA-1-1-H&E.svs"

    def test_valid_pas_stain_ndpi(self):
        result = parse_slide_id("N2024004455SA-1-1-PAS_UTC2024-09-10T14_22_00Z.ndpi")
        assert result is not None
        assert result.case_id == "N2024004455"
        assert result.stain == "PAS"
        assert result.extension == ".ndpi"

    def test_valid_case_sensitivity(self):
        # CaseID must start with uppercase N
        result = parse_slide_id("N2024002863A-2-3-HE.svs")
        assert result is not None
        assert result.pot == "A"
        assert result.block == "2"
        assert result.section == "3"
        assert result.stain == "HE"

    def test_invalid_no_case_id(self):
        assert parse_slide_id("54564564.svs") is None

    def test_invalid_wrong_format(self):
        assert parse_slide_id("random_file.svs") is None

    def test_invalid_unsupported_extension(self):
        assert parse_slide_id("N2024002863SA-1-1-H&E.png") is None

    def test_invalid_empty_string(self):
        assert parse_slide_id("") is None

    def test_invalid_missing_stain(self):
        assert parse_slide_id("N2024002863SA-1-1.svs") is None

    def test_invalid_lowercase_n(self):
        # CaseID requires uppercase N
        assert parse_slide_id("n2024002863SA-1-1-HE.svs") is None

    def test_valid_mrxs_extension(self):
        result = parse_slide_id("N2024001234AB-1-2-PAS.mrxs")
        assert result is not None
        assert result.extension == ".mrxs"

    def test_valid_tiff_extension(self):
        result = parse_slide_id("N2024001234SA-1-1-H&E.tiff")
        assert result is not None
        assert result.extension == ".tiff"


class TestIsoZToFilenameTs:
    def test_conversion(self):
        assert iso_z_to_filename_ts("2024-08-22T08:36:39Z") == "UTC2024-08-22T08_36_39Z"

    def test_midnight(self):
        assert iso_z_to_filename_ts("2024-01-01T00:00:00Z") == "UTC2024-01-01T00_00_00Z"

    def test_end_of_day(self):
        assert iso_z_to_filename_ts("2024-12-31T23:59:59Z") == "UTC2024-12-31T23_59_59Z"


class TestBuildFinalFilename:
    def test_already_has_timestamp(self):
        parsed = parse_slide_id("N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs")
        assert parsed is not None
        result = build_final_filename(parsed)
        assert result == "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"

    def test_adds_timestamp_from_iso_z(self):
        parsed = parse_slide_id("N2024002863SA-1-1-H&E.svs")
        assert parsed is not None
        result = build_final_filename(parsed, iso_z="2024-08-22T08:36:39Z")
        assert result == "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"

    def test_no_timestamp_no_iso_z(self):
        parsed = parse_slide_id("N2024002863SA-1-1-H&E.svs")
        assert parsed is not None
        result = build_final_filename(parsed)
        assert result == "N2024002863SA-1-1-H&E.svs"
