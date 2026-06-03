"""
Unit tests for the SSE change-detection helpers in dashboard/sse.py.

These tests exercise poll_changes() and init_checkpoints() directly using
MagicMock sessions — no real database or HTTP server required.

Strategy for mocking multiple sequential execute() calls:
  Each call to session.execute() returns a fresh mock whose .scalar()
  returns a specific value.  We use side_effect with a list to control
  the return sequence in the exact order that poll_changes() issues queries:

  Query order (matches implementation in sse.py poll_changes):
    1.  SELECT MAX(ServiceTrigger.internal_id)          → trigger_max_id
    2.  SELECT COUNT WHERE status IN (pending, running) → trigger_active_count
    3.  SELECT MAX(PipelineEvent.event_id)              → event_max_id
    4.  SELECT MAX(FileRecord.internal_id)              → file_max_id
    5.  SELECT MAX(FileRecord.updated_at)               → file_max_updated
    6.  SELECT MAX(TechnicianChange.internal_id)        → recovery_max_id
    7.  SELECT MAX(RunnerRegistration.last_heartbeat_at)→ runner_max_heartbeat
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scalar_mock(value):
    """Return a mock that simulates session.execute(...).scalar() → value."""
    m = MagicMock()
    m.scalar.return_value = value
    return m


def _make_session(scalars: list) -> MagicMock:
    """Return a mock session whose execute() yields successive scalar values."""
    session = MagicMock()
    session.execute.side_effect = [_scalar_mock(v) for v in scalars]
    return session


# Default "nothing changed" scalars — same as checkpoints' defaults
_BASELINE_SCALARS = [
    5,     # trigger max_id
    2,     # trigger active count
    10,    # event max_id
    8,     # file max_id
    None,  # file max_updated
    3,     # recovery max_id
    None,  # runner max_heartbeat
]

_NOW = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
_LATER = datetime(2026, 6, 3, 12, 0, 5, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# SseCheckpoints
# ---------------------------------------------------------------------------

class TestSseCheckpoints:
    def test_defaults(self):
        from pathoryx_enterprise.services.dashboard.sse import SseCheckpoints

        cp = SseCheckpoints()
        assert cp.trigger_max_id == 0
        assert cp.trigger_active_count == 0
        assert cp.event_max_id == 0
        assert cp.file_max_id == 0
        assert cp.file_max_updated is None
        assert cp.recovery_max_id == 0
        assert cp.runner_max_heartbeat is None
        assert cp.initialized is False


# ---------------------------------------------------------------------------
# init_checkpoints
# ---------------------------------------------------------------------------

class TestInitCheckpoints:
    def test_populates_all_fields(self):
        from pathoryx_enterprise.services.dashboard.sse import init_checkpoints

        session = _make_session([5, 2, 10, 8, _NOW, 3, _LATER])
        cp = init_checkpoints(session)

        assert cp.initialized is True
        assert cp.trigger_max_id == 5
        assert cp.trigger_active_count == 2
        assert cp.event_max_id == 10
        assert cp.file_max_id == 8
        assert cp.file_max_updated == _NOW
        assert cp.recovery_max_id == 3
        assert cp.runner_max_heartbeat == _LATER

    def test_returns_initialized_cp_on_all_null(self):
        from pathoryx_enterprise.services.dashboard.sse import init_checkpoints

        session = _make_session([None, 0, None, None, None, None, None])
        cp = init_checkpoints(session)

        assert cp.initialized is True
        assert cp.trigger_max_id == 0
        assert cp.event_max_id == 0

    def test_partial_failure_still_initializes(self):
        """A query error on one table should not prevent initialization."""
        from pathoryx_enterprise.services.dashboard.sse import init_checkpoints
        from sqlalchemy.exc import OperationalError

        session = MagicMock()
        # First two calls succeed, third raises
        ok1 = _scalar_mock(5)
        ok2 = _scalar_mock(2)
        session.execute.side_effect = [
            ok1, ok2,
            OperationalError("", {}, Exception()),  # event query fails
            _scalar_mock(8), _scalar_mock(None),
            _scalar_mock(3), _scalar_mock(None),
        ]

        cp = init_checkpoints(session)
        assert cp.initialized is True
        assert cp.trigger_max_id == 5   # populated
        assert cp.event_max_id == 0     # default — query failed


# ---------------------------------------------------------------------------
# poll_changes — no changes
# ---------------------------------------------------------------------------

class TestPollChangesNoChanges:
    def _make_cp(self):
        from pathoryx_enterprise.services.dashboard.sse import SseCheckpoints

        return SseCheckpoints(
            initialized=True,
            trigger_max_id=5,
            trigger_active_count=2,
            event_max_id=10,
            file_max_id=8,
            file_max_updated=_NOW,
            recovery_max_id=3,
            runner_max_heartbeat=_NOW,
        )

    def test_no_events_when_nothing_changed(self):
        from pathoryx_enterprise.services.dashboard.sse import poll_changes

        session = _make_session([5, 2, 10, 8, _NOW, 3, _NOW])
        cp = self._make_cp()
        events = poll_changes(session, cp)
        assert events == []

    def test_checkpoints_unchanged(self):
        from pathoryx_enterprise.services.dashboard.sse import poll_changes

        session = _make_session([5, 2, 10, 8, _NOW, 3, _NOW])
        cp = self._make_cp()
        poll_changes(session, cp)
        assert cp.trigger_max_id == 5
        assert cp.event_max_id == 10


# ---------------------------------------------------------------------------
# poll_changes — changes detected
# ---------------------------------------------------------------------------

class TestPollChangesDetectsChanges:
    def _base_cp(self, **overrides):
        from pathoryx_enterprise.services.dashboard.sse import SseCheckpoints

        # Build via dict so callers can override any field without a "multiple
        # values for keyword argument" error.
        defaults = dict(
            initialized=True,
            trigger_max_id=5,
            trigger_active_count=2,
            event_max_id=10,
            file_max_id=8,
            file_max_updated=_NOW,
            recovery_max_id=3,
            runner_max_heartbeat=_NOW,
        )
        defaults.update(overrides)
        return SseCheckpoints(**defaults)

    def test_new_trigger_emits_queue_updated(self):
        from pathoryx_enterprise.services.dashboard.sse import poll_changes

        session = _make_session([6, 2, 10, 8, _NOW, 3, _NOW])  # trigger max_id advanced
        cp = self._base_cp()
        events = poll_changes(session, cp)
        types = [e['type'] for e in events]
        assert 'queue_updated' in types
        assert cp.trigger_max_id == 6

    def test_active_count_change_emits_queue_updated(self):
        from pathoryx_enterprise.services.dashboard.sse import poll_changes

        session = _make_session([5, 3, 10, 8, _NOW, 3, _NOW])  # active count 2→3
        cp = self._base_cp()
        events = poll_changes(session, cp)
        assert any(e['type'] == 'queue_updated' for e in events)

    def test_new_event_emits_pipeline_event_created(self):
        from pathoryx_enterprise.services.dashboard.sse import poll_changes

        session = _make_session([5, 2, 11, 8, _NOW, 3, _NOW])  # event_id 10→11
        cp = self._base_cp()
        events = poll_changes(session, cp)
        assert any(e['type'] == 'pipeline_event_created' for e in events)
        assert cp.event_max_id == 11

    def test_new_file_emits_file_record_updated(self):
        from pathoryx_enterprise.services.dashboard.sse import poll_changes

        session = _make_session([5, 2, 10, 9, _NOW, 3, _NOW])  # file_max_id 8→9
        cp = self._base_cp()
        events = poll_changes(session, cp)
        assert any(e['type'] == 'file_record_updated' for e in events)
        assert cp.file_max_id == 9

    def test_updated_at_advance_emits_file_record_updated(self):
        from pathoryx_enterprise.services.dashboard.sse import poll_changes

        session = _make_session([5, 2, 10, 8, _LATER, 3, _NOW])  # updated_at advanced
        cp = self._base_cp()
        events = poll_changes(session, cp)
        assert any(e['type'] == 'file_record_updated' for e in events)
        assert cp.file_max_updated == _LATER

    def test_new_recovery_emits_recovery_event_created(self):
        from pathoryx_enterprise.services.dashboard.sse import poll_changes

        session = _make_session([5, 2, 10, 8, _NOW, 4, _NOW])  # recovery 3→4
        cp = self._base_cp()
        events = poll_changes(session, cp)
        assert any(e['type'] == 'recovery_event_created' for e in events)
        assert cp.recovery_max_id == 4

    def test_runner_heartbeat_advance_emits_service_health_updated(self):
        from pathoryx_enterprise.services.dashboard.sse import poll_changes

        session = _make_session([5, 2, 10, 8, _NOW, 3, _LATER])  # heartbeat advanced
        cp = self._base_cp()
        events = poll_changes(session, cp)
        assert any(e['type'] == 'service_health_updated' for e in events)
        assert cp.runner_max_heartbeat == _LATER

    def test_multiple_changes_emit_multiple_events(self):
        from pathoryx_enterprise.services.dashboard.sse import poll_changes

        session = _make_session([6, 3, 11, 8, _NOW, 3, _NOW])  # trigger + event changed
        cp = self._base_cp()
        events = poll_changes(session, cp)
        types = [e['type'] for e in events]
        assert 'queue_updated' in types
        assert 'pipeline_event_created' in types

    def test_events_have_type_and_ts_fields(self):
        from pathoryx_enterprise.services.dashboard.sse import poll_changes

        session = _make_session([6, 2, 10, 8, _NOW, 3, _NOW])
        cp = self._base_cp()
        events = poll_changes(session, cp)
        for ev in events:
            assert 'type' in ev
            assert 'ts' in ev
            assert isinstance(ev['ts'], str)

    def test_runner_heartbeat_null_does_not_emit(self):
        """No runner heartbeat in DB → no service_health_updated event."""
        from pathoryx_enterprise.services.dashboard.sse import poll_changes

        session = _make_session([5, 2, 10, 8, _NOW, 3, None])  # no runner
        cp = self._base_cp(runner_max_heartbeat=None)
        events = poll_changes(session, cp)
        assert not any(e['type'] == 'service_health_updated' for e in events)


# ---------------------------------------------------------------------------
# poll_changes — uninitialised cp falls back silently
# ---------------------------------------------------------------------------

class TestPollChangesUninitialised:
    def test_uninitialised_cp_does_not_emit_events(self):
        from pathoryx_enterprise.services.dashboard.sse import SseCheckpoints, poll_changes

        session = _make_session([5, 2, 10, 8, _NOW, 3, _NOW])
        cp = SseCheckpoints(initialized=False)
        events = poll_changes(session, cp)
        assert events == []
        assert cp.initialized is True


# ---------------------------------------------------------------------------
# poll_changes — graceful degradation on DB errors
# ---------------------------------------------------------------------------

class TestPollChangesDegrades:
    def test_single_query_failure_does_not_suppress_other_events(self):
        """A DB error on one group should not prevent events from other groups."""
        from pathoryx_enterprise.services.dashboard.sse import SseCheckpoints, poll_changes
        from sqlalchemy.exc import OperationalError

        cp = SseCheckpoints(
            initialized=True,
            trigger_max_id=5, trigger_active_count=2,
            event_max_id=10,
            file_max_id=8, file_max_updated=_NOW,
            recovery_max_id=3,
            runner_max_heartbeat=_NOW,
        )

        session = MagicMock()
        # The trigger group's try/except wraps BOTH trigger queries together.
        # When the first execute() raises, the second execute() call (for
        # active count) is never reached — the exception jumps straight to the
        # except block.  So only 1 execute() call is consumed by the trigger
        # group, not 2.  The remaining calls go to the other groups in order.
        session.execute.side_effect = [
            OperationalError("", {}, Exception()),  # trigger max_id → raises
            # trigger active count is never called (exception short-circuits)
            _scalar_mock(11),  # event max_id: 10 → 11 → should emit
            _scalar_mock(8), _scalar_mock(_NOW),   # file max_id, file updated_at
            _scalar_mock(3),                        # recovery max_id
            _scalar_mock(_NOW),                     # runner max_heartbeat
        ]
        events = poll_changes(session, cp)
        # Even though the trigger query failed, pipeline_event should appear
        assert any(e['type'] == 'pipeline_event_created' for e in events)
