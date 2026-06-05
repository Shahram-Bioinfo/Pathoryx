"""
Unit tests for the scanner fleet configuration system.

Covers:
  - Config loading from YAML (valid, missing, malformed)
  - Name resolution: known scanner → display name
  - Name resolution: unknown scanner → fallback to raw ID
  - Disabled scanner handling
  - Empty / edge-case inputs
  - Fleet enumeration methods
"""
import pytest
import yaml

from pathoryx_enterprise.services.dashboard.scanner_fleet import ScannerEntry, ScannerFleet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path, data) -> str:
    p = tmp_path / "scanner_fleet.yaml"
    p.write_text(yaml.dump(data))
    return str(p)


def _make_fleet(*entries: tuple) -> ScannerFleet:
    """Build a fleet from (scanner_id, display_name, enabled=True) tuples."""
    return ScannerFleet([
        ScannerEntry(scanner_id=e[0], display_name=e[1], enabled=e[2] if len(e) > 2 else True)
        for e in entries
    ])


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestScannerFleetLoad:
    def test_loads_valid_yaml(self, tmp_path):
        path = _write_yaml(tmp_path, {
            "scanners": [
                {"scanner_id": "sc-01", "display_name": "homeone",
                 "location": "lab-main", "vendor": "Leica", "enabled": True},
                {"scanner_id": "sc-02", "display_name": "resolute",
                 "location": "lab-main", "vendor": "Hamamatsu", "enabled": True},
            ]
        })
        fleet = ScannerFleet.load(path)
        assert fleet.total_count == 2
        assert fleet.enabled_count == 2

    def test_missing_file_returns_empty_fleet(self, tmp_path):
        fleet = ScannerFleet.load(str(tmp_path / "nonexistent.yaml"))
        assert fleet.total_count == 0
        assert fleet.enabled_count == 0

    def test_empty_scanners_key_returns_empty_fleet(self, tmp_path):
        path = _write_yaml(tmp_path, {"scanners": []})
        fleet = ScannerFleet.load(path)
        assert fleet.total_count == 0

    def test_null_scanners_key_returns_empty_fleet(self, tmp_path):
        path = _write_yaml(tmp_path, {"scanners": None})
        fleet = ScannerFleet.load(path)
        assert fleet.total_count == 0

    def test_missing_scanner_id_entry_is_skipped(self, tmp_path):
        path = _write_yaml(tmp_path, {
            "scanners": [
                {"scanner_id": "sc-01", "display_name": "homeone"},
                {"display_name": "no-id-here"},  # missing scanner_id — should be skipped
            ]
        })
        fleet = ScannerFleet.load(path)
        assert fleet.total_count == 1
        assert fleet.is_known("sc-01")

    def test_display_name_defaults_to_scanner_id_when_omitted(self, tmp_path):
        path = _write_yaml(tmp_path, {
            "scanners": [{"scanner_id": "sc-01"}]  # no display_name
        })
        fleet = ScannerFleet.load(path)
        assert fleet.display_name("sc-01") == "sc-01"

    def test_loads_disabled_scanner(self, tmp_path):
        path = _write_yaml(tmp_path, {
            "scanners": [
                {"scanner_id": "sc-01", "display_name": "homeone", "enabled": True},
                {"scanner_id": "sc-02", "display_name": "resolute", "enabled": False},
            ]
        })
        fleet = ScannerFleet.load(path)
        assert fleet.total_count == 2
        assert fleet.enabled_count == 1

    def test_all_scanners_from_example_config(self, tmp_path):
        """Smoke-test the actual example config format from scanner_fleet.yaml."""
        path = _write_yaml(tmp_path, {
            "scanners": [
                {"scanner_id": "scanner-01", "display_name": "homeone",
                 "location": "lab-main", "vendor": "unknown", "enabled": True},
                {"scanner_id": "scanner-02", "display_name": "resolute",
                 "location": "lab-main", "vendor": "unknown", "enabled": True},
                {"scanner_id": "scanner-03", "display_name": "stardestroyer",
                 "location": "lab-main", "vendor": "unknown", "enabled": True},
            ]
        })
        fleet = ScannerFleet.load(path)
        assert fleet.total_count == 3
        assert fleet.display_name("scanner-01") == "homeone"
        assert fleet.display_name("scanner-02") == "resolute"
        assert fleet.display_name("scanner-03") == "stardestroyer"


# ---------------------------------------------------------------------------
# Name resolution
# ---------------------------------------------------------------------------

class TestScannerNameResolution:
    @pytest.fixture
    def fleet(self):
        return _make_fleet(
            ("sc-01", "homeone"),
            ("sc-02", "resolute"),
            ("sc-03", "stardestroyer", False),
        )

    def test_known_scanner_returns_display_name(self, fleet):
        assert fleet.display_name("sc-01") == "homeone"
        assert fleet.display_name("sc-02") == "resolute"

    def test_disabled_scanner_still_resolves_display_name(self, fleet):
        assert fleet.display_name("sc-03") == "stardestroyer"

    def test_unknown_scanner_falls_back_to_raw_id(self, fleet):
        assert fleet.display_name("sc-99") == "sc-99"
        assert fleet.display_name("unknown-scanner-xyz") == "unknown-scanner-xyz"

    def test_empty_string_scanner_id_falls_back(self, fleet):
        assert fleet.display_name("") == ""

    def test_get_returns_entry_for_known_scanner(self, fleet):
        entry = fleet.get("sc-01")
        assert entry is not None
        assert entry.display_name == "homeone"

    def test_get_returns_none_for_unknown_scanner(self, fleet):
        assert fleet.get("sc-99") is None

    def test_is_known_true_for_registered_scanner(self, fleet):
        assert fleet.is_known("sc-01") is True

    def test_is_known_false_for_unregistered(self, fleet):
        assert fleet.is_known("sc-99") is False

    def test_scanner_id_preserved_in_entry(self, fleet):
        entry = fleet.get("sc-01")
        assert entry.scanner_id == "sc-01"


# ---------------------------------------------------------------------------
# Enabled / disabled filtering
# ---------------------------------------------------------------------------

class TestScannerFleetFiltering:
    @pytest.fixture
    def mixed_fleet(self):
        return _make_fleet(
            ("sc-01", "homeone",      True),
            ("sc-02", "resolute",     True),
            ("sc-03", "stardestroyer", False),
        )

    def test_enabled_excludes_disabled_scanners(self, mixed_fleet):
        enabled_ids = {e.scanner_id for e in mixed_fleet.enabled()}
        assert "sc-01" in enabled_ids
        assert "sc-02" in enabled_ids
        assert "sc-03" not in enabled_ids

    def test_all_includes_disabled_scanners(self, mixed_fleet):
        all_ids = {e.scanner_id for e in mixed_fleet.all()}
        assert "sc-03" in all_ids

    def test_enabled_count_reflects_only_enabled(self, mixed_fleet):
        assert mixed_fleet.enabled_count == 2

    def test_total_count_includes_disabled(self, mixed_fleet):
        assert mixed_fleet.total_count == 3

    def test_empty_fleet_returns_empty_lists(self):
        fleet = ScannerFleet([])
        assert fleet.enabled() == []
        assert fleet.all() == []
        assert fleet.enabled_count == 0
        assert fleet.total_count == 0


# ---------------------------------------------------------------------------
# ScannerEntry dataclass
# ---------------------------------------------------------------------------

class TestScannerEntry:
    def test_defaults(self):
        entry = ScannerEntry(scanner_id="sc-01", display_name="homeone")
        assert entry.location == ""
        assert entry.vendor == "unknown"
        assert entry.enabled is True

    def test_frozen_immutable(self):
        entry = ScannerEntry(scanner_id="sc-01", display_name="homeone")
        with pytest.raises((AttributeError, TypeError)):
            entry.display_name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Fleet with no config (production fallback)
# ---------------------------------------------------------------------------

class TestEmptyFleetFallback:
    def test_unknown_id_falls_back_in_empty_fleet(self):
        fleet = ScannerFleet([])
        assert fleet.display_name("scanner-01") == "scanner-01"
        assert fleet.display_name("ANY-id") == "ANY-id"

    def test_load_default_returns_fleet_without_raising(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SCANNER_FLEET_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)  # CWD has no configs/ directory
        fleet = ScannerFleet.load_default()
        assert isinstance(fleet, ScannerFleet)
        assert fleet.total_count == 0
