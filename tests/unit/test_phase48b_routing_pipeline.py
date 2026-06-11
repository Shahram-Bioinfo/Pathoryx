"""
Phase 4.8B — routing-pipeline integration tests.

These tests verify that:
  - the BabelShark stage runner calls the routing engine for real slides
  - dry_run is ALWAYS True — never changes the upload destination
  - decisions are recorded in DB with the correct fields
  - the 5-priority routing chain (override → color_dot → scanner_policy →
    mode_default → fallback) produces the expected decisions
  - the dashboard API returns recorded decisions and can build a chain
  - invalid/missing routing config never crashes the pipeline
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures / helpers ────────────────────────────────────────────────────────

POLICY_CFG: dict[str, Any] = {
    "dry_run": True,
    "timezone": "UTC",
    "fallback_destination": "clinical_pacs",
    "default_mode": "clinical_day",
    "modes": {
        "clinical_day": {
            "active": {"start": "00:00", "end": "23:59"},
            "profile": "clinical",
            "default_destination": "clinical_pacs",
            "scanner_destinations": {
                "M40010": {"destination": "research_storage_resolute"},
            },
        }
    },
    "color_dot_rules": {
        "red": {"destination": "urgent_research"},
        "blue": {"destination": "research_project_blue"},
    },
}

BABELSHARK_CFG = {"routing_policies": POLICY_CFG}

_NOW = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _make_runner(policy_cfg: Optional[dict] = None):
    """Return a minimal BabelSharkStageRunner for _run_routing_decision testing."""
    from pathoryx_enterprise.services.babelshark.stage_runner import BabelSharkStageRunner

    runner = BabelSharkStageRunner.__new__(BabelSharkStageRunner)
    runner.config = {"routing_policies": policy_cfg} if policy_cfg is not None else BABELSHARK_CFG
    runner.log = MagicMock()
    return runner


def _make_session(
    scanner_id: Optional[str] = "M40010",
    color_dot: Optional[str] = None,
    confidence: Optional[float] = None,
) -> MagicMock:
    """
    Return a mock DB session whose execute().fetchone() returns controlled rows.

    _run_routing_decision does two fetchone() calls:
      1st → file_records row: (scanner_id,)
      2nd → color_marker_results row: (dominant_color, raw_payload)
    """
    session = MagicMock()
    call_count = {"n": 0}

    raw_payload: dict = {}
    if color_dot:
        raw_payload["DetectedColor"] = color_dot
    if confidence is not None:
        raw_payload["Confidence"] = confidence

    rows = [
        (scanner_id,) if scanner_id else None,
        (color_dot, raw_payload) if color_dot or confidence is not None else None,
    ]

    def _execute(sql, *a, **kw):
        r = MagicMock()
        idx = call_count["n"] % len(rows)
        call_count["n"] += 1
        r.fetchone.return_value = rows[idx]
        return r

    session.execute.side_effect = _execute
    return session


# ── 1. Real pipeline calls routing engine ────────────────────────────────────

class TestPipelineCallsRoutingEngine:
    def test_run_routing_decision_is_invoked_from_enrichment_pipeline(self):
        """run_enrichment_pipeline source must reference _run_routing_decision."""
        import inspect
        from pathoryx_enterprise.services.babelshark.stage_runner import BabelSharkStageRunner

        src = inspect.getsource(BabelSharkStageRunner.run_enrichment_pipeline)
        assert "_run_routing_decision" in src, (
            "_run_routing_decision must be called from run_enrichment_pipeline"
        )

    def test_routing_decision_method_exists_on_runner(self):
        """BabelSharkStageRunner must have _run_routing_decision method."""
        from pathoryx_enterprise.services.babelshark.stage_runner import BabelSharkStageRunner

        assert callable(getattr(BabelSharkStageRunner, "_run_routing_decision", None)), (
            "BabelSharkStageRunner._run_routing_decision not found"
        )


# ── 2. dry_run records decision but does NOT change destination ───────────────

class TestDryRunDoesNotChangeDestination:
    def test_record_decision_called_with_dry_run_true(self):
        """record_decision must be called with dry_run=True regardless of config."""
        runner = _make_runner()
        session = _make_session(scanner_id="M40010")

        with (
            patch("pathoryx_enterprise.services.babelshark.stage_runner.get_session") as mock_gs,
            patch("pathoryx_enterprise.services.dashboard.routing_queries.list_active_overrides", return_value=[]),
            patch("pathoryx_enterprise.services.dashboard.routing_queries.record_decision", return_value=42) as mock_rec,
        ):
            mock_gs.return_value.__enter__ = MagicMock(return_value=session)
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)

            runner._run_routing_decision(
                file_record_id=1,
                global_artifact_id="ga-001",
                slide_id="N2024002863SA-1-1-H&E.svs",
            )

        assert mock_rec.called, "record_decision was not called"
        _, kwargs = mock_rec.call_args
        assert kwargs.get("dry_run") is True, (
            f"dry_run must be True, got {kwargs.get('dry_run')!r}"
        )

    def test_dry_run_config_false_still_records_dry_run_true(self):
        """Code must hard-enforce dry_run=True even if config sets it False."""
        runner = _make_runner({**POLICY_CFG, "dry_run": False})
        session = _make_session(scanner_id="M40010")

        with (
            patch("pathoryx_enterprise.services.babelshark.stage_runner.get_session") as mock_gs,
            patch("pathoryx_enterprise.services.dashboard.routing_queries.list_active_overrides", return_value=[]),
            patch("pathoryx_enterprise.services.dashboard.routing_queries.record_decision", return_value=99) as mock_rec,
        ):
            mock_gs.return_value.__enter__ = MagicMock(return_value=session)
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)

            runner._run_routing_decision(
                file_record_id=2,
                global_artifact_id="ga-002",
                slide_id="N2024002863SA-1-1-HE.svs",
            )

        _, kwargs = mock_rec.call_args
        assert kwargs.get("dry_run") is True, (
            "dry_run must always be True — code must not trust config value for this flag"
        )


# ── 3. Decision saved with correct fields ─────────────────────────────────────

class TestDecisionSavedWithCorrectFields:
    def test_decision_contains_slide_and_scanner(self):
        """Saved decision must include slide_id and scanner_id."""
        runner = _make_runner()
        session = _make_session(scanner_id="M40015")

        with (
            patch("pathoryx_enterprise.services.babelshark.stage_runner.get_session") as mock_gs,
            patch("pathoryx_enterprise.services.dashboard.routing_queries.list_active_overrides", return_value=[]),
            patch("pathoryx_enterprise.services.dashboard.routing_queries.record_decision", return_value=10) as mock_rec,
        ):
            mock_gs.return_value.__enter__ = MagicMock(return_value=session)
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)

            runner._run_routing_decision(
                file_record_id=3,
                global_artifact_id="ga-003",
                slide_id="N2024999999SA-1-1-HE.svs",
            )

        _, kwargs = mock_rec.call_args
        assert kwargs.get("slide_id") == "N2024999999SA-1-1-HE.svs"
        assert kwargs.get("scanner_id") == "M40015"
        assert kwargs.get("mode") is not None
        assert kwargs.get("routing_reason") is not None


# ── 4. Color-dot rule overrides scanner policy ────────────────────────────────

class TestColorDotOverrideScannerPolicy:
    def test_red_dot_routes_to_urgent_research(self):
        """color_dot=red must win over any scanner-specific policy."""
        from pathoryx_enterprise.services.routing.engine import RoutingPolicyEngine

        engine = RoutingPolicyEngine(POLICY_CFG)
        result = engine.get_routing_decision(
            scanner_id="M40010",   # has scanner_destinations → research_storage_resolute
            color_dot="red",       # color_dot_rules.red → urgent_research
            overrides=[],
            now=_NOW,
        )
        assert result.destination == "urgent_research"
        assert "color_dot" in result.routing_reason

    def test_blue_dot_routes_to_research_project_blue(self):
        from pathoryx_enterprise.services.routing.engine import RoutingPolicyEngine

        engine = RoutingPolicyEngine(POLICY_CFG)
        result = engine.get_routing_decision(
            scanner_id="M40010",
            color_dot="blue",
            overrides=[],
            now=_NOW,
        )
        assert result.destination == "research_project_blue"
        assert "color_dot" in result.routing_reason


# ── 5. Scanner policy used when no color dot ──────────────────────────────────

class TestScannerPolicyWithoutColorDot:
    def test_scanner_specific_destination_returned(self):
        from pathoryx_enterprise.services.routing.engine import RoutingPolicyEngine

        engine = RoutingPolicyEngine(POLICY_CFG)
        result = engine.get_routing_decision(
            scanner_id="M40010",
            color_dot=None,
            overrides=[],
            now=_NOW,
        )
        assert result.destination == "research_storage_resolute"
        assert "scanner" in result.routing_reason


# ── 6. Mode default used when no scanner rule ─────────────────────────────────

class TestModeDefaultWhenNoScannerRule:
    def test_unknown_scanner_falls_back_to_mode_default(self):
        from pathoryx_enterprise.services.routing.engine import RoutingPolicyEngine

        engine = RoutingPolicyEngine(POLICY_CFG)
        result = engine.get_routing_decision(
            scanner_id="UNKNOWN_SCANNER_XYZ",
            color_dot=None,
            overrides=[],
            now=_NOW,
        )
        assert result.destination == "clinical_pacs"
        assert "mode_default" in result.routing_reason or "fallback" in result.routing_reason


# ── 7. Fallback used when no policy applies ───────────────────────────────────

class TestFallbackWhenNoPolicyApplies:
    def test_no_modes_uses_fallback_destination(self):
        from pathoryx_enterprise.services.routing.engine import RoutingPolicyEngine

        minimal_cfg: dict[str, Any] = {
            "dry_run": True,
            "fallback_destination": "clinical_pacs",
        }
        engine = RoutingPolicyEngine(minimal_cfg)
        result = engine.get_routing_decision(
            scanner_id="M40010",
            color_dot=None,
            overrides=[],
            now=_NOW,
        )
        assert result.destination == "clinical_pacs"
        assert result.routing_reason in ("fallback", "no_policy")


# ── 8. Dashboard returns recorded decisions ───────────────────────────────────

class TestDashboardReturnDecisions:
    def test_decisions_endpoint_returns_items_and_total(self):
        """GET /dashboard/api/routing/decisions returns RoutingDecisionsResponse."""
        from fastapi.testclient import TestClient
        from pathoryx_enterprise.services.dashboard.app import create_app, get_db

        mock_db = MagicMock()
        mock_db.execute.return_value.mappings.return_value.all.return_value = []
        mock_db.execute.return_value.mappings.return_value.first.return_value = None
        mock_db.execute.return_value.scalar.return_value = 0

        app = create_app()
        app.dependency_overrides[get_db] = lambda: mock_db
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/dashboard/api/routing/decisions")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data


# ── 9. "Why this slide?" decision chain generated ─────────────────────────────

class TestDecisionChainEndpoint:
    def test_chain_endpoint_returns_five_step_chain(self):
        """GET /routing/decision/{id}/chain returns DecisionChainResponse with 5 steps."""
        from fastapi.testclient import TestClient
        from pathoryx_enterprise.services.dashboard.app import create_app, get_db

        mock_db = MagicMock()
        row_data = {
            "id": 1,
            "created_at": _NOW,
            "slide_id": "N2024002863SA-1-1-HE.svs",
            "scanner_id": "M40010",
            "mode": "clinical_day",
            "profile": "clinical",
            "color_dot": None,
            "color_dot_confidence": None,
            "destination": "clinical_pacs",
            "routing_reason": "mode_default",
            "override_id": None,
            "dry_run": True,
        }

        with (
            patch("pathoryx_enterprise.services.dashboard.routing_queries.get_decision_by_id", return_value=row_data),
            patch("pathoryx_enterprise.services.dashboard.app._load_babelshark_config", return_value=BABELSHARK_CFG),
        ):
            app = create_app()
            app.dependency_overrides[get_db] = lambda: mock_db
            client = TestClient(app)
            response = client.get("/dashboard/api/routing/decision/1/chain")

        assert response.status_code == 200
        data = response.json()
        assert "chain" in data, f"Missing 'chain' in response: {data}"
        assert isinstance(data["chain"], list)
        assert len(data["chain"]) == 5, f"Expected 5 chain steps, got {len(data['chain'])}"
        assert data["decision_id"] == 1
        assert data["dry_run"] is True


# ── 10. Invalid config does not crash pipeline ────────────────────────────────

class TestInvalidConfigDoesNotCrashPipeline:
    def test_missing_routing_policies_silently_skipped(self):
        """If routing_policies key is absent, _run_routing_decision returns without error."""
        from pathoryx_enterprise.services.babelshark.stage_runner import BabelSharkStageRunner

        runner = BabelSharkStageRunner.__new__(BabelSharkStageRunner)
        runner.config = {}
        runner.log = MagicMock()

        runner._run_routing_decision(
            file_record_id=99,
            global_artifact_id="ga-099",
            slide_id="N2024000000SA-1-1-HE.svs",
        )
        # No exception = pass

    def test_db_error_does_not_propagate_to_caller(self):
        """A DB error inside _run_routing_decision must be swallowed, not raised."""
        runner = _make_runner()

        with patch("pathoryx_enterprise.services.babelshark.stage_runner.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = MagicMock(side_effect=RuntimeError("DB down"))
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)

            try:
                runner._run_routing_decision(
                    file_record_id=100,
                    global_artifact_id="ga-100",
                    slide_id="N2024000001SA-1-1-HE.svs",
                )
            except Exception as exc:
                pytest.fail(
                    f"_run_routing_decision propagated exception: {exc!r}\n"
                    "Pipeline must never crash due to routing errors."
                )
