"""
Phase 4.4 — Palantir Computer Core analytics endpoints.

Tests:
  A. get_core_overview — counts from FileRecord and EstimatedUploadQueue
  B. get_stain_distribution — GROUP BY stain_type with percentages
  C. get_recovery_stats — TechnicianChange and WatchedFolderSnapshot aggregation
  D. get_storage_stats — per-scanner breakdown and aggregates
  E. get_upload_velocity — speed, duration, daily counts
  F. GET /core/overview endpoint — 200 with correct schema
  G. GET /core/stains endpoint — 200 with correct schema
  H. GET /core/recovery endpoint — 200 with correct schema
  I. GET /core/storage endpoint — 200 with correct schema
  J. GET /core/uploads endpoint — 200 with correct schema
  K. GET /core/scanners endpoint — 200 with correct schema
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app_client():
    from pathoryx_enterprise.services.dashboard.app import create_app, get_db
    app = create_app()
    session_mock = MagicMock()
    app.dependency_overrides[get_db] = lambda: session_mock
    client = TestClient(app, raise_server_exceptions=False)
    return client, session_mock


def _empty_overview() -> dict:
    return {
        "total_slides": 0, "slides_today": 0, "uploaded_today": 0,
        "failed_slides": 0, "active_uploads": 0, "queued_uploads": 0,
        "delayed_uploads": 0, "recovery_backlog": 0, "unreviewed_changes": 0,
        "total_bytes": 0, "status_counts": {}, "upload_status_counts": {},
    }


def _empty_recovery() -> dict:
    return {
        "total_monitored": 0, "failed_count": 0, "suspicious_count": 0,
        "manual_review_count": 0, "auto_recovered": 0, "manual_review_required": 0,
        "total_changes": 0, "total_resolved": 0, "recovery_rate": 0.0,
        "recent_7d": 0, "by_folder": {}, "by_review_status": {}, "by_outcome": {},
    }


def _empty_storage() -> dict:
    return {
        "total_slides_with_size": 0, "total_bytes": 0, "avg_bytes": 0,
        "max_bytes": 0, "min_bytes": 0, "uploaded_today_bytes": 0, "by_scanner": [],
    }


def _empty_uploads() -> dict:
    return {
        "avg_speed_mbps": None, "avg_duration_seconds": None,
        "total_in_queue": 0, "completed_total": 0, "failed_total": 0,
        "total_retries": 0, "queue_depth": 0, "delayed_count": 0,
        "daily_uploads_7d": [],
    }


# ---------------------------------------------------------------------------
# A. get_core_overview
# ---------------------------------------------------------------------------

class TestGetCoreOverview:
    def test_returns_zero_counts_on_empty_db(self):
        from pathoryx_enterprise.services.dashboard.core_queries import get_core_overview

        session = MagicMock()

        call_count = [0]
        def _exec(stmt):
            call_count[0] += 1
            r = MagicMock()
            r.all.return_value = []
            r.scalar.return_value = 0
            return r

        session.execute.side_effect = _exec

        result = get_core_overview(session)

        assert result["total_slides"] == 0
        assert result["uploaded_today"] == 0
        assert result["active_uploads"] == 0

    def test_aggregates_slide_status_counts(self):
        from pathoryx_enterprise.services.dashboard.core_queries import get_core_overview

        session = MagicMock()

        # Mock various execute calls
        call_count = [0]
        def _exec(stmt):
            call_count[0] += 1
            r = MagicMock()
            if call_count[0] == 1:
                # FileRecord GROUP BY status
                mock_row1 = MagicMock()
                mock_row1.status = "uploaded"
                mock_row1.cnt = 100
                mock_row1.total_size = 1_073_741_824
                mock_row2 = MagicMock()
                mock_row2.status = "qc_failed"
                mock_row2.cnt = 5
                mock_row2.total_size = 0
                r.all.return_value = [mock_row1, mock_row2]
            elif call_count[0] == 2:
                # slides_today COUNT
                r.scalar.return_value = 10
            elif call_count[0] == 3:
                # EstimatedUploadQueue GROUP BY status
                r.all.return_value = []
            else:
                r.all.return_value = []
                r.scalar.return_value = 0
            return r

        session.execute.side_effect = _exec

        result = get_core_overview(session)

        assert result["total_slides"] == 105
        assert result["failed_slides"] == 5
        assert result["slides_today"] == 10
        assert result["status_counts"]["uploaded"] == 100


# ---------------------------------------------------------------------------
# B. get_stain_distribution
# ---------------------------------------------------------------------------

class TestGetStainDistribution:
    def test_returns_empty_on_no_data(self):
        from pathoryx_enterprise.services.dashboard.core_queries import get_stain_distribution

        session = MagicMock()
        session.execute.return_value.all.return_value = []

        result = get_stain_distribution(session)
        assert result == []

    def test_computes_percentage_correctly(self):
        from pathoryx_enterprise.services.dashboard.core_queries import get_stain_distribution

        session = MagicMock()
        r1 = MagicMock(); r1.stain_type = "H&E"; r1.cnt = 75
        r2 = MagicMock(); r2.stain_type = "IHC"; r2.cnt = 25

        session.execute.return_value.all.return_value = [r1, r2]

        result = get_stain_distribution(session)

        assert len(result) == 2
        he = next(i for i in result if i["stain_type"] == "H&E")
        assert he["count"] == 75
        assert he["percentage"] == pytest.approx(75.0, abs=0.1)

        ihc = next(i for i in result if i["stain_type"] == "IHC")
        assert ihc["percentage"] == pytest.approx(25.0, abs=0.1)

    def test_unknown_stain_type_shown_as_unknown(self):
        from pathoryx_enterprise.services.dashboard.core_queries import get_stain_distribution

        session = MagicMock()
        r = MagicMock(); r.stain_type = None; r.cnt = 10
        session.execute.return_value.all.return_value = [r]

        result = get_stain_distribution(session)
        assert result[0]["stain_type"] == "Unknown"


# ---------------------------------------------------------------------------
# C. get_recovery_stats
# ---------------------------------------------------------------------------

class TestGetRecoveryStats:
    def test_returns_zero_recovery_rate_when_no_changes(self):
        from pathoryx_enterprise.services.dashboard.core_queries import get_recovery_stats

        session = MagicMock()

        call_count = [0]
        def _exec(stmt):
            call_count[0] += 1
            r = MagicMock()
            r.all.return_value = []
            r.scalar.return_value = 0
            return r

        session.execute.side_effect = _exec

        result = get_recovery_stats(session)

        assert result["recovery_rate"] == 0.0
        assert result["total_monitored"] == 0

    def test_recovery_rate_calculated_correctly(self):
        from pathoryx_enterprise.services.dashboard.core_queries import get_recovery_stats

        session = MagicMock()
        call_count = [0]

        def _exec(stmt):
            call_count[0] += 1
            r = MagicMock()
            if call_count[0] == 1:
                # WatchedFolderSnapshot GROUP BY folder_label
                rr = MagicMock(); rr.folder_label = "failed"; rr.cnt = 20
                r.all.return_value = [rr]
            elif call_count[0] == 2:
                # TechnicianChange GROUP BY review_status
                r1 = MagicMock(); r1.review_status = "reviewed"; r1.cnt = 8
                r2 = MagicMock(); r2.review_status = "pending"; r2.cnt = 2
                r.all.return_value = [r1, r2]
            elif call_count[0] == 3:
                # TechnicianChange GROUP BY recovery_outcome
                r.all.return_value = []
            else:
                r.scalar.return_value = 3
                r.all.return_value = []
            return r

        session.execute.side_effect = _exec

        result = get_recovery_stats(session)

        assert result["failed_count"] == 20
        assert result["total_changes"] == 10
        assert result["total_resolved"] == 8
        assert result["recovery_rate"] == pytest.approx(80.0, abs=0.1)


# ---------------------------------------------------------------------------
# D. get_storage_stats
# ---------------------------------------------------------------------------

class TestGetStorageStats:
    def test_returns_zeros_on_empty_db(self):
        from pathoryx_enterprise.services.dashboard.core_queries import get_storage_stats

        session = MagicMock()
        call_count = [0]

        def _exec(stmt):
            call_count[0] += 1
            r = MagicMock()
            if call_count[0] == 1:
                # Aggregate query
                r.one.return_value.cnt = 0
                r.one.return_value.total = None
                r.one.return_value.avg = None
                r.one.return_value.max_sz = None
                r.one.return_value.min_sz = None
            elif call_count[0] == 2:
                # Per-scanner
                r.all.return_value = []
            else:
                r.scalar.return_value = 0
            return r

        session.execute.side_effect = _exec

        result = get_storage_stats(session)

        assert result["total_bytes"] == 0
        assert result["by_scanner"] == []

    def test_per_scanner_breakdown_populated(self):
        from pathoryx_enterprise.services.dashboard.core_queries import get_storage_stats

        session = MagicMock()
        call_count = [0]

        def _exec(stmt):
            call_count[0] += 1
            r = MagicMock()
            if call_count[0] == 1:
                r.one.return_value.cnt = 10
                r.one.return_value.total = 10 * 1_073_741_824
                r.one.return_value.avg = 1_073_741_824
                r.one.return_value.max_sz = 2_147_483_648
                r.one.return_value.min_sz = 536_870_912
            elif call_count[0] == 2:
                row = MagicMock()
                row.scanner_id = "SCANNER01"
                row.cnt = 10
                row.total = 10 * 1_073_741_824
                row.avg = 1_073_741_824
                r.all.return_value = [row]
            else:
                r.scalar.return_value = 0
            return r

        session.execute.side_effect = _exec

        result = get_storage_stats(session)

        assert len(result["by_scanner"]) == 1
        assert result["by_scanner"][0]["scanner_id"] == "SCANNER01"


# ---------------------------------------------------------------------------
# E. get_upload_velocity
# ---------------------------------------------------------------------------

class TestGetUploadVelocity:
    def test_returns_none_speed_when_no_upload_data(self):
        from pathoryx_enterprise.services.dashboard.core_queries import get_upload_velocity

        session = MagicMock()
        call_count = [0]

        def _exec(stmt):
            call_count[0] += 1
            r = MagicMock()
            r.one.return_value.avg_speed = None
            r.one.return_value.total = 0
            r.one.return_value.completed = 0
            r.one.return_value.failed = 0
            r.one.return_value.total_retries = 0
            r.one.return_value.avg_dur = None
            r.all.return_value = []
            r.scalar.return_value = 0
            return r

        session.execute.side_effect = _exec

        result = get_upload_velocity(session)

        assert result["avg_speed_mbps"] is None
        assert result["avg_duration_seconds"] is None
        assert result["daily_uploads_7d"] == []


# ---------------------------------------------------------------------------
# F–K: API endpoint tests
# ---------------------------------------------------------------------------

class TestComputerCoreEndpoints:
    MODULE = "pathoryx_enterprise.services.dashboard.core_queries"

    def test_overview_endpoint_returns_200(self):
        client, _ = _make_app_client()
        with patch(f"{self.MODULE}.get_core_overview", return_value=_empty_overview()):
            resp = client.get("/dashboard/api/core/overview")
        assert resp.status_code == 200
        body = resp.json()
        assert "total_slides" in body
        assert "as_of" in body

    def test_stains_endpoint_returns_200(self):
        client, _ = _make_app_client()
        with patch(f"{self.MODULE}.get_stain_distribution", return_value=[
            {"stain_type": "H&E", "count": 50, "percentage": 100.0}
        ]):
            resp = client.get("/dashboard/api/core/stains")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert body["total"] == 50

    def test_recovery_endpoint_returns_200(self):
        client, _ = _make_app_client()
        with patch(f"{self.MODULE}.get_recovery_stats", return_value=_empty_recovery()):
            resp = client.get("/dashboard/api/core/recovery")
        assert resp.status_code == 200
        body = resp.json()
        assert "recovery_rate" in body
        assert "total_monitored" in body

    def test_storage_endpoint_returns_200(self):
        client, _ = _make_app_client()
        with patch(f"{self.MODULE}.get_storage_stats", return_value=_empty_storage()):
            resp = client.get("/dashboard/api/core/storage")
        assert resp.status_code == 200
        body = resp.json()
        assert "total_bytes" in body
        assert "by_scanner" in body

    def test_uploads_endpoint_returns_200(self):
        client, _ = _make_app_client()
        with patch(f"{self.MODULE}.get_upload_velocity", return_value=_empty_uploads()):
            resp = client.get("/dashboard/api/core/uploads")
        assert resp.status_code == 200
        body = resp.json()
        assert "avg_speed_mbps" in body
        assert "daily_uploads_7d" in body

    def test_scanners_endpoint_returns_200(self):
        client, _ = _make_app_client()
        with patch(f"{self.MODULE}.get_scanner_activity", return_value=[]):
            resp = client.get("/dashboard/api/core/scanners")
        assert resp.status_code == 200
        body = resp.json()
        assert "scanners" in body
        assert isinstance(body["scanners"], list)

    def test_endpoints_degrade_gracefully_on_db_error(self):
        """All core endpoints return 200 with zeroed data when DB throws."""
        client, _ = _make_app_client()
        with patch(
            f"{self.MODULE}.get_core_overview",
            side_effect=Exception("DB connection lost"),
        ):
            resp = client.get("/dashboard/api/core/overview")
        assert resp.status_code == 200
        assert resp.json()["total_slides"] == 0

    def test_stain_distribution_response_has_percentage(self):
        client, _ = _make_app_client()
        with patch(f"{self.MODULE}.get_stain_distribution", return_value=[
            {"stain_type": "IHC", "count": 30, "percentage": 60.0},
            {"stain_type": "H&E", "count": 20, "percentage": 40.0},
        ]):
            resp = client.get("/dashboard/api/core/stains")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2
        assert items[0]["percentage"] == pytest.approx(60.0)
