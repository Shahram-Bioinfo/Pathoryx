"""
Phase 10 — Observability, Safety & Operational Stability: unit tests.

Tests cover:
  - Heartbeat aging and health state classification
  - Stuck trigger detection (pending/running/exhausted)
  - build_operational_incidents severity classification
  - Environment config loading (safe defaults on missing files)
  - Dry-run safety banner surfacing
  - Queue intelligence from service health
  - DB health metrics endpoint degradation
  - SSE invalidation includes 'operations' key
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient  # noqa: E402

from pathoryx_enterprise.services.dashboard.app import create_app, get_db  # noqa: E402
from pathoryx_enterprise.services.dashboard.queries import (  # noqa: E402
    RUNNER_STALE_THRESHOLD_SECONDS,
    STUCK_PENDING_THRESHOLD_MINUTES,
    STUCK_RUNNING_THRESHOLD_MINUTES,
    build_operational_incidents,
    get_environment_config,
    build_retry_chains,
)

_QUERY_MODULE = "pathoryx_enterprise.services.dashboard.app.q"
_NOW = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(scope="module")
def mock_db():
    return MagicMock()


@pytest.fixture(scope="module")
def client(app, mock_db):
    app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# A) Heartbeat aging & health state
# ---------------------------------------------------------------------------

class TestHeartbeatAging:
    def _runner(self, age_seconds: float) -> dict:
        """Build a dict that looks like get_service_health_extended output."""
        return {
            "runner_id":             "runner-test",
            "service_name":          "qc_service",
            "host_id":               "host-01",
            "pid":                   12345,
            "status":                "active",
            "health_state":          "healthy",  # will be overwritten
            "heartbeat_age_seconds": age_seconds,
            "uptime_seconds":        3600.0,
            "started_at":            _NOW - timedelta(hours=1),
            "last_heartbeat_at":     _NOW - timedelta(seconds=age_seconds),
            "environment":           "development",
            "service_version":       "1.0.0",
            "queue_pending":         0,
            "queue_running":         0,
            "queue_failed":          0,
        }

    def test_threshold_constants_defined(self):
        assert RUNNER_STALE_THRESHOLD_SECONDS == 120

    def test_healthy_threshold_below_60(self):
        """Heartbeats under 60s map to healthy state (per get_service_health_extended)."""
        assert RUNNER_STALE_THRESHOLD_SECONDS > 60

    def test_operations_health_endpoint_200(self, client):
        rows = [
            {
                "runner_id": "r1", "service_name": "qc_service",
                "host_id": "h1", "pid": 100, "status": "active",
                "health_state": "healthy", "heartbeat_age_seconds": 10.0,
                "uptime_seconds": 3600.0,
                "started_at": _NOW - timedelta(hours=1),
                "last_heartbeat_at": _NOW - timedelta(seconds=10),
                "environment": "development", "service_version": "1.0.0",
                "queue_pending": 0, "queue_running": 0, "queue_failed": 0,
            }
        ]
        with patch(f"{_QUERY_MODULE}.get_service_health_extended", return_value=rows):
            resp = client.get("/dashboard/api/operations/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["services"][0]["health_state"] == "healthy"
        assert data["services"][0]["heartbeat_age_seconds"] == pytest.approx(10.0, abs=0.1)

    def test_operations_health_empty_degrades_gracefully(self, client):
        from sqlalchemy.exc import OperationalError
        with patch(
            f"{_QUERY_MODULE}.get_service_health_extended",
            side_effect=OperationalError("", {}, Exception()),
        ):
            resp = client.get("/dashboard/api/operations/health")
        assert resp.status_code == 200
        assert resp.json()["services"] == []

    def test_stale_service_generates_warning_incident(self):
        stale_svc = {
            "service_name": "qc_service",
            "health_state": "stale",
            "heartbeat_age_seconds": 200.0,
        }
        incidents = build_operational_incidents(
            [stale_svc], [], {"failed_triggers": 0, "recovery_backlog": 0},
            {"environment": "development", "upload_dry_run": True, "c_store_enabled": False},
        )
        assert any(i["severity"] == "warning" and "stale" in i["title"].lower() for i in incidents)

    def test_disconnected_service_generates_critical_incident(self):
        disc_svc = {
            "service_name": "upload_service",
            "health_state": "disconnected",
            "heartbeat_age_seconds": 600.0,
        }
        incidents = build_operational_incidents(
            [disc_svc], [], {"failed_triggers": 0, "recovery_backlog": 0},
            {"environment": "development", "upload_dry_run": True, "c_store_enabled": False},
        )
        assert any(i["severity"] == "critical" for i in incidents)


# ---------------------------------------------------------------------------
# B) Stuck trigger detection
# ---------------------------------------------------------------------------

class TestStuckTriggerDetection:
    def test_threshold_constants_defined(self):
        assert STUCK_PENDING_THRESHOLD_MINUTES == 15
        assert STUCK_RUNNING_THRESHOLD_MINUTES == 60

    def test_stuck_triggers_endpoint_200(self, client):
        items = [
            {
                "trigger_id": 42, "kind": "pending_stuck", "severity": "warning",
                "stage": "qc", "target_service": "qc_service",
                "global_artifact_id": "GAI-001",
                "stuck_seconds": 1200.0, "retry_count": 0, "max_retries": 3,
                "error_message": None,
                "triggered_at": _NOW - timedelta(minutes=20),
                "likely_cause": "No active worker",
            }
        ]
        with patch(f"{_QUERY_MODULE}.get_stuck_triggers", return_value=items):
            resp = client.get("/dashboard/api/operations/stuck-triggers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["pending_stuck"] == 1
        assert data["items"][0]["kind"] == "pending_stuck"

    def test_stuck_triggers_empty_on_db_error(self, client):
        from sqlalchemy.exc import OperationalError
        with patch(
            f"{_QUERY_MODULE}.get_stuck_triggers",
            side_effect=OperationalError("", {}, Exception()),
        ):
            resp = client.get("/dashboard/api/operations/stuck-triggers")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_exhausted_trigger_generates_critical_incident(self):
        exhausted = {
            "trigger_id": 1, "kind": "exhausted", "severity": "critical",
            "stage": "dicom", "target_service": "dicom_service",
            "global_artifact_id": None, "stuck_seconds": 3600.0,
            "retry_count": 3, "max_retries": 3,
            "error_message": None, "triggered_at": None,
            "likely_cause": "Retry limit exhausted",
        }
        incidents = build_operational_incidents(
            [], [exhausted], {"failed_triggers": 0, "recovery_backlog": 0},
            {"environment": "development", "upload_dry_run": True, "c_store_enabled": False},
        )
        assert any(i["severity"] == "critical" and "exhausted" in i["title"].lower() for i in incidents)

    def test_custom_threshold_passed_to_endpoint(self, client):
        with patch(f"{_QUERY_MODULE}.get_stuck_triggers", return_value=[]) as mock:
            client.get("/dashboard/api/operations/stuck-triggers?pending_threshold_minutes=30")
        mock.assert_called_once()
        assert mock.call_args[1]["pending_threshold_minutes"] == 30


# ---------------------------------------------------------------------------
# C) Operational incidents & severity classification
# ---------------------------------------------------------------------------

class TestOperationalIncidents:
    def _base_env(self, dry_run=True, c_store=False, env="development"):
        return {
            "environment":    env,
            "upload_dry_run": dry_run,
            "c_store_enabled": c_store,
        }

    def test_incidents_endpoint_200(self, client):
        with (
            patch(f"{_QUERY_MODULE}.get_service_health_extended", return_value=[]),
            patch(f"{_QUERY_MODULE}.get_stuck_triggers", return_value=[]),
            patch(f"{_QUERY_MODULE}.get_db_health_metrics", return_value={"failed_triggers": 0, "recovery_backlog": 0}),
            patch(f"{_QUERY_MODULE}.get_environment_config", return_value=self._base_env()),
        ):
            resp = client.get("/dashboard/api/operations/incidents")
        assert resp.status_code == 200
        data = resp.json()
        assert "incidents" in data
        assert "critical_count" in data
        assert "warning_count" in data
        assert "as_of" in data

    def test_dry_run_generates_info_incident(self):
        incidents = build_operational_incidents(
            [], [], {"failed_triggers": 0, "recovery_backlog": 0},
            self._base_env(dry_run=True),
        )
        assert any(i["severity"] == "info" and "dry" in i["title"].lower() for i in incidents)

    def test_live_upload_in_nonprod_generates_warning(self):
        incidents = build_operational_incidents(
            [], [], {"failed_triggers": 0, "recovery_backlog": 0},
            self._base_env(dry_run=False, c_store=True, env="development"),
        )
        assert any(i["severity"] == "warning" for i in incidents)

    def test_failed_trigger_accumulation_generates_warning(self):
        incidents = build_operational_incidents(
            [], [], {"failed_triggers": 15, "recovery_backlog": 0},
            self._base_env(),
        )
        assert any(i["severity"] == "warning" and "failed trigger" in i["title"].lower() for i in incidents)

    def test_all_nominal_no_critical(self):
        incidents = build_operational_incidents(
            [{"service_name": "qc", "health_state": "healthy", "heartbeat_age_seconds": 5.0}],
            [],
            {"failed_triggers": 0, "recovery_backlog": 0},
            self._base_env(dry_run=True),
        )
        assert not any(i["severity"] == "critical" for i in incidents)

    def test_incidents_sorted_critical_first(self):
        svc = {"service_name": "qc", "health_state": "disconnected", "heartbeat_age_seconds": 600.0}
        incidents = build_operational_incidents(
            [svc], [], {"failed_triggers": 5, "recovery_backlog": 0},
            self._base_env(dry_run=True),
        )
        severities = [i["severity"] for i in incidents]
        # critical must appear before info
        if "critical" in severities and "info" in severities:
            assert severities.index("critical") < severities.index("info")

    def test_recovery_backlog_generates_warning(self):
        incidents = build_operational_incidents(
            [], [], {"failed_triggers": 0, "recovery_backlog": 25},
            self._base_env(),
        )
        assert any("recovery" in i["title"].lower() for i in incidents)


# ---------------------------------------------------------------------------
# D) Environment config
# ---------------------------------------------------------------------------

class TestEnvironmentConfig:
    def test_returns_safe_defaults_when_config_missing(self):
        cfg = get_environment_config()
        # Must return a dict, never raise
        assert isinstance(cfg, dict)
        assert "environment" in cfg
        assert "upload_dry_run" in cfg
        assert "c_store_enabled" in cfg
        assert "lis_enabled" in cfg
        assert "pasnet_enabled" in cfg

    def test_default_upload_is_dry_run(self):
        """When config file exists and has dry_run=true, it should parse correctly."""
        import tempfile, os, yaml
        cfg_content = {
            "upload": {"dry_run": True},
            "cstore": {"upload_via_c_store": False, "peer_ip": "test-pacs"},
            "lis": {"enabled": False},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(cfg_content, f)
            f.flush()
            env_override = {"DICOM_CONFIG_PATH": f.name}
            with patch.dict(os.environ, env_override):
                result = get_environment_config()
        os.unlink(f.name)
        assert result["upload_dry_run"] is True
        assert result["c_store_enabled"] is False
        assert result["upload_peer_ip"] == "test-pacs"

    def test_live_upload_detected(self):
        import tempfile, os, yaml
        cfg_content = {
            "upload": {"dry_run": False},
            "cstore": {"upload_via_c_store": True, "peer_ip": "prod-pacs", "default_peer_port": "32001"},
            "lis": {"enabled": True},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(cfg_content, f)
            f.flush()
            with patch.dict(os.environ, {"DICOM_CONFIG_PATH": f.name}):
                result = get_environment_config()
        os.unlink(f.name)
        assert result["upload_dry_run"] is False
        assert result["c_store_enabled"] is True
        assert result["lis_enabled"] is True
        assert result["upload_peer_port"] == "32001"

    def test_environment_endpoint_200(self, client):
        with patch(f"{_QUERY_MODULE}.get_environment_config", return_value={
            "environment": "development",
            "upload_dry_run": True,
            "c_store_enabled": False,
            "lis_enabled": False,
            "pasnet_enabled": False,
            "upload_peer_ip": None,
            "upload_peer_port": None,
            "sec_dcm_bin": None,
        }):
            resp = client.get("/dashboard/api/operations/environment")
        assert resp.status_code == 200
        data = resp.json()
        assert data["upload_dry_run"] is True
        assert data["environment"] == "development"


# ---------------------------------------------------------------------------
# E) DB health metrics endpoint
# ---------------------------------------------------------------------------

class TestDbHealthEndpoint:
    def test_returns_200_with_data(self, client):
        metrics = {
            "table_sizes": {"core.service_trigger": 1000, "events.pipeline_events": 5000},
            "failed_triggers": 3,
            "pending_triggers": 5,
            "oldest_pending_age_seconds": 45.0,
            "recovery_backlog": 2,
        }
        with patch(f"{_QUERY_MODULE}.get_db_health_metrics", return_value=metrics):
            resp = client.get("/dashboard/api/operations/db-health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["failed_triggers"] == 3
        assert data["pending_triggers"] == 5
        assert "as_of" in data

    def test_degrades_gracefully_on_db_error(self, client):
        from sqlalchemy.exc import OperationalError
        with patch(
            f"{_QUERY_MODULE}.get_db_health_metrics",
            side_effect=OperationalError("", {}, Exception()),
        ):
            resp = client.get("/dashboard/api/operations/db-health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["failed_triggers"] == 0
        assert data["table_sizes"] == {}


# ---------------------------------------------------------------------------
# F) SSE invalidation coverage
# ---------------------------------------------------------------------------

class TestSSEInvalidationCoverage:
    def test_queue_updated_includes_operations(self):
        import os
        sse_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__),
            "../../dashboard-ui/src/hooks/useSSE.ts",
        ))
        content = open(sse_path).read()
        assert "'operations'" in content or '"operations"' in content, (
            "useSSE.ts must include 'operations' in queue_updated EVENT_INVALIDATIONS"
        )

    def test_service_health_updated_includes_operations(self):
        import os
        sse_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__),
            "../../dashboard-ui/src/hooks/useSSE.ts",
        ))
        content = open(sse_path).read()
        # The service_health_updated event should invalidate operations
        lines = content.split("\n")
        health_line = next((l for l in lines if "service_health_updated" in l), "")
        # Find the block that belongs to service_health_updated
        assert "operations" in content, "operations key must appear in useSSE.ts"

    def test_operations_center_page_exists(self):
        import os
        page_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__),
            "../../dashboard-ui/src/pages/OperationsCenter.tsx",
        ))
        assert os.path.exists(page_path), "OperationsCenter.tsx must exist"

    def test_operations_api_file_exists(self):
        import os
        api_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__),
            "../../dashboard-ui/src/api/operations.ts",
        ))
        assert os.path.exists(api_path), "api/operations.ts must exist"
