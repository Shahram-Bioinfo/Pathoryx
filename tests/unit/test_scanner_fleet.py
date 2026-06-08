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

    def test_loads_real_fleet_config(self, tmp_path):
        """Smoke-test the real scanner_fleet.yaml format with serial numbers and aliases."""
        path = _write_yaml(tmp_path, {
            "scanners": [
                {"scanner_id": "M40010", "serial_number": "M40010",
                 "display_name": "Resolute", "location": "lab-main",
                 "vendor": "Leica Biosystems", "model": "Aperio GT450",
                 "aliases": ["scanner-02"], "enabled": True},
                {"scanner_id": "M40023", "serial_number": "M40023",
                 "display_name": "HomeOne", "location": "lab-secondary",
                 "vendor": "Leica Biosystems", "model": "Aperio GT450",
                 "aliases": ["scanner-01"], "enabled": True},
                {"scanner_id": "SS12620R", "serial_number": "SS12620R",
                 "display_name": "StarDestroyer Devastator", "location": "lab-main",
                 "vendor": "Leica Biosystems", "model": "Aperio GT450 RUO",
                 "aliases": ["scanner-03"], "enabled": True},
            ]
        })
        fleet = ScannerFleet.load(path)
        assert fleet.total_count == 3
        assert fleet.display_name("M40010") == "Resolute"
        assert fleet.display_name("M40023") == "HomeOne"
        assert fleet.display_name("SS12620R") == "StarDestroyer Devastator"
        # Verify metadata fields
        entry = fleet.get("M40010")
        assert entry is not None
        assert entry.serial_number == "M40010"
        assert entry.model == "Aperio GT450"
        assert entry.vendor == "Leica Biosystems"
        assert entry.aliases == ("scanner-02",)

    def test_empty_aliases_list_loads_cleanly(self, tmp_path):
        path = _write_yaml(tmp_path, {
            "scanners": [
                {"scanner_id": "M40015", "display_name": "Avenger",
                 "aliases": [], "enabled": True},
            ]
        })
        fleet = ScannerFleet.load(path)
        entry = fleet.get("M40015")
        assert entry is not None
        assert entry.aliases == ()


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
# Alias resolution
# ---------------------------------------------------------------------------

class TestScannerAliasResolution:
    @pytest.fixture
    def fleet_with_aliases(self):
        return ScannerFleet([
            ScannerEntry(
                scanner_id="M40010", display_name="Resolute",
                serial_number="M40010", model="Aperio GT450",
                vendor="Leica Biosystems", aliases=("scanner-02",),
            ),
            ScannerEntry(
                scanner_id="M40023", display_name="HomeOne",
                serial_number="M40023", model="Aperio GT450",
                vendor="Leica Biosystems", aliases=("scanner-01",),
            ),
            ScannerEntry(
                scanner_id="SS12620R", display_name="StarDestroyer Devastator",
                serial_number="SS12620R", model="Aperio GT450 RUO",
                vendor="Leica Biosystems", aliases=("scanner-03",),
            ),
        ])

    def test_primary_id_resolves(self, fleet_with_aliases):
        assert fleet_with_aliases.display_name("M40010") == "Resolute"
        assert fleet_with_aliases.display_name("M40023") == "HomeOne"
        assert fleet_with_aliases.display_name("SS12620R") == "StarDestroyer Devastator"

    def test_alias_resolves_to_same_display_name(self, fleet_with_aliases):
        assert fleet_with_aliases.display_name("scanner-02") == "Resolute"
        assert fleet_with_aliases.display_name("scanner-01") == "HomeOne"
        assert fleet_with_aliases.display_name("scanner-03") == "StarDestroyer Devastator"

    def test_alias_get_returns_canonical_entry(self, fleet_with_aliases):
        entry = fleet_with_aliases.get("scanner-01")
        assert entry is not None
        assert entry.scanner_id == "M40023"
        assert entry.display_name == "HomeOne"

    def test_is_known_true_for_alias(self, fleet_with_aliases):
        assert fleet_with_aliases.is_known("scanner-01") is True
        assert fleet_with_aliases.is_known("scanner-02") is True

    def test_alias_does_not_inflate_total_count(self, fleet_with_aliases):
        assert fleet_with_aliases.total_count == 3

    def test_all_returns_canonical_entries_only(self, fleet_with_aliases):
        ids = [e.scanner_id for e in fleet_with_aliases.all()]
        assert ids == ["M40010", "M40023", "SS12620R"]

    def test_unknown_alias_falls_back_to_raw_id(self, fleet_with_aliases):
        assert fleet_with_aliases.display_name("scanner-99") == "scanner-99"

    def test_multiple_aliases_all_resolve(self):
        fleet = ScannerFleet([
            ScannerEntry(
                scanner_id="M40015", display_name="Avenger",
                aliases=("old-id-1", "old-id-2"),
            )
        ])
        assert fleet.display_name("old-id-1") == "Avenger"
        assert fleet.display_name("old-id-2") == "Avenger"
        assert fleet.total_count == 1


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
        assert entry.model == ""
        assert entry.serial_number == ""
        assert entry.aliases == ()
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
