"""
Phase 4.8 — Routing Policy Engine unit tests.

Stage 1 contract:
  - dry_run is always True
  - No real destination is changed
  - Engine computes decisions for audit/preview
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, time
from unittest.mock import MagicMock, patch

try:
    import zoneinfo
except ImportError:
    from backports import zoneinfo  # type: ignore

from pathoryx_enterprise.services.routing.engine import (
    RoutingPolicyEngine,
    RoutingResult,
    REASON_COLOR_DOT,
    REASON_FALLBACK,
    REASON_MANUAL_OVERRIDE,
    REASON_MODE_DEFAULT,
    REASON_SCANNER_POLICY,
)

# ── Shared config fixture ────────────────────────────────────────────────────

SAMPLE_CONFIG = {
    "timezone": "Europe/Copenhagen",
    "default_mode": "clinical_day",
    "dry_run": True,
    "fallback_destination": "clinical_pacs",
    "modes": {
        "clinical_day": {
            "active": {"start": "08:00", "end": "16:00"},
            "profile": "clinical",
            "default_destination": "clinical_pacs",
            "scanner_destinations": {
                "NPAPERIO1": {"destination": "clinical_pacs"},
                "RESOLUTE": {"destination": "clinical_pacs"},
            },
        },
        "research_night": {
            "active": {"start": "16:00", "end": "08:00"},
            "profile": "research",
            "default_destination": "research_storage",
            "scanner_destinations": {
                "NPAPERIO1": {"destination": "research_storage_A"},
                "RESOLUTE": {"destination": "research_storage_B"},
                "HOMEONE": {"destination": "research_storage_C"},
            },
        },
    },
    "color_dot_rules": {
        "red": {"destination": "urgent_research"},
        "blue": {"destination": "research_project_A"},
        "green": {"destination": "research_project_B"},
        "yellow": {"destination": "clinical_special"},
    },
}

CPN_TZ = zoneinfo.ZoneInfo("Europe/Copenhagen")


def _at(hour: int, minute: int = 0) -> datetime:
    """Return a UTC datetime corresponding to the given CPN local hour."""
    import dateutil.tz as dtz  # type: ignore[import]
    local = datetime(2026, 6, 10, hour, minute,
                     tzinfo=zoneinfo.ZoneInfo("Europe/Copenhagen"))
    return local.astimezone(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# TestModeDetection
# ─────────────────────────────────────────────────────────────────────────────

class TestModeDetection:
    def setup_method(self) -> None:
        self.engine = RoutingPolicyEngine(SAMPLE_CONFIG)

    def test_clinical_day_at_noon(self) -> None:
        mode = self.engine.get_active_mode(_at(12))
        assert mode is not None
        assert mode.name == "clinical_day"

    def test_clinical_day_at_start_boundary(self) -> None:
        mode = self.engine.get_active_mode(_at(8))
        assert mode is not None
        assert mode.name == "clinical_day"

    def test_research_night_at_17(self) -> None:
        mode = self.engine.get_active_mode(_at(17))
        assert mode is not None
        assert mode.name == "research_night"

    def test_research_night_overnight_at_03(self) -> None:
        mode = self.engine.get_active_mode(_at(3))
        assert mode is not None
        assert mode.name == "research_night"

    def test_research_night_at_end_boundary_excluded(self) -> None:
        # 08:00 exactly → clinical_day starts
        mode = self.engine.get_active_mode(_at(8, 0))
        assert mode is not None
        assert mode.name == "clinical_day"

    def test_mode_profile_clinical(self) -> None:
        mode = self.engine.get_active_mode(_at(10))
        assert mode is not None
        assert mode.profile == "clinical"

    def test_mode_profile_research(self) -> None:
        mode = self.engine.get_active_mode(_at(20))
        assert mode is not None
        assert mode.profile == "research"


# ─────────────────────────────────────────────────────────────────────────────
# TestScannerRouting
# ─────────────────────────────────────────────────────────────────────────────

class TestScannerRouting:
    def setup_method(self) -> None:
        self.engine = RoutingPolicyEngine(SAMPLE_CONFIG)

    def test_homeone_routes_to_research_c_at_night(self) -> None:
        result = self.engine.get_routing_decision(
            scanner_id="HOMEONE", now=_at(20)
        )
        assert result.destination == "research_storage_C"
        assert REASON_SCANNER_POLICY in result.routing_reason
        assert result.dry_run is True

    def test_resolute_routes_to_research_b_at_night(self) -> None:
        result = self.engine.get_routing_decision(
            scanner_id="RESOLUTE", now=_at(19)
        )
        assert result.destination == "research_storage_B"

    def test_npaperio1_routes_to_clinical_pacs_at_day(self) -> None:
        result = self.engine.get_routing_decision(
            scanner_id="NPAPERIO1", now=_at(12)
        )
        assert result.destination == "clinical_pacs"

    def test_unknown_scanner_uses_mode_default(self) -> None:
        result = self.engine.get_routing_decision(
            scanner_id="UNKNOWN_SCANNER", now=_at(12)
        )
        assert result.destination == "clinical_pacs"
        assert REASON_MODE_DEFAULT in result.routing_reason

    def test_unknown_scanner_at_night_uses_mode_default(self) -> None:
        result = self.engine.get_routing_decision(
            scanner_id="UNKNOWN_SCANNER", now=_at(20)
        )
        assert result.destination == "research_storage"
        assert REASON_MODE_DEFAULT in result.routing_reason

    def test_no_scanner_id_uses_mode_default(self) -> None:
        result = self.engine.get_routing_decision(scanner_id=None, now=_at(12))
        assert result.destination == "clinical_pacs"


# ─────────────────────────────────────────────────────────────────────────────
# TestColorDotRouting
# ─────────────────────────────────────────────────────────────────────────────

class TestColorDotRouting:
    def setup_method(self) -> None:
        self.engine = RoutingPolicyEngine(SAMPLE_CONFIG)

    def test_red_dot_routes_to_urgent_research(self) -> None:
        result = self.engine.get_routing_decision(
            scanner_id="NPAPERIO1", color_dot="red", now=_at(12)
        )
        assert result.destination == "urgent_research"
        assert REASON_COLOR_DOT in result.routing_reason
        assert result.color_dot == "red"

    def test_blue_dot_overrides_scanner_routing(self) -> None:
        # Even if scanner has a specific policy, color dot wins
        result = self.engine.get_routing_decision(
            scanner_id="HOMEONE", color_dot="blue", now=_at(20)
        )
        assert result.destination == "research_project_A"

    def test_color_dot_case_insensitive(self) -> None:
        result = self.engine.get_routing_decision(color_dot="RED", now=_at(10))
        assert result.destination == "urgent_research"

    def test_unknown_color_dot_falls_through_to_scanner(self) -> None:
        result = self.engine.get_routing_decision(
            scanner_id="RESOLUTE", color_dot="purple", now=_at(20)
        )
        # purple not in rules → falls through to scanner policy
        assert result.destination == "research_storage_B"
        assert REASON_SCANNER_POLICY in result.routing_reason

    def test_yellow_dot_to_clinical_special(self) -> None:
        result = self.engine.get_routing_decision(color_dot="yellow", now=_at(12))
        assert result.destination == "clinical_special"


# ─────────────────────────────────────────────────────────────────────────────
# TestOverridePriority
# ─────────────────────────────────────────────────────────────────────────────

class TestOverridePriority:
    def setup_method(self) -> None:
        self.engine = RoutingPolicyEngine(SAMPLE_CONFIG)

    def _scanner_override(self, scanner_id: str, destination: str) -> dict:
        return {
            "id": 1,
            "is_active": True,
            "target_type": "scanner",
            "target_value": scanner_id,
            "destination": destination,
            "expires_at": None,
        }

    def _file_override(self, file_id: str, destination: str) -> dict:
        return {
            "id": 2,
            "is_active": True,
            "target_type": "file",
            "target_value": file_id,
            "destination": destination,
            "expires_at": None,
        }

    def test_scanner_override_beats_config_policy(self) -> None:
        overrides = [self._scanner_override("HOMEONE", "override_dest")]
        result = self.engine.get_routing_decision(
            scanner_id="HOMEONE", now=_at(20), overrides=overrides
        )
        assert result.destination == "override_dest"
        assert result.routing_reason == REASON_MANUAL_OVERRIDE
        assert result.override_id == 1

    def test_scanner_override_beats_color_dot(self) -> None:
        overrides = [self._scanner_override("NPAPERIO1", "forced_dest")]
        result = self.engine.get_routing_decision(
            scanner_id="NPAPERIO1", color_dot="red",
            now=_at(12), overrides=overrides
        )
        assert result.destination == "forced_dest"
        assert result.routing_reason == REASON_MANUAL_OVERRIDE

    def test_file_override_is_applied(self) -> None:
        overrides = [self._file_override("artifact-uuid-123", "file_dest")]
        result = self.engine.get_routing_decision(
            scanner_id="HOMEONE", file_id="artifact-uuid-123",
            now=_at(20), overrides=overrides
        )
        assert result.destination == "file_dest"
        assert result.routing_reason == REASON_MANUAL_OVERRIDE

    def test_inactive_override_is_ignored(self) -> None:
        overrides = [{
            "id": 3, "is_active": False,
            "target_type": "scanner", "target_value": "HOMEONE",
            "destination": "should_not_be_used", "expires_at": None,
        }]
        result = self.engine.get_routing_decision(
            scanner_id="HOMEONE", now=_at(20), overrides=overrides
        )
        assert result.destination != "should_not_be_used"
        assert result.routing_reason != REASON_MANUAL_OVERRIDE

    def test_override_for_other_scanner_does_not_apply(self) -> None:
        overrides = [self._scanner_override("RESOLUTE", "resolute_dest")]
        result = self.engine.get_routing_decision(
            scanner_id="HOMEONE", now=_at(20), overrides=overrides
        )
        assert result.destination != "resolute_dest"

    def test_empty_overrides_list(self) -> None:
        result = self.engine.get_routing_decision(
            scanner_id="HOMEONE", now=_at(20), overrides=[]
        )
        assert result.destination == "research_storage_C"


# ─────────────────────────────────────────────────────────────────────────────
# TestFallback
# ─────────────────────────────────────────────────────────────────────────────

class TestFallback:
    def test_no_policy_returns_fallback(self) -> None:
        cfg = {
            "fallback_destination": "global_fallback",
            "dry_run": True,
            "modes": {},
        }
        engine = RoutingPolicyEngine(cfg)
        result = engine.get_routing_decision(scanner_id="ANY")
        assert result.destination == "global_fallback"
        assert result.routing_reason == REASON_FALLBACK

    def test_no_fallback_returns_unknown(self) -> None:
        cfg = {"dry_run": True, "modes": {}}
        engine = RoutingPolicyEngine(cfg)
        result = engine.get_routing_decision(scanner_id="ANY")
        assert result.destination == "unknown"

    def test_dry_run_always_true_stage1(self) -> None:
        engine = RoutingPolicyEngine(SAMPLE_CONFIG)
        result = engine.get_routing_decision(now=_at(10))
        assert result.dry_run is True


# ─────────────────────────────────────────────────────────────────────────────
# TestValidation
# ─────────────────────────────────────────────────────────────────────────────

class TestValidation:
    def test_valid_config_no_errors(self) -> None:
        engine = RoutingPolicyEngine(SAMPLE_CONFIG)
        issues = engine.validate()
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []

    def test_no_modes_is_error(self) -> None:
        cfg = {**SAMPLE_CONFIG, "modes": {}}
        engine = RoutingPolicyEngine(cfg)
        issues = engine.validate()
        assert any(i.severity == "error" and "modes" in i.message.lower() for i in issues)

    def test_missing_fallback_is_warning(self) -> None:
        cfg = {k: v for k, v in SAMPLE_CONFIG.items() if k != "fallback_destination"}
        engine = RoutingPolicyEngine(cfg)
        issues = engine.validate()
        assert any(i.severity == "warning" and "fallback" in i.message.lower() for i in issues)

    def test_mode_missing_default_destination_is_error(self) -> None:
        import copy
        cfg = copy.deepcopy(SAMPLE_CONFIG)
        del cfg["modes"]["clinical_day"]["default_destination"]
        engine = RoutingPolicyEngine(cfg)
        issues = engine.validate()
        assert any(
            i.severity == "error" and "clinical_day" in i.message
            for i in issues
        )


# ─────────────────────────────────────────────────────────────────────────────
# TestOvernightSchedules
# ─────────────────────────────────────────────────────────────────────────────

class TestOvernightSchedules:
    def setup_method(self) -> None:
        self.engine = RoutingPolicyEngine(SAMPLE_CONFIG)

    def test_midnight_is_research_night(self) -> None:
        mode = self.engine.get_active_mode(_at(0))
        assert mode is not None
        assert mode.name == "research_night"

    def test_0159_is_research_night(self) -> None:
        mode = self.engine.get_active_mode(_at(1, 59))
        assert mode is not None
        assert mode.name == "research_night"

    def test_0759_is_research_night(self) -> None:
        mode = self.engine.get_active_mode(_at(7, 59))
        assert mode is not None
        assert mode.name == "research_night"

    def test_1559_is_clinical_day(self) -> None:
        mode = self.engine.get_active_mode(_at(15, 59))
        assert mode is not None
        assert mode.name == "clinical_day"

    def test_1600_is_research_night(self) -> None:
        mode = self.engine.get_active_mode(_at(16, 0))
        assert mode is not None
        assert mode.name == "research_night"


# ─────────────────────────────────────────────────────────────────────────────
# TestStatusSummary
# ─────────────────────────────────────────────────────────────────────────────

class TestStatusSummary:
    def setup_method(self) -> None:
        self.engine = RoutingPolicyEngine(SAMPLE_CONFIG)

    def test_summary_contains_active_mode(self) -> None:
        summary = self.engine.get_status_summary(_at(12))
        assert summary["active_mode"] == "clinical_day"

    def test_summary_dry_run_always_true(self) -> None:
        summary = self.engine.get_status_summary(_at(12))
        assert summary["dry_run"] is True

    def test_summary_has_modes_list(self) -> None:
        summary = self.engine.get_status_summary(_at(12))
        assert len(summary["modes"]) == 2

    def test_summary_has_color_rules(self) -> None:
        summary = self.engine.get_status_summary(_at(12))
        assert len(summary["color_dot_rules"]) == 4

    def test_summary_marks_only_one_mode_active(self) -> None:
        summary = self.engine.get_status_summary(_at(12))
        active_modes = [m for m in summary["modes"] if m["is_active"]]
        assert len(active_modes) == 1
        assert active_modes[0]["name"] == "clinical_day"

    def test_summary_as_of_is_string(self) -> None:
        summary = self.engine.get_status_summary(_at(12))
        assert isinstance(summary["as_of"], str)


# ─────────────────────────────────────────────────────────────────────────────
# TestUnknownTimezone
# ─────────────────────────────────────────────────────────────────────────────

class TestUnknownTimezone:
    def test_fallback_to_utc_on_bad_timezone(self) -> None:
        cfg = {**SAMPLE_CONFIG, "timezone": "Not/AReal_Timezone"}
        engine = RoutingPolicyEngine(cfg)
        # Should not raise; should fall back to UTC
        result = engine.get_routing_decision(scanner_id="HOMEONE")
        assert result.destination is not None


# ─────────────────────────────────────────────────────────────────────────────
# TestDashboardAPI  (mocked DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardAPI:
    """HTTP-level tests for the /dashboard/api/routing/* endpoints."""

    @pytest.fixture(scope="class")
    def app(self):
        from pathoryx_enterprise.services.dashboard.app import create_app
        return create_app()

    @pytest.fixture(scope="class")
    def mock_db(self):
        return MagicMock()

    @pytest.fixture(scope="class")
    def client(self, app, mock_db):
        from fastapi.testclient import TestClient
        from pathoryx_enterprise.services.dashboard.app import get_db
        app.dependency_overrides[get_db] = lambda: mock_db
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
        app.dependency_overrides.clear()

    def test_routing_status_200(self, client) -> None:
        resp = client.get("/dashboard/api/routing/status")
        assert resp.status_code == 200

    def test_routing_status_has_dry_run(self, client) -> None:
        resp = client.get("/dashboard/api/routing/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "dry_run" in body
        assert body["dry_run"] is True

    def test_routing_preview_200(self, client, mock_db) -> None:
        # Mock the DB to return empty slide list
        mock_db.execute.return_value.mappings.return_value.all.return_value = []
        resp = client.get("/dashboard/api/routing/preview")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "dry_run" in body

    def test_routing_overrides_200(self, client, mock_db) -> None:
        mock_db.execute.return_value.mappings.return_value.all.return_value = []
        mock_db.execute.return_value.fetchall.return_value = []
        resp = client.get("/dashboard/api/routing/overrides")
        assert resp.status_code == 200

    def test_routing_decisions_200(self, client, mock_db) -> None:
        mock_db.execute.return_value.mappings.return_value.all.return_value = []
        mock_db.execute.return_value.mappings.return_value.one.return_value = {
            "total": 0, "dry_run_count": 0, "override_count": 0,
            "unique_scanners": 0, "unique_destinations": 0,
            "unique_modes": 0, "last_decision_at": None,
        }
        resp = client.get("/dashboard/api/routing/decisions")
        assert resp.status_code == 200

    def test_create_override_invalid_target_type(self, client) -> None:
        resp = client.post("/dashboard/api/routing/override", json={
            "target_type": "invalid_type",
            "target_value": "HOMEONE",
            "destination": "research_storage_C",
        })
        assert resp.status_code == 422

    def test_delete_override_not_found(self, client, mock_db) -> None:
        mock_db.execute.return_value.fetchone.return_value = None
        mock_db.commit.return_value = None
        resp = client.delete("/dashboard/api/routing/override/99999")
        assert resp.status_code == 404

    def test_routing_preview_query_param_limit(self, client, mock_db) -> None:
        mock_db.execute.return_value.mappings.return_value.all.return_value = []
        resp = client.get("/dashboard/api/routing/preview?limit=50")
        assert resp.status_code == 200

    def test_routing_preview_limit_too_large(self, client) -> None:
        resp = client.get("/dashboard/api/routing/preview?limit=9999")
        assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# TestConflictDetection
# ─────────────────────────────────────────────────────────────────────────────

class TestConflictDetection:
    def test_overlapping_non_overnight_modes_are_warned(self) -> None:
        cfg = {
            "fallback_destination": "x",
            "dry_run": True,
            "modes": {
                "mode_a": {
                    "active": {"start": "08:00", "end": "14:00"},
                    "profile": "a",
                    "default_destination": "dest_a",
                },
                "mode_b": {
                    "active": {"start": "12:00", "end": "18:00"},
                    "profile": "b",
                    "default_destination": "dest_b",
                },
            },
        }
        engine = RoutingPolicyEngine(cfg)
        issues = engine.validate()
        warnings = [i for i in issues if i.severity == "warning"]
        assert any("overlap" in i.message.lower() for i in warnings)


# ── Fleet config (matches scanner_fleet.yaml and babelshark_config.yaml) ─────

FLEET_CONFIG = {
    "dry_run": True,
    "timezone": "Europe/Copenhagen",
    "fallback_destination": "clinical_pacs",
    "default_mode": "clinical_day",
    "modes": {
        "clinical_day": {
            "active": {"start": "08:00", "end": "16:00"},
            "profile": "clinical",
            "default_destination": "clinical_pacs",
            "scanner_destinations": {},
        },
        "research_night": {
            "active": {"start": "16:00", "end": "07:00"},
            "profile": "research",
            "default_destination": "research_storage",
            "scanner_destinations": {
                "M40010": {"destination": "research_storage_resolute"},
                "M40015": {"destination": "research_storage_avenger"},
                "M40023": {"destination": "research_storage_homeone"},
                "M40024": {"destination": "research_storage_chimera"},
                "SS12620R": {"destination": "research_storage_stardestroyer"},
            },
        },
    },
    "color_dot_rules": {
        "red":    {"destination": "urgent_research"},
        "blue":   {"destination": "research_project_blue"},
        "green":  {"destination": "research_project_green"},
        "yellow": {"destination": "clinical_special"},
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# TestFleetConfig — validates routing behaviour against real fleet scanner IDs
# ─────────────────────────────────────────────────────────────────────────────

class TestFleetConfig:
    def setup_method(self) -> None:
        self.engine = RoutingPolicyEngine(FLEET_CONFIG)

    # Mode detection
    def test_clinical_day_at_noon(self) -> None:
        assert self.engine.get_active_mode(_at(12)).name == "clinical_day"

    def test_research_night_at_20(self) -> None:
        assert self.engine.get_active_mode(_at(20)).name == "research_night"

    def test_research_night_at_02(self) -> None:
        assert self.engine.get_active_mode(_at(2)).name == "research_night"

    def test_clinical_day_at_08_boundary(self) -> None:
        assert self.engine.get_active_mode(_at(8)).name == "clinical_day"

    def test_research_night_at_1600_boundary(self) -> None:
        assert self.engine.get_active_mode(_at(16)).name == "research_night"

    # During day — all scanners use clinical_pacs via mode default
    def test_all_fleet_scanners_go_to_clinical_pacs_at_day(self) -> None:
        for scanner_id in ("M40010", "M40015", "M40023", "M40024", "SS12620R"):
            result = self.engine.get_routing_decision(scanner_id=scanner_id, now=_at(12))
            assert result.destination == "clinical_pacs", (
                f"{scanner_id} should route to clinical_pacs during day, got {result.destination}"
            )
            assert result.dry_run is True

    # During night — each scanner routes to its own storage
    def test_resolute_m40010_routes_to_research_storage_resolute(self) -> None:
        result = self.engine.get_routing_decision(scanner_id="M40010", now=_at(20))
        assert result.destination == "research_storage_resolute"
        assert REASON_SCANNER_POLICY in result.routing_reason

    def test_avenger_m40015_routes_to_research_storage_avenger(self) -> None:
        result = self.engine.get_routing_decision(scanner_id="M40015", now=_at(20))
        assert result.destination == "research_storage_avenger"
        assert REASON_SCANNER_POLICY in result.routing_reason

    def test_homeone_m40023_routes_to_research_storage_homeone(self) -> None:
        result = self.engine.get_routing_decision(scanner_id="M40023", now=_at(20))
        assert result.destination == "research_storage_homeone"

    def test_chimera_m40024_routes_to_research_storage_chimera(self) -> None:
        result = self.engine.get_routing_decision(scanner_id="M40024", now=_at(20))
        assert result.destination == "research_storage_chimera"

    def test_stardestroyer_ss12620r_routes_to_research_storage_stardestroyer(self) -> None:
        result = self.engine.get_routing_decision(scanner_id="SS12620R", now=_at(20))
        assert result.destination == "research_storage_stardestroyer"

    def test_unknown_scanner_uses_research_storage_default_at_night(self) -> None:
        result = self.engine.get_routing_decision(scanner_id="UNKNOWN", now=_at(20))
        assert result.destination == "research_storage"
        assert REASON_MODE_DEFAULT in result.routing_reason

    # Color-dot rules
    def test_red_dot_routes_to_urgent_research(self) -> None:
        result = self.engine.get_routing_decision(color_dot="red", now=_at(12))
        assert result.destination == "urgent_research"

    def test_blue_dot_routes_to_research_project_blue(self) -> None:
        result = self.engine.get_routing_decision(color_dot="blue", now=_at(12))
        assert result.destination == "research_project_blue"

    def test_green_dot_routes_to_research_project_green(self) -> None:
        result = self.engine.get_routing_decision(color_dot="green", now=_at(12))
        assert result.destination == "research_project_green"

    def test_yellow_dot_routes_to_clinical_special(self) -> None:
        result = self.engine.get_routing_decision(color_dot="yellow", now=_at(12))
        assert result.destination == "clinical_special"

    def test_color_dot_beats_scanner_policy_at_night(self) -> None:
        result = self.engine.get_routing_decision(
            scanner_id="M40010", color_dot="red", now=_at(20)
        )
        assert result.destination == "urgent_research"
        assert REASON_COLOR_DOT in result.routing_reason

    # Fallback
    def test_fallback_destination_is_clinical_pacs(self) -> None:
        engine_no_modes = RoutingPolicyEngine({
            "dry_run": True,
            "fallback_destination": "clinical_pacs",
        })
        result = engine_no_modes.get_routing_decision(scanner_id="M40010")
        assert result.destination == "clinical_pacs"
        assert result.routing_reason == REASON_FALLBACK

    # Validation
    def test_fleet_config_has_no_errors(self) -> None:
        issues = self.engine.validate()
        errors = [i for i in issues if i.severity == "error"]
        assert errors == [], f"Unexpected validation errors: {errors}"

    def test_fleet_config_dry_run_is_true(self) -> None:
        summary = self.engine.get_status_summary()
        assert summary["dry_run"] is True

    def test_fleet_config_has_two_modes(self) -> None:
        summary = self.engine.get_status_summary()
        assert len(summary["modes"]) == 2

    def test_fleet_config_has_four_color_rules(self) -> None:
        summary = self.engine.get_status_summary()
        assert len(summary["color_dot_rules"]) == 4

    def test_fleet_config_fallback_in_summary(self) -> None:
        summary = self.engine.get_status_summary()
        assert summary["fallback_destination"] == "clinical_pacs"


# ─────────────────────────────────────────────────────────────────────────────
# TestActiveConfigFile — loads configs/babelshark_config.yaml and validates it
# ─────────────────────────────────────────────────────────────────────────────

class TestActiveConfigFile:
    """Reads the actual config file from disk to catch YAML/key drift."""

    CONFIG_PATH = "configs/babelshark_config.yaml"

    @pytest.fixture(scope="class")
    def policies(self):
        import yaml
        from pathlib import Path
        path = Path(self.CONFIG_PATH)
        if not path.exists():
            pytest.skip(f"{self.CONFIG_PATH} not found — run from repo root")
        with open(path) as fh:
            cfg = yaml.safe_load(fh) or {}
        p = cfg.get("routing_policies")
        if p is None:
            pytest.fail(f"routing_policies section missing from {self.CONFIG_PATH}")
        return p

    def test_routing_policies_present(self, policies) -> None:
        assert policies is not None

    def test_dry_run_is_true(self, policies) -> None:
        assert policies.get("dry_run") is True

    def test_has_clinical_day_mode(self, policies) -> None:
        assert "clinical_day" in policies.get("modes", {})

    def test_has_research_night_mode(self, policies) -> None:
        assert "research_night" in policies.get("modes", {})

    def test_clinical_day_default_destination_set(self, policies) -> None:
        assert policies["modes"]["clinical_day"].get("default_destination") == "clinical_pacs"

    def test_research_night_default_destination_set(self, policies) -> None:
        assert policies["modes"]["research_night"].get("default_destination") == "research_storage"

    def test_fallback_destination_is_clinical_pacs(self, policies) -> None:
        assert policies.get("fallback_destination") == "clinical_pacs"

    def test_scanner_ids_match_fleet(self, policies) -> None:
        expected = {"M40010", "M40015", "M40023", "M40024", "SS12620R"}
        night_dests = policies["modes"]["research_night"].get("scanner_destinations", {})
        actual = set(night_dests.keys())
        assert actual == expected, f"Scanner ID mismatch. Expected {expected}, got {actual}"

    def test_color_dot_rules_present(self, policies) -> None:
        rules = policies.get("color_dot_rules", {})
        for color in ("red", "blue", "green", "yellow"):
            assert color in rules, f"Missing color_dot_rule for '{color}'"

    def test_engine_loads_without_errors(self, policies) -> None:
        engine = RoutingPolicyEngine(policies)
        issues = engine.validate()
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []


# ─────────────────────────────────────────────────────────────────────────────
# TestDashboardAPIWithFleetConfig — API returns fleet config data
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardAPIWithFleetConfig:
    """Verifies the routing status API surface when fleet config is active."""

    @pytest.fixture(scope="class")
    def client_with_fleet_config(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch, MagicMock
        from pathoryx_enterprise.services.dashboard.app import create_app, get_db

        app = create_app()
        mock_db = MagicMock()
        app.dependency_overrides[get_db] = lambda: mock_db

        # Inject FLEET_CONFIG so the test is independent of disk state
        fleet_babelshark = {"routing_policies": FLEET_CONFIG}
        with patch(
            "pathoryx_enterprise.services.dashboard.app._load_babelshark_config",
            return_value=fleet_babelshark,
        ):
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c
        app.dependency_overrides.clear()

    def test_status_has_two_modes(self, client_with_fleet_config) -> None:
        resp = client_with_fleet_config.get("/dashboard/api/routing/status")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body.get("modes", [])) == 2

    def test_status_has_four_color_dot_rules(self, client_with_fleet_config) -> None:
        resp = client_with_fleet_config.get("/dashboard/api/routing/status")
        body = resp.json()
        assert len(body.get("color_dot_rules", [])) == 4

    def test_status_fallback_destination(self, client_with_fleet_config) -> None:
        resp = client_with_fleet_config.get("/dashboard/api/routing/status")
        body = resp.json()
        assert body.get("fallback_destination") == "clinical_pacs"

    def test_status_dry_run_true(self, client_with_fleet_config) -> None:
        resp = client_with_fleet_config.get("/dashboard/api/routing/status")
        body = resp.json()
        assert body.get("dry_run") is True

    def test_status_has_no_policy_error_when_config_present(self, client_with_fleet_config) -> None:
        resp = client_with_fleet_config.get("/dashboard/api/routing/status")
        body = resp.json()
        issues = body.get("validation_issues", [])
        policy_missing = [i for i in issues if "missing" in i.get("message", "").lower()]
        assert policy_missing == [], f"Unexpected 'missing' validation issues: {policy_missing}"

    def test_status_mode_names_correct(self, client_with_fleet_config) -> None:
        resp = client_with_fleet_config.get("/dashboard/api/routing/status")
        body = resp.json()
        mode_names = {m["name"] for m in body.get("modes", [])}
        assert "clinical_day" in mode_names
        assert "research_night" in mode_names

    def test_status_scanner_destinations_in_research_night(self, client_with_fleet_config) -> None:
        resp = client_with_fleet_config.get("/dashboard/api/routing/status")
        body = resp.json()
        night = next(m for m in body["modes"] if m["name"] == "research_night")
        scanner_ids = {s["scanner_id"] for s in night.get("scanner_destinations", [])}
        assert "M40010" in scanner_ids
        assert "SS12620R" in scanner_ids
