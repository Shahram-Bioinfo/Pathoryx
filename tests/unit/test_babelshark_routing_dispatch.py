"""Tests for BabelShark post-routing DB dispatch correctness.

Bug: failed-routed slides were receiving status=qc_pending and a QC trigger
because run_enrichment_pipeline dispatched unconditionally.  The root cause
was two-fold:
  1. _status_for_final_route() wrote invalid constraint values ('FINAL_ROUTE_FAILED'),
     causing SAVEPOINT rollbacks that left current_file_path pointing to the
     deleted staging path.
  2. run_enrichment_pipeline's deferred-trigger block always called
     mark_intake_complete (→ qc_pending + QC trigger) regardless of routing outcome.

Fix (covered here):
  - _status_for_final_route maps failed→babelshark_failed, success→qc_pending
  - BabelSharkDBWriter.mark_babelshark_failed sets status, path, event; no trigger
  - run_slide_id_generation returns (slide_id, routing_status, routing_output_path)
  - BabelSharkStageRunner._FAILED_ROUTING_STATUSES gates which dispatch path fires
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from pathoryx_enterprise.db.models.core import FileRecord
from pathoryx_enterprise.services.babelshark.db_writer import BabelSharkDBWriter
from pathoryx_enterprise.services.babelshark.stage_runner import BabelSharkStageRunner


# ---------------------------------------------------------------------------
# _status_for_final_route
# ---------------------------------------------------------------------------


def _status_for_final_route(row_status: str) -> str:
    from pathoryx_enterprise.services.babelshark.core.slide_id_generator import (
        _status_for_final_route as _sfr,
    )
    return _sfr(row_status)


@pytest.mark.parametrize("row_status,expected", [
    ("success", "qc_pending"),
    ("research_success", "qc_pending"),
    ("dicom_renamed", "qc_pending"),
    ("failed", "babelshark_failed"),
    ("nan", "babelshark_failed"),
    ("error", "babelshark_failed"),
    ("", "manual_review"),
    ("research_original", "manual_review"),
    ("routed", "manual_review"),
    ("suspicious", "manual_review"),
    ("FAILED", "babelshark_failed"),   # case-insensitive
    ("SUCCESS", "qc_pending"),
])
def test_status_for_final_route_valid_values(row_status, expected):
    """_status_for_final_route must only return values in ck_file_records_status."""
    result = _status_for_final_route(row_status)
    assert result == expected, f"status_for_final_route({row_status!r}) → {result!r}, want {expected!r}"


@pytest.mark.parametrize("row_status", [
    "success", "research_success", "dicom_renamed",
    "failed", "nan", "error",
    "", "research_original", "routed", "suspicious",
])
def test_status_for_final_route_never_invalid(row_status):
    """Returned status must never be the old FINAL_ROUTE_* / ROUTED_* strings."""
    result = _status_for_final_route(row_status)
    assert not result.startswith("FINAL_ROUTE_"), f"Got invalid status {result!r}"
    assert not result.startswith("ROUTED_"), f"Got invalid status {result!r}"


# ---------------------------------------------------------------------------
# BabelSharkDBWriter.mark_babelshark_failed
# ---------------------------------------------------------------------------


def _make_db_writer():
    session = MagicMock()
    record = FileRecord(
        internal_id=42,
        global_artifact_id="gaid-test",
        current_file_path="/staging/slide.svs",
        canonical_path="/staging/slide.svs",
        status="intake_registered",
    )
    session.execute.return_value.scalar_one.return_value = record
    writer = BabelSharkDBWriter(session)
    writer._event_repo = MagicMock()
    writer._trigger_repo = MagicMock()
    return writer, session, record


def test_mark_babelshark_failed_sets_status():
    writer, session, record = _make_db_writer()
    writer.mark_babelshark_failed(
        file_record_internal_id=42,
        global_artifact_id="gaid-test",
        actual_path="/failed/2026-06-01/slide.svs",
        routing_status="failed",
    )
    assert record.status == "babelshark_failed"


def test_mark_babelshark_failed_updates_paths():
    writer, session, record = _make_db_writer()
    writer.mark_babelshark_failed(
        file_record_internal_id=42,
        global_artifact_id="gaid-test",
        actual_path="/failed/2026-06-01/slide.svs",
        routing_status="failed",
    )
    assert record.current_file_path == "/failed/2026-06-01/slide.svs"
    assert record.canonical_path == "/failed/2026-06-01/slide.svs"


def test_mark_babelshark_failed_no_path_update_when_empty():
    writer, session, record = _make_db_writer()
    record.current_file_path = "/staging/slide.svs"
    writer.mark_babelshark_failed(
        file_record_internal_id=42,
        global_artifact_id="gaid-test",
        actual_path=None,
        routing_status="failed",
    )
    # path must not be overwritten when actual_path is None
    assert record.current_file_path == "/staging/slide.svs"


def test_mark_babelshark_failed_emits_event():
    writer, session, record = _make_db_writer()
    writer.mark_babelshark_failed(
        file_record_internal_id=42,
        global_artifact_id="gaid-test",
        actual_path="/failed/2026-06-01/slide.svs",
        routing_status="failed",
    )
    writer._event_repo.append.assert_called_once()
    call_kwargs = writer._event_repo.append.call_args.kwargs
    assert call_kwargs["event_type"] == "babelshark.failed_routed"
    assert call_kwargs["event_payload"]["actual_path"] == "/failed/2026-06-01/slide.svs"
    assert call_kwargs["event_payload"]["routing_status"] == "failed"


def test_mark_babelshark_failed_does_not_enqueue_trigger():
    writer, session, record = _make_db_writer()
    writer.mark_babelshark_failed(
        file_record_internal_id=42,
        global_artifact_id="gaid-test",
        routing_status="failed",
    )
    writer._trigger_repo.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# BabelSharkStageRunner._FAILED_ROUTING_STATUSES membership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status,expected_failed", [
    ("failed", True),
    ("nan", True),
    ("error", True),
    ("success", False),
    ("research_success", False),
    ("dicom_renamed", False),
    ("", False),
    ("research_original", False),
])
def test_failed_routing_statuses_membership(status, expected_failed):
    result = status in BabelSharkStageRunner._FAILED_ROUTING_STATUSES
    assert result == expected_failed, (
        f"{status!r} in _FAILED_ROUTING_STATUSES → {result}, want {expected_failed}"
    )


# ---------------------------------------------------------------------------
# run_slide_id_generation return shape
# ---------------------------------------------------------------------------


def test_run_slide_id_generation_returns_tuple():
    """run_slide_id_generation must return a 3-tuple (slide_id, routing_status, output_path)."""
    import logging
    import tempfile
    from pathlib import Path

    runner = BabelSharkStageRunner(
        config={},
        log=logging.getLogger("test"),
        correlation_id="test-corr",
    )

    mock_cfg = {
        "metadata_excel_path": "",
        "final_output_dir": "/final",
    }

    with (
        patch(
            "pathoryx_enterprise.services.babelshark.stage_runner"
            ".BabelSharkStageRunner.run_slide_id_generation",
            return_value=("SID-001", "success", "/final/SID-001.svs"),
        ) as mock_sid,
    ):
        result = runner.run_slide_id_generation.__wrapped__ if hasattr(
            runner.run_slide_id_generation, "__wrapped__"
        ) else None

    # Direct shape assertion via a fresh call with heavy mocking
    with (
        patch(
            "pathoryx_enterprise.services.babelshark.core.slide_id_generator.run_pipeline"
        ),
        patch("pathoryx_enterprise.services.babelshark.stage_runner.get_session"),
        patch("pandas.read_excel", return_value=__import__("pandas").DataFrame()),
        patch(
            "pathoryx_enterprise.services.babelshark.stage_runner"
            ".BabelSharkStageRunner._create_step_run"
        ),
        patch(
            "pathoryx_enterprise.services.babelshark.stage_runner"
            ".BabelSharkStageRunner._emit_event"
        ),
        patch(
            "pathoryx_enterprise.services.babelshark.stage_runner"
            ".BabelSharkStageRunner._update_file_record_meta"
        ),
        patch(
            "pathoryx_enterprise.services.babelshark.stage_runner"
            ".stage_latency_seconds"
        ),
        patch(
            "pathoryx_enterprise.services.babelshark.stage_runner"
            ".files_processed_total"
        ),
    ):
        result = runner.run_slide_id_generation(
            mock_cfg, file_record_id=1, global_artifact_id="gaid-x", pipeline_run_id=1
        )

    assert isinstance(result, tuple), "run_slide_id_generation must return a tuple"
    assert len(result) == 3, "tuple must be (slide_id, routing_status, routing_output_path)"
    slide_id, routing_status, routing_output_path = result
    # With an empty Excel, all three should be falsy defaults
    assert slide_id is None
    assert routing_status == ""
    assert routing_output_path == ""
