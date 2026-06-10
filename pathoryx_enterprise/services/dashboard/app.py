"""
FastAPI application for the Palantir Dashboard.

All endpoints are read-only. Each endpoint degrades gracefully on DB errors
(returns empty/zero data with HTTP 200) so the dashboard stays up even when
tables are empty or a query fails unexpectedly.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Generator, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pathoryx_enterprise.db.session import get_session

from . import sse as _sse

from . import queries as q
from .actions import ActionError, execute_technician_rename, update_review_state, validate_filename_structured
from .schemas import (
    ArtifactInvestigationResponse,
    AuditTrailResponse,
    ConversionResultSummary,
    CoreOverviewResponse,
    DbHealthResponse,
    EnvironmentConfig,
    OperationalIncident,
    OperationalIncidentsResponse,
    RecoveryStatsResponse,
    ScannerActivityItem,
    ScannerActivityResponse,
    ServiceHealthExtended,
    ServiceHealthExtendedResponse,
    StainDistributionItem,
    StainDistributionResponse,
    StorageScannerItem,
    StorageStatsResponse,
    StuckTriggerItem,
    StuckTriggersResponse,
    DailyUploadCount,
    UploadVelocityResponse,
    EventItem,
    EventListResponse,
    ExtractionResultSummary,
    FailedSlideItem,
    FailedTriggerItem,
    FailureGroup,
    FilenameValidationRequest,
    FilenameValidationResponse,
    FailuresResponse,
    PathLineageItem,
    QueueMetric,
    RetryChainItem,
    LabelPreviewResponse,
    MonitoredFileItem,
    MonitoredFilesResponse,
    OpenFolderResponse,
    OverviewResponse,
    QCResultSummary,
    QueueRow,
    QueueStatusResponse,
    RecoveryEventItem,
    RecoveryItem,
    RecoveryResponse,
    ReviewStateUpdateRequest,
    ReviewStateUpdateResponse,
    RunnerItem,
    RunnerStatusCounts,
    ServicesHealthResponse,
    SlideDetailResponse,
    SlideItem,
    SlideListResponse,
    SlideStatusCounts,
    TechnicianRenameRequest,
    TechnicianRenameResponse,
    TriggerItem,
    TriggerStatusCounts,
    UploadResultSummary,
    ScannerConfig,
    ScannerFleetResponse,
    ScannerSummaryItem,
    ScannerSummaryResponse,
    UploadFilterOptions,
    UploadIngestRequest,
    UploadIngestResponse,
    UploadMetrics,
    UploadPriorityRequest,
    UploadQueueItem,
    UploadQueueResponse,
    UploadQueueUpdateRequest,
    ValidationComponent,
    ValidationIssue,
    WatchFolderSummary,
    WatchFoldersResponse,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SSE stream constants
# ---------------------------------------------------------------------------

_SSE_POLL_INTERVAL: float = 5.0   # seconds between DB change-detection polls
_SSE_HEARTBEAT_INTERVAL: float = 25.0  # seconds between SSE keepalive comments


# ---------------------------------------------------------------------------
# SSE generator — module-level so tests can import it directly
# ---------------------------------------------------------------------------

async def _make_event_stream(request: Request) -> AsyncGenerator[bytes, None]:
    """
    Async generator that yields SSE-formatted byte strings.

    Lifecycle:
      1. Run init_checkpoints() in the thread-pool to establish a baseline
         without emitting any events.
      2. Loop: sleep _SSE_POLL_INTERVAL, then run poll_changes() in the
         thread-pool.  If changes were detected, yield the corresponding SSE
         event lines.  Emit a keepalive comment every _SSE_HEARTBEAT_INTERVAL
         seconds to prevent proxy / browser timeouts.
      3. Break when the client disconnects (request.is_disconnected()).

    All blocking DB calls are offloaded to asyncio's default ThreadPoolExecutor
    via run_in_executor() so the FastAPI event loop is never blocked.

    Wire format (RFC 8895 / HTML EventSource):
        event: queue_updated\\n
        data: {"type": "queue_updated", "ts": "..."}\\n
        \\n
    """
    loop = asyncio.get_running_loop()
    cp = _sse.SseCheckpoints()

    # ── Initialise checkpoints without emitting ───────────────────────────────
    def _init() -> _sse.SseCheckpoints:
        with get_session() as session:
            return _sse.init_checkpoints(session)

    try:
        cp = await loop.run_in_executor(None, _init)
    except Exception as exc:
        logger.warning("SSE: checkpoint init failed (%s) — starting blind", exc)
        cp = _sse.SseCheckpoints(initialized=True)

    last_heartbeat = loop.time()

    while True:
        # ── Client-disconnect check ───────────────────────────────────────────
        if await request.is_disconnected():
            logger.debug("SSE: client disconnected — closing generator")
            break

        await asyncio.sleep(_SSE_POLL_INTERVAL)

        # ── Keepalive comment ─────────────────────────────────────────────────
        now = loop.time()
        if now - last_heartbeat >= _SSE_HEARTBEAT_INTERVAL:
            yield b": heartbeat\n\n"
            last_heartbeat = now

        # ── DB poll in thread pool ────────────────────────────────────────────
        def _poll() -> list[dict]:
            with get_session() as session:
                return _sse.poll_changes(session, cp)

        try:
            events = await loop.run_in_executor(None, _poll)
        except Exception as exc:
            logger.warning("SSE: poll error: %s", exc)
            continue

        for ev in events:
            line = f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
            yield line.encode()


# ---------------------------------------------------------------------------
# DB dependency — allows TestClient to override with an in-memory session.
# ---------------------------------------------------------------------------


def get_db() -> Generator[Session, None, None]:
    with get_session() as session:
        yield session


DbDep = Annotated[Session, Depends(get_db)]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


import functools
from .scanner_fleet import ScannerFleet
from starlette.middleware.cors import CORSMiddleware


@functools.lru_cache(maxsize=1)
def _load_scanner_fleet() -> ScannerFleet:
    """
    Load the scanner fleet config once and cache it.
    Returned object is immutable so cache sharing is safe.
    Gracefully returns an empty fleet if no config is found.
    """
    return ScannerFleet.load_default()


@functools.lru_cache(maxsize=1)
def _load_recovery_config() -> dict:
    """
    Load recovery_sentry.yaml for validation hints (timestamp policy).
    Cached after first load; returns {} on any failure.
    Safe to call on every keystroke — never hits the filesystem more than once.
    """
    import os
    import yaml

    path_str = (
        os.environ.get("RECOVERY_SENTRY_CONFIG")
        or "configs/recovery_sentry.yaml"
    )
    try:
        with open(path_str) as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.debug("_load_recovery_config: %s", exc)
        return {}


def _load_babelshark_config() -> dict:
    """Load babelshark YAML config. Returns {} on any failure."""
    import os
    import yaml

    config_path_str = (
        os.environ.get("BABELSHARK_CONFIG_PATH")
        or os.environ.get("BABELSHARK_CONFIG")
    )
    if not config_path_str:
        for candidate in ("configs/babelshark_config.yaml", "./configs/babelshark_config.yaml"):
            if Path(candidate).exists():
                config_path_str = candidate
                break
    if not config_path_str:
        return {}
    try:
        with open(config_path_str) as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.debug("_load_babelshark_config: %s", exc)
        return {}


def _build_label_search_dirs(cfg: dict) -> list[Path]:
    """
    Return an ordered list of directories to search for label-crop images.

    Priority:
      1. Configured run_output_dir → date subdirs (newest first) → label_crops/
      2. Configured run_output_dir → date subdirs → failed_datamatrix/
      3. Configured label_crops_dir (flat)
      4. Configured label_root_dir (legacy)
      5. data/run_output/ relative to CWD → same pattern  (Linux/dev fallback)
      6. data/label_crops/ and data/labels/ relative to CWD

    The double pass (config + CWD fallback) means this works on both Windows
    (configured paths exist) and Linux dev (CWD-relative data/ exists).
    """
    dirs: list[Path] = []

    def _add_run_output_subdirs(base: Path) -> None:
        if not base.exists():
            return
        try:
            date_dirs = sorted(
                [d for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")],
                reverse=True,  # most recent first
            )
        except PermissionError:
            return
        for dd in date_dirs:
            dirs.append(dd / "label_crops")
            dirs.append(dd / "failed_datamatrix")

    # 1-2. Configured run_output_dir
    if cfg.get("run_output_dir"):
        _add_run_output_subdirs(Path(cfg["run_output_dir"]))

    # 3. Configured label_crops_dir
    if cfg.get("label_crops_dir"):
        dirs.append(Path(cfg["label_crops_dir"]))

    # 4. Configured label_root_dir
    if cfg.get("label_root_dir"):
        dirs.append(Path(cfg["label_root_dir"]))

    # 5. CWD-relative fallback (Linux dev / CI)
    _add_run_output_subdirs(Path("data/run_output"))

    # 6. Flat fallback dirs
    dirs.extend([Path("data/label_crops"), Path("data/labels")])

    return dirs


def _label_allowed_roots(cfg: dict) -> list[Path]:
    """
    Compute the set of roots within which label images may be served.
    Prevents path traversal: any resolved candidate outside these roots is rejected.
    """
    roots: list[Path] = []
    # data/ relative to CWD is always permitted (covers watch folders + output dirs on Linux)
    cwd_data = Path("data").resolve()
    if cwd_data.exists():
        roots.append(cwd_data)
    # Configured output roots
    for key in ("run_output_dir", "label_crops_dir", "label_root_dir"):
        if cfg.get(key):
            p = Path(cfg[key])
            if p.exists():
                roots.append(p.resolve())
    # Also add configured watch_folders from RecoverySentry so WSI files in those
    # folders are readable on Windows where data/ may not exist
    try:
        from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings
        rs = RecoverySentrySettings()
        for wf in rs.watch_folders:
            if wf.exists():
                roots.append(wf.resolve())
        if rs.final_destination and rs.final_destination.exists():
            roots.append(rs.final_destination.resolve())
    except Exception:
        pass
    # Absolute fallback so at least something is in the list
    if not roots:
        roots.append(Path("/").resolve())
    return roots


def _label_cache_dir(cfg: dict) -> Path:
    """
    Return a writable directory for caching on-the-fly extracted label images.

    Priority:
      1. Configured label_crops_dir (if parent exists)
      2. data/run_output/{today}/label_crops/
      3. data/label_crops/
    """
    import datetime
    if cfg.get("label_crops_dir"):
        d = Path(cfg["label_crops_dir"])
        if d.parent.exists():
            return d
    today = datetime.date.today().isoformat()
    run_out_crops = Path("data/run_output") / today / "label_crops"
    if run_out_crops.parent.parent.exists():
        return run_out_crops
    return Path("data/label_crops")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Palantir Dashboard API",
        description="Read-only observability API for the Palantir Enterprise pipeline.",
        version="1.0.0",
        docs_url="/dashboard/docs",
        redoc_url="/dashboard/redoc",
        openapi_url="/dashboard/openapi.json",
    )

    # Allow the Vite dev servers (ports 5173 and 5174) to reach this API
    # without CORS errors.  Same-origin production requests are unaffected.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "Accept"],
    )

    # ------------------------------------------------------------------
    # GET /dashboard/api/overview
    # ------------------------------------------------------------------
    @app.get("/dashboard/api/overview", response_model=OverviewResponse)
    def overview(db: DbDep) -> OverviewResponse:
        now = datetime.now(tz=timezone.utc)
        since_24h = now - timedelta(hours=24)

        try:
            slide_counts = q.count_slides_by_status(db)
        except SQLAlchemyError as exc:
            logger.warning("overview: slide count query failed: %s", exc)
            slide_counts = {}

        try:
            trigger_counts = q.count_triggers_by_status(db)
        except SQLAlchemyError as exc:
            logger.warning("overview: trigger count query failed: %s", exc)
            trigger_counts = {}

        try:
            runner_counts = q.count_runners_by_status(db)
        except SQLAlchemyError as exc:
            logger.warning("overview: runner count query failed: %s", exc)
            runner_counts = {}

        try:
            events_24h = q.count_events_since(db, since_24h)
        except SQLAlchemyError as exc:
            logger.warning("overview: events count query failed: %s", exc)
            events_24h = 0

        known_trigger_statuses = {"pending", "running", "failed", "completed"}
        other_triggers: dict[str, int] = {
            k: v for k, v in trigger_counts.items() if k not in known_trigger_statuses
        }

        active_runners = runner_counts.get("active", 0)
        stale_runners = runner_counts.get("stale", 0) + runner_counts.get("crashed", 0)
        other_runners = {
            k: v for k, v in runner_counts.items() if k not in {"active", "stale", "crashed"}
        }

        return OverviewResponse(
            as_of=now,
            slides=SlideStatusCounts(
                total=sum(slide_counts.values()),
                by_status=slide_counts,
            ),
            triggers=TriggerStatusCounts(
                pending=trigger_counts.get("pending", 0),
                running=trigger_counts.get("running", 0),
                failed=trigger_counts.get("failed", 0),
                completed=trigger_counts.get("completed", 0),
            ),
            runners=RunnerStatusCounts(
                active=active_runners,
                stale=stale_runners,
                other=other_runners,
            ),
            events_last_24h=events_24h,
        )

    # ------------------------------------------------------------------
    # GET /dashboard/api/slides
    # ------------------------------------------------------------------
    @app.get("/dashboard/api/slides", response_model=SlideListResponse)
    def slides_list(
        db: DbDep,
        status: Annotated[Optional[str], Query(description="Filter by pipeline status")] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> SlideListResponse:
        try:
            total, items = q.list_slides(db, status=status, page=page, page_size=page_size)
        except SQLAlchemyError as exc:
            logger.warning("slides_list: query failed: %s", exc)
            return SlideListResponse(total=0, page=page, page_size=page_size, items=[])

        return SlideListResponse(
            total=total,
            page=page,
            page_size=page_size,
            items=[SlideItem.model_validate(r) for r in items],
        )

    # ------------------------------------------------------------------
    # GET /dashboard/api/slides/{global_artifact_id}
    # ------------------------------------------------------------------
    @app.get("/dashboard/api/slides/{global_artifact_id}", response_model=SlideDetailResponse)
    def slide_detail(db: DbDep, global_artifact_id: str) -> SlideDetailResponse:
        try:
            record = q.get_slide_by_artifact_id(db, global_artifact_id)
        except SQLAlchemyError as exc:
            logger.warning("slide_detail: file_record query failed: %s", exc)
            raise HTTPException(status_code=503, detail="Database query failed") from exc

        if record is None:
            raise HTTPException(status_code=404, detail="Slide not found")

        rid = record.internal_id

        qc = None
        try:
            qc_orm = q.get_latest_qc_result(db, rid)
            if qc_orm is not None:
                qc = QCResultSummary.model_validate(qc_orm)
        except SQLAlchemyError as exc:
            logger.warning("slide_detail: qc query failed: %s", exc)

        conversion = None
        try:
            conv_orm = q.get_latest_conversion_result(db, rid)
            if conv_orm is not None:
                conversion = ConversionResultSummary.model_validate(conv_orm)
        except SQLAlchemyError as exc:
            logger.warning("slide_detail: conversion query failed: %s", exc)

        upload = None
        try:
            upl_orm = q.get_latest_upload_result(db, rid)
            if upl_orm is not None:
                upload = UploadResultSummary.model_validate(upl_orm)
        except SQLAlchemyError as exc:
            logger.warning("slide_detail: upload query failed: %s", exc)

        events: list[EventItem] = []
        try:
            ev_orm = q.list_events_for_artifact(db, global_artifact_id, limit=50)
            events = [EventItem.model_validate(e) for e in ev_orm]
        except SQLAlchemyError as exc:
            logger.warning("slide_detail: events query failed: %s", exc)

        trigger_rows: list[TriggerItem] = []
        try:
            raw_triggers = q.list_triggers_for_artifact(db, global_artifact_id)
            trigger_rows = [TriggerItem.model_validate(r) for r in raw_triggers]
        except SQLAlchemyError as exc:
            logger.warning("slide_detail: triggers query failed: %s", exc)

        recovery_rows: list[RecoveryEventItem] = []
        try:
            raw_recovery = q.list_recovery_for_artifact(db, global_artifact_id)
            recovery_rows = [RecoveryEventItem.model_validate(r) for r in raw_recovery]
        except SQLAlchemyError as exc:
            logger.warning("slide_detail: recovery query failed: %s", exc)

        extraction: ExtractionResultSummary | None = None
        try:
            ext_orm = q.get_extraction_result(db, rid)
            if ext_orm is not None:
                extraction = ExtractionResultSummary.model_validate(ext_orm)
        except SQLAlchemyError as exc:
            logger.warning("slide_detail: extraction query failed: %s", exc)

        return SlideDetailResponse(
            file_record=SlideItem.model_validate(record),
            qc_result=qc,
            conversion_result=conversion,
            upload_result=upload,
            recent_events=events,
            triggers=trigger_rows,
            recovery_events=recovery_rows,
            extraction_result=extraction,
        )

    # ------------------------------------------------------------------
    # GET /dashboard/api/artifacts/{global_artifact_id}/investigation
    # Phase 9 — consolidated investigation endpoint
    # ------------------------------------------------------------------
    @app.get(
        "/dashboard/api/artifacts/{global_artifact_id}/investigation",
        response_model=ArtifactInvestigationResponse,
        summary="Full artifact investigation bundle",
        description=(
            "Returns all artifact data in one optimised query: triggers, events, "
            "recovery history, stage results plus server-computed intelligence layers "
            "(retry chains, queue metrics, failure groups, path lineage)."
        ),
    )
    def artifact_investigation(
        db: DbDep,
        global_artifact_id: str,
        events_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> ArtifactInvestigationResponse:
        try:
            bundle = q.get_artifact_investigation(
                db,
                global_artifact_id,
                events_limit=events_limit,
            )
        except SQLAlchemyError as exc:
            logger.warning("artifact_investigation: query failed: %s", exc)
            raise HTTPException(status_code=503, detail="Database query failed") from exc

        if bundle is None:
            raise HTTPException(status_code=404, detail="Artifact not found")

        fr = bundle["file_record"]

        def _qc():
            row = bundle["qc_result"]
            return QCResultSummary.model_validate(row) if row else None

        def _conv():
            row = bundle["conversion_result"]
            return ConversionResultSummary.model_validate(row) if row else None

        def _upl():
            row = bundle["upload_result"]
            return UploadResultSummary.model_validate(row) if row else None

        def _ext():
            row = bundle["extraction_result"]
            return ExtractionResultSummary.model_validate(row) if row else None

        return ArtifactInvestigationResponse(
            file_record=SlideItem.model_validate(fr),
            qc_result=_qc(),
            conversion_result=_conv(),
            upload_result=_upl(),
            extraction_result=_ext(),
            triggers=[TriggerItem.model_validate(t) for t in bundle["triggers"]],
            recovery_events=[RecoveryEventItem.model_validate(r) for r in bundle["recovery_events"]],
            recent_events=[EventItem.model_validate(e) for e in bundle["events"]],
            events_total=bundle["events_total"],
            retry_chains=[RetryChainItem(**rc) for rc in bundle["retry_chains"]],
            queue_metrics=[QueueMetric(**qm) for qm in bundle["queue_metrics"]],
            failure_groups=[FailureGroup(**fg) for fg in bundle["failure_groups"]],
            path_lineage=[PathLineageItem(**pl) for pl in bundle["path_lineage"]],
        )

    # ------------------------------------------------------------------
    # GET /dashboard/api/events/recent
    # ------------------------------------------------------------------
    @app.get("/dashboard/api/events/recent", response_model=EventListResponse)
    def events_recent(
        db: DbDep,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> EventListResponse:
        try:
            rows = q.list_recent_events(db, limit=limit)
        except SQLAlchemyError as exc:
            logger.warning("events_recent: query failed: %s", exc)
            return EventListResponse(items=[], count=0)

        items = [EventItem.model_validate(r) for r in rows]
        return EventListResponse(items=items, count=len(items))

    # ------------------------------------------------------------------
    # GET /dashboard/api/queues
    # ------------------------------------------------------------------
    @app.get("/dashboard/api/queues", response_model=QueueStatusResponse)
    def queues(db: DbDep) -> QueueStatusResponse:
        try:
            rows = q.get_queue_status(db)
        except SQLAlchemyError as exc:
            logger.warning("queues: query failed: %s", exc)
            return QueueStatusResponse(queues=[], total_pending=0, total_failed=0)

        queue_rows = [QueueRow(**r) for r in rows]
        return QueueStatusResponse(
            queues=queue_rows,
            total_pending=sum(r.pending for r in queue_rows),
            total_failed=sum(r.failed for r in queue_rows),
        )

    # ------------------------------------------------------------------
    # GET /dashboard/api/recovery
    # ------------------------------------------------------------------
    @app.get("/dashboard/api/recovery", response_model=RecoveryResponse)
    def recovery(
        db: DbDep,
        review_status: Annotated[
            Optional[str],
            Query(description="Filter by review_status (e.g. detected, requeued, dismissed)"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> RecoveryResponse:
        try:
            total, items = q.list_recovery_items(db, review_status=review_status, limit=limit)
        except SQLAlchemyError as exc:
            logger.warning("recovery: query failed: %s", exc)
            return RecoveryResponse(items=[], total=0, by_status={})

        # Per-status counts are fetched separately so they are always global
        # (unaffected by the review_status filter applied to the items list).
        status_counts: dict[str, int] = {}
        try:
            status_counts = q.count_recovery_by_status(db)
        except SQLAlchemyError as exc:
            logger.warning("recovery: status count query failed: %s", exc)

        return RecoveryResponse(
            items=[RecoveryItem.model_validate(r) for r in items],
            total=total,
            by_status=status_counts,
        )

    # ------------------------------------------------------------------
    # GET /dashboard/api/failures
    # ------------------------------------------------------------------
    @app.get("/dashboard/api/failures", response_model=FailuresResponse)
    def failures(
        db: DbDep,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> FailuresResponse:
        failed_slides: list[FailedSlideItem] = []
        try:
            slide_rows = q.list_failed_slides(db, limit=limit)
            failed_slides = [FailedSlideItem.model_validate(r) for r in slide_rows]
        except SQLAlchemyError as exc:
            logger.warning("failures: failed_slides query failed: %s", exc)

        failed_triggers: list[FailedTriggerItem] = []
        try:
            trigger_rows = q.list_failed_triggers(db, limit=limit)
            failed_triggers = [FailedTriggerItem.model_validate(r) for r in trigger_rows]
        except SQLAlchemyError as exc:
            logger.warning("failures: failed_triggers query failed: %s", exc)

        # Collect unique non-null artifact IDs across slides and triggers, then
        # check which have recovery events in a single IN query.
        all_artifact_ids = list({
            gaid
            for gaid in (
                [s.global_artifact_id for s in failed_slides]
                + [t.global_artifact_id for t in failed_triggers]
            )
            if gaid is not None
        })
        recovery_artifact_ids: list[str] = []
        try:
            recovery_artifact_ids = q.list_artifact_ids_with_recovery(db, all_artifact_ids)
        except SQLAlchemyError as exc:
            logger.warning("failures: recovery check query failed: %s", exc)

        return FailuresResponse(
            failed_slides=failed_slides,
            failed_triggers=failed_triggers,
            artifact_ids_with_recovery=recovery_artifact_ids,
        )

    # ------------------------------------------------------------------
    # GET /dashboard/api/stream   (Server-Sent Events)
    # ------------------------------------------------------------------
    @app.get(
        "/dashboard/api/stream",
        summary="Server-Sent Events stream for live dashboard updates",
        response_description="text/event-stream — one SSE event per detected change",
    )
    async def event_stream(request: Request) -> StreamingResponse:
        """
        Lightweight SSE endpoint for the operations dashboard.

        Emits a named event whenever a change is detected in one of the
        monitored pipeline tables.  The frontend uses this to invalidate the
        corresponding React Query caches so the UI refreshes within ~5 s of
        a pipeline state change, without waiting for the next 30 s poll.

        Event types:
            queue_updated          — ServiceTrigger rows added or active count changed
            pipeline_event_created — new PipelineEvent row
            file_record_updated    — new FileRecord or updated_at advanced
            recovery_event_created — new TechnicianChange row
            service_health_updated — RunnerRegistration.last_heartbeat_at advanced

        Payload (all events):
            {"type": "<event_type>", "ts": "<ISO-8601 UTC>"}

        Keepalive: a ": heartbeat" SSE comment is sent every ~25 s.
        The endpoint degrades gracefully — a DB failure on one poll cycle is
        logged and skipped; the connection remains open.

        Polling fallback: existing React Query refetchInterval polling is NOT
        removed.  SSE provides faster invalidation; polling provides resilience.
        """
        return StreamingResponse(
            _make_event_stream(request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",   # disable nginx/gunicorn output buffering
                "Connection": "keep-alive",
            },
        )

    # ------------------------------------------------------------------
    # GET /dashboard/api/recovery/watch-folders
    # ------------------------------------------------------------------
    @app.get("/dashboard/api/recovery/watch-folders", response_model=WatchFoldersResponse)
    def recovery_watch_folders(db: DbDep) -> WatchFoldersResponse:
        try:
            rows = q.get_watch_folder_stats(db)
        except Exception as exc:
            logger.warning("recovery_watch_folders: query failed: %s", exc)
            rows = []

        # Merge configured folder list so empty folders still appear
        folders_by_label = {r["folder_label"]: r for r in rows}
        result_folders: list[WatchFolderSummary] = []

        # Try to surface configured paths from RecoverySentrySettings
        configured_labels: list[str] = []
        try:
            from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings
            rs = RecoverySentrySettings()
            for fp in rs.watch_folders:
                lbl = fp.name
                configured_labels.append(lbl)
                if lbl not in folders_by_label:
                    folders_by_label[lbl] = {
                        "folder_label": lbl,
                        "folder_path": str(fp),
                        "total_files": 0,
                        "recently_changed": 0,
                        "awaiting_review": 0,
                        "auto_recovered": 0,
                        "last_scan_time": None,
                    }
        except Exception:
            pass

        order = configured_labels or list(folders_by_label.keys())
        seen: set[str] = set()
        for lbl in order + list(folders_by_label.keys()):
            if lbl in seen:
                continue
            seen.add(lbl)
            r = folders_by_label.get(lbl, {})
            result_folders.append(
                WatchFolderSummary(
                    label=lbl,
                    path=r.get("folder_path"),
                    total_files=r.get("total_files", 0),
                    recently_changed=r.get("recently_changed", 0),
                    awaiting_review=r.get("awaiting_review", 0),
                    auto_recovered=r.get("auto_recovered", 0),
                    last_scan_time=r.get("last_scan_time"),
                )
            )

        return WatchFoldersResponse(
            folders=result_folders,
            as_of=datetime.now(tz=timezone.utc),
        )

    # ------------------------------------------------------------------
    # GET /dashboard/api/recovery/files
    # ------------------------------------------------------------------
    @app.get("/dashboard/api/recovery/files", response_model=MonitoredFilesResponse)
    def recovery_files(
        db: DbDep,
        folder_type: Annotated[Optional[str], Query(description="failed | suspicious | manual_review")] = None,
        review_status: Annotated[Optional[str], Query()] = None,
        recovery_status: Annotated[Optional[str], Query()] = None,
        search: Annotated[Optional[str], Query(description="Filename substring search")] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> MonitoredFilesResponse:
        try:
            total, items = q.list_monitored_files(
                db,
                folder_type=folder_type,
                review_status=review_status,
                recovery_status=recovery_status,
                search=search,
                limit=limit,
            )
        except Exception as exc:
            logger.warning("recovery_files: query failed: %s", exc)
            return MonitoredFilesResponse(total=0, items=[])

        # Build watch-root-by-label map for relative path computation
        watch_root_by_label: dict[str, Path] = {}
        try:
            from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings
            rs = RecoverySentrySettings()
            watch_root_by_label = {fp.name: fp.resolve() for fp in rs.watch_folders}
        except Exception:
            pass

        def _enrich(item: dict) -> dict:
            label = item.get("folder_label", "")
            fp_str = item.get("folder_path") or ""
            rel = ""
            exists = True
            if fp_str and label in watch_root_by_label:
                try:
                    rel = str(Path(fp_str).relative_to(watch_root_by_label[label]))
                    if rel == ".":
                        rel = ""
                except ValueError:
                    rel = ""
            if fp_str:
                exists = Path(fp_str).exists()
            return {**item, "relative_folder_path": rel, "folder_exists": exists}

        return MonitoredFilesResponse(
            total=total,
            items=[MonitoredFileItem(**_enrich(item)) for item in items],
        )

    # ------------------------------------------------------------------
    # POST /dashboard/api/recovery/files/{file_id}/technician-rename
    # ------------------------------------------------------------------
    @app.post(
        "/dashboard/api/recovery/files/{file_id}/technician-rename",
        response_model=TechnicianRenameResponse,
    )
    def technician_rename(
        db: DbDep,
        file_id: int,
        body: TechnicianRenameRequest,
    ) -> TechnicianRenameResponse:
        if not body.confirm:
            raise HTTPException(
                status_code=422,
                detail="confirm must be true to execute a rename",
            )

        snapshot = q.get_monitored_file(db, file_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Monitored file not found")

        try:
            from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings
            settings = RecoverySentrySettings()
        except Exception as exc:
            logger.error("technician_rename: could not load RecoverySentrySettings: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="RecoverySentry configuration unavailable — cannot validate rename target",
            ) from exc

        try:
            result = execute_technician_rename(
                snapshot=snapshot,
                proposed_filename=body.proposed_filename,
                technician_note=body.technician_note,
                watch_folders=settings.watch_folders,
                settings=settings,
            )
        except ActionError as exc:
            return TechnicianRenameResponse(
                outcome="validation_failed",
                validation_error=str(exc),
            )
        except Exception as exc:
            logger.error("technician_rename: unexpected error: %s", exc)
            raise HTTPException(status_code=500, detail="Rename operation failed") from exc

        return TechnicianRenameResponse(**result)

    # ------------------------------------------------------------------
    # GET /dashboard/api/recovery/files/{file_id}/label-preview
    # ------------------------------------------------------------------
    @app.get(
        "/dashboard/api/recovery/files/{file_id}/label-preview",
        response_model=LabelPreviewResponse,
    )
    def label_preview(db: DbDep, file_id: int) -> LabelPreviewResponse:
        try:
            data = q.get_label_preview_data(db, file_id)
        except Exception as exc:
            logger.warning("label_preview: query failed: %s", exc)
            data = {
                "file_id": file_id, "filename": None,
                "available": False, "unavailable_reason": "query_failed",
            }
        return LabelPreviewResponse(**data)

    # ------------------------------------------------------------------
    # POST /dashboard/api/recovery/validate-filename
    # ------------------------------------------------------------------
    @app.post(
        "/dashboard/api/recovery/validate-filename",
        response_model=FilenameValidationResponse,
    )
    def validate_filename(body: FilenameValidationRequest) -> FilenameValidationResponse:
        """
        Validate a proposed filename against the Palantir slide ID rules.

        Safe to call on every keystroke — no filesystem or DB side-effects.
        Returns structured components and any stain-canonical normalized form.
        """
        rec_cfg = _load_recovery_config()
        config_requires_timestamp = not rec_cfg.get("recovery", {}).get(
            "add_timestamp_if_missing", True
        )

        result = validate_filename_structured(
            body.filename,
            original_extension=body.original_extension,
            config_requires_timestamp=config_requires_timestamp,
        )
        components = None
        if result["components"]:
            c = result["components"]
            components = ValidationComponent(
                case_id=c.get("case_id"),
                pot=c.get("pot"),
                block=c.get("block"),
                section=c.get("section"),
                stain=c.get("stain"),
                timestamp=c.get("timestamp"),
                extension=c.get("extension"),
            )
        return FilenameValidationResponse(
            filename=result["filename"],
            classification=result["classification"],
            components=components,
            errors=[ValidationIssue(**e) for e in result["errors"]],
            warnings=[ValidationIssue(**w) for w in result["warnings"]],
            suggested_correction=result.get("suggested_correction"),
            normalized_filename=result.get("normalized_filename"),
        )

    # ------------------------------------------------------------------
    # PATCH /dashboard/api/recovery/changes/{change_id}/review-state
    # ------------------------------------------------------------------
    @app.patch(
        "/dashboard/api/recovery/changes/{change_id}/review-state",
        response_model=ReviewStateUpdateResponse,
    )
    def update_change_review_state(
        change_id: int,
        body: ReviewStateUpdateRequest,
    ) -> ReviewStateUpdateResponse:
        """
        Transition a TechnicianChange to a new review_status.

        Enforces the allowed transition table.  Each transition emits an
        immutable PipelineEvent so the full history is auditable.
        """
        try:
            result = update_review_state(
                change_id=change_id,
                new_status=body.review_status,
                technician_note=body.technician_note,
            )
        except ActionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("update_change_review_state: %s", exc)
            raise HTTPException(status_code=500, detail="Review state update failed") from exc

        return ReviewStateUpdateResponse(**result)

    # ------------------------------------------------------------------
    # GET /dashboard/api/recovery/files/{file_id}/label-image
    # ------------------------------------------------------------------
    @app.get("/dashboard/api/recovery/files/{file_id}/label-image")
    def label_image(file_id: int, db: DbDep):
        """
        Serve the label-crop image for a watched folder file.

        Source resolution order:
          1. Pre-generated label crop from run_output_dir/{date}/label_crops/ (newest first)
          2. Pre-generated crop from failed_datamatrix/ subdirs
          3. Configured label_crops_dir / label_root_dir (flat, legacy)
          4. CWD fallback: data/run_output/{date}/label_crops/, data/label_crops/
          5. On-the-fly extraction from the original WSI embedded label image
             (OpenSlide associated_images — reads ~50 KB, NOT the full 10 GB scan)
             → cached to data/label_crops/{stem}.png for future requests
          6. 404 with structured reason

        All filesystem paths are validated with is_path_safe() before use.
        """
        from fastapi.responses import FileResponse
        from pathoryx_enterprise.utils.path_validation import is_path_safe, sanitize_filename
        from .wsi_label_extractor import extract_wsi_label_to_cache

        snapshot = q.get_monitored_file(db, file_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="File not found in watched folders")

        raw_stem = Path(snapshot.filename).stem
        safe_stem = sanitize_filename(raw_stem)
        if not safe_stem:
            raise HTTPException(status_code=400, detail="Invalid filename stem")

        cfg = _load_babelshark_config()
        search_dirs = _build_label_search_dirs(cfg)
        allowed_roots = _label_allowed_roots(cfg)

        def _serve(path: Path, source_header: str) -> FileResponse:
            suffix = path.suffix.lower()
            media = "image/jpeg" if suffix in (".jpg", ".jpeg") else f"image/{suffix.lstrip('.')}"
            return FileResponse(
                path=str(path),
                media_type=media,
                headers={
                    "Cache-Control": "max-age=3600",
                    "X-Label-Source": source_header,
                },
            )

        # ── Steps 1-4: search pre-generated crop directories ─────────────────
        for directory in search_dirs:
            if not directory.exists():
                continue
            for suffix in (".png", ".jpg", ".jpeg", ".tif"):
                candidate = directory / f"{safe_stem}{suffix}"
                if not candidate.exists():
                    continue
                if not is_path_safe(candidate, allowed_roots):
                    logger.warning(
                        "label_image: candidate %s rejected by path validation", candidate
                    )
                    continue
                return _serve(candidate, "label_crop")

        # ── Step 5: extract from original WSI if it still exists ─────────────
        if snapshot.file_path:
            wsi_path = Path(snapshot.file_path)
            if wsi_path.exists() and is_path_safe(wsi_path, allowed_roots):
                cache_dir = _label_cache_dir(cfg)
                extracted = extract_wsi_label_to_cache(wsi_path, cache_dir, safe_stem)
                if extracted is not None and extracted.exists():
                    return _serve(extracted, "wsi_embedded")

        # ── Step 6: nothing found ─────────────────────────────────────────────
        raise HTTPException(
            status_code=404,
            detail=f"No label image found for '{snapshot.filename}'",
        )

    # ------------------------------------------------------------------
    # GET /dashboard/api/recovery/files/{file_id}/audit-trail
    # ------------------------------------------------------------------
    @app.get(
        "/dashboard/api/recovery/files/{file_id}/audit-trail",
        response_model=AuditTrailResponse,
    )
    def audit_trail(db: DbDep, file_id: int) -> AuditTrailResponse:
        """
        Return the complete audit history for a watched folder file.

        Includes all TechnicianChange records and linked PipelineEvents, ordered
        chronologically so the UI can render a recovery timeline.
        """
        try:
            data = q.get_artifact_audit_trail(db, file_id)
        except Exception as exc:
            logger.warning("audit_trail: query failed: %s", exc)
            data = {"file_id": file_id, "changes": [], "events": []}
        return AuditTrailResponse(**data)

    # ------------------------------------------------------------------
    # POST /dashboard/api/recovery/files/{file_id}/open-folder
    # ------------------------------------------------------------------
    @app.post(
        "/dashboard/api/recovery/files/{file_id}/open-folder",
        response_model=OpenFolderResponse,
        summary="Open the containing folder in the host OS file manager",
        description=(
            "Opens the folder containing the monitored file in the native file manager "
            "(File Explorer on Windows, xdg-open on Linux, Finder on macOS). "
            "Intended for local workstation deployments only. "
            "The path is validated against configured recovery roots before opening."
        ),
    )
    def open_folder(db: DbDep, file_id: int) -> OpenFolderResponse:
        import platform
        import subprocess

        from pathoryx_enterprise.utils.path_validation import is_path_safe

        snapshot = q.get_monitored_file(db, file_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="File not found in watched folders")

        if not snapshot.file_path:
            return OpenFolderResponse(opened=False, path=None, message="No file path recorded for this entry")

        folder = Path(snapshot.file_path).parent

        # Security: validate the folder is under a configured recovery root
        try:
            from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings
            rs = RecoverySentrySettings()
            allowed_roots = rs.allowed_roots
        except Exception:
            allowed_roots = []

        # Always allow the file's own parent directory if it is under data/ relative to CWD
        cwd_data = Path("data").resolve()
        if cwd_data.exists():
            allowed_roots.append(cwd_data)

        if not is_path_safe(folder, allowed_roots or [folder]):
            raise HTTPException(
                status_code=403,
                detail=f"Folder is outside configured recovery roots and cannot be opened",
            )

        if not folder.exists():
            return OpenFolderResponse(
                opened=False,
                path=str(folder),
                message="Folder no longer exists on disk",
            )

        try:
            system = platform.system()
            if system == "Windows":
                import os as _os
                _os.startfile(str(folder))
            elif system == "Darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                # Linux — attempt xdg-open; graceful if not available (headless server)
                result = subprocess.run(
                    ["xdg-open", str(folder)],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode != 0:
                    return OpenFolderResponse(
                        opened=False,
                        path=str(folder),
                        message=f"xdg-open returned exit code {result.returncode}. "
                                "The dashboard backend may be running headless.",
                    )
            return OpenFolderResponse(opened=True, path=str(folder), message="Folder opened")
        except FileNotFoundError:
            return OpenFolderResponse(
                opened=False,
                path=str(folder),
                message="No file manager available (xdg-open / open not found). "
                        "Run the dashboard locally to use this feature.",
            )
        except Exception as exc:
            logger.warning("open_folder: error opening %s: %s", folder, exc)
            return OpenFolderResponse(opened=False, path=str(folder), message=str(exc))

    # ------------------------------------------------------------------
    # Phase 10 — Operational observability endpoints
    # ------------------------------------------------------------------

    @app.get(
        "/dashboard/api/operations/health",
        response_model=ServiceHealthExtendedResponse,
        summary="Extended service health with heartbeat ages and queue depths",
    )
    def operations_health(db: DbDep) -> ServiceHealthExtendedResponse:
        try:
            rows = q.get_service_health_extended(db)
        except Exception as exc:
            logger.warning("operations_health: query failed: %s", exc)
            rows = []
        return ServiceHealthExtendedResponse(
            services=[ServiceHealthExtended(**r) for r in rows],
            stale_threshold_seconds=q.RUNNER_STALE_THRESHOLD_SECONDS,
            as_of=datetime.now(tz=timezone.utc),
        )

    @app.get(
        "/dashboard/api/operations/stuck-triggers",
        response_model=StuckTriggersResponse,
        summary="Detect triggers stuck in pending or running state",
    )
    def operations_stuck_triggers(
        db: DbDep,
        pending_threshold_minutes: Annotated[int, Query(ge=1, le=120)] = q.STUCK_PENDING_THRESHOLD_MINUTES,
        running_threshold_minutes: Annotated[int, Query(ge=1, le=480)] = q.STUCK_RUNNING_THRESHOLD_MINUTES,
    ) -> StuckTriggersResponse:
        try:
            items = q.get_stuck_triggers(
                db,
                pending_threshold_minutes=pending_threshold_minutes,
                running_threshold_minutes=running_threshold_minutes,
            )
        except Exception as exc:
            logger.warning("operations_stuck_triggers: query failed: %s", exc)
            items = []
        return StuckTriggersResponse(
            items=[StuckTriggerItem(**i) for i in items],
            total=len(items),
            pending_stuck=sum(1 for i in items if i["kind"] == "pending_stuck"),
            running_stuck=sum(1 for i in items if i["kind"] == "running_stuck"),
            exhausted=sum(1 for i in items if i["kind"] == "exhausted"),
        )

    @app.get(
        "/dashboard/api/operations/incidents",
        response_model=OperationalIncidentsResponse,
        summary="Operational incident surface — severity-sorted warnings",
    )
    def operations_incidents(db: DbDep) -> OperationalIncidentsResponse:
        try:
            service_health = q.get_service_health_extended(db)
        except Exception as exc:
            logger.warning("operations_incidents: health query failed: %s", exc)
            service_health = []
        try:
            stuck = q.get_stuck_triggers(db)
        except Exception as exc:
            logger.warning("operations_incidents: stuck trigger query failed: %s", exc)
            stuck = []
        try:
            db_health = q.get_db_health_metrics(db)
        except Exception as exc:
            logger.warning("operations_incidents: db_health query failed: %s", exc)
            db_health = {"failed_triggers": 0, "recovery_backlog": 0}
        env_cfg = q.get_environment_config()

        incidents = q.build_operational_incidents(service_health, stuck, db_health, env_cfg)
        now = datetime.now(tz=timezone.utc)
        return OperationalIncidentsResponse(
            incidents=[OperationalIncident(**i) for i in incidents],
            total=len(incidents),
            critical_count=sum(1 for i in incidents if i["severity"] == "critical"),
            warning_count=sum(1 for i in incidents if i["severity"] == "warning"),
            info_count=sum(1 for i in incidents if i["severity"] == "info"),
            as_of=now,
        )

    @app.get(
        "/dashboard/api/operations/environment",
        response_model=EnvironmentConfig,
        summary="Environment and operational safety configuration",
    )
    def operations_environment() -> EnvironmentConfig:
        cfg = q.get_environment_config()
        return EnvironmentConfig(**cfg)

    # ------------------------------------------------------------------
    # Phase 3.6 — Scanner Fleet endpoints
    # ------------------------------------------------------------------

    from . import upload_queries as uq

    @app.get(
        "/dashboard/api/scanners",
        response_model=ScannerFleetResponse,
        summary="Scanner fleet configuration",
    )
    def scanner_fleet_list(
        include_disabled: Annotated[bool, Query(description="Include disabled scanners")] = False,
    ) -> ScannerFleetResponse:
        """
        Return the configured scanner fleet.
        By default only enabled scanners are returned; pass include_disabled=true
        to retrieve the complete fleet including disabled entries.
        """
        fleet = _load_scanner_fleet()
        entries = fleet.all() if include_disabled else fleet.enabled()
        return ScannerFleetResponse(
            scanners=[
                ScannerConfig(
                    scanner_id=e.scanner_id,
                    display_name=e.display_name,
                    location=e.location,
                    vendor=e.vendor,
                    enabled=e.enabled,
                )
                for e in entries
            ],
            total=fleet.total_count,
            enabled_count=fleet.enabled_count,
        )

    @app.get(
        "/dashboard/api/scanners/summary",
        response_model=ScannerSummaryResponse,
        summary="Per-scanner upload queue metrics",
    )
    def scanner_summary(db: DbDep) -> ScannerSummaryResponse:
        """
        Return upload queue counts grouped by scanner, enriched with display names.
        Includes all scanners that have queue data PLUS enabled fleet scanners with
        zero counts (so the summary always shows the full configured fleet).
        """
        fleet = _load_scanner_fleet()
        try:
            items = uq.get_scanner_summary(db, fleet)
        except Exception as exc:
            logger.warning("scanner_summary: query failed: %s", exc)
            items = []
        return ScannerSummaryResponse(
            scanners=[ScannerSummaryItem(**item) for item in items],
            as_of=datetime.now(tz=timezone.utc),
        )

    # ------------------------------------------------------------------
    # Phase 3.5 — Upload Operations endpoints
    # ------------------------------------------------------------------

    @app.get(
        "/dashboard/api/uploads/queue",
        response_model=UploadQueueResponse,
        summary="Paginated upload queue with optional filters",
    )
    def upload_queue(
        db: DbDep,
        status: Annotated[Optional[str], Query(description="queued|estimating|uploading|uploaded|delayed|failed")] = None,
        scanner_id: Annotated[Optional[str], Query()] = None,
        uploader_host: Annotated[Optional[str], Query()] = None,
        search: Annotated[Optional[str], Query(description="Filename/slide_id substring")] = None,
        from_date: Annotated[Optional[datetime], Query()] = None,
        to_date: Annotated[Optional[datetime], Query()] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> UploadQueueResponse:
        try:
            total, items = uq.list_upload_queue(
                db,
                status=status,
                scanner_id=scanner_id,
                uploader_host=uploader_host,
                search=search,
                from_date=from_date,
                to_date=to_date,
                page=page,
                page_size=page_size,
            )
        except Exception as exc:
            logger.warning("upload_queue: query failed: %s", exc)
            return UploadQueueResponse(total=0, page=page, page_size=page_size, items=[])
        return UploadQueueResponse(
            total=total,
            page=page,
            page_size=page_size,
            items=[UploadQueueItem(**item) for item in items],
        )

    @app.get(
        "/dashboard/api/uploads/metrics",
        response_model=UploadMetrics,
        summary="Upload queue operational metrics",
    )
    def upload_metrics(db: DbDep) -> UploadMetrics:
        try:
            metrics = uq.get_upload_metrics(db)
        except Exception as exc:
            logger.warning("upload_metrics: query failed: %s", exc)
            metrics = {
                "queued_count": 0, "active_count": 0, "completed_today": 0,
                "failed_count": 0, "delayed_count": 0,
                "avg_duration_seconds": None, "avg_throughput_mbps": None,
            }
        return UploadMetrics(**metrics)

    @app.get(
        "/dashboard/api/uploads/filters",
        response_model=UploadFilterOptions,
        summary="Available filter values for scanner and host dropdowns",
    )
    def upload_filters(db: DbDep) -> UploadFilterOptions:
        try:
            scanners = uq.list_upload_scanners(db)
            hosts = uq.list_upload_hosts(db)
        except Exception as exc:
            logger.warning("upload_filters: query failed: %s", exc)
            scanners, hosts = [], []
        return UploadFilterOptions(scanners=scanners, hosts=hosts)

    @app.post(
        "/dashboard/api/uploads/ingest",
        response_model=UploadIngestResponse,
        summary="Bulk upsert upload queue records from the uploader service",
    )
    def upload_ingest(
        db: DbDep,
        body: UploadIngestRequest,
    ) -> UploadIngestResponse:
        """
        Idempotent bulk ingest from the uploader service.

        Each record is upserted by (filename, queued_at).
        Only updates if the incoming last_updated_at is newer than stored.
        Safe to call repeatedly; no duplicate rows are created.
        """
        if not body.records:
            return UploadIngestResponse(upserted_count=0, skipped_count=0)
        try:
            records = [r.model_dump(exclude_none=False) for r in body.records]
            upserted, skipped = uq.upsert_upload_records(db, records)
        except Exception as exc:
            logger.error("upload_ingest: failed: %s", exc)
            raise HTTPException(status_code=500, detail="Ingest operation failed") from exc
        return UploadIngestResponse(upserted_count=upserted, skipped_count=skipped)

    @app.put(
        "/dashboard/api/uploads/queue/{record_id}",
        response_model=UploadQueueItem,
        summary="Update a single upload queue record",
    )
    def upload_update(
        db: DbDep,
        record_id: int,
        body: UploadQueueUpdateRequest,
    ) -> UploadQueueItem:
        try:
            result = uq.update_upload_record(db, record_id, body.model_dump(exclude_none=True))
        except Exception as exc:
            logger.error("upload_update: %s", exc)
            raise HTTPException(status_code=500, detail="Update failed") from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Upload record not found")
        return UploadQueueItem(**result)

    @app.patch(
        "/dashboard/api/uploads/queue/{record_id}/priority",
        response_model=UploadQueueItem,
        summary="Update the priority of a queued upload record",
    )
    def upload_priority_update(
        db: DbDep,
        record_id: int,
        body: UploadPriorityRequest,
    ) -> UploadQueueItem:
        if not (0 <= body.priority <= 9):
            raise HTTPException(status_code=422, detail="Priority must be between 0 and 9")
        try:
            result = uq.update_upload_priority(db, record_id, body.priority)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("upload_priority_update: %s", exc)
            raise HTTPException(status_code=500, detail="Priority update failed") from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Upload record not found")
        return UploadQueueItem(**result)

    @app.get(
        "/dashboard/api/operations/db-health",
        response_model=DbHealthResponse,
        summary="Database table sizes and health metrics",
    )
    def operations_db_health(db: DbDep) -> DbHealthResponse:
        try:
            metrics = q.get_db_health_metrics(db)
        except Exception as exc:
            logger.warning("operations_db_health: query failed: %s", exc)
            metrics = {"table_sizes": {}, "failed_triggers": 0, "pending_triggers": 0,
                       "oldest_pending_age_seconds": None, "recovery_backlog": 0}
        return DbHealthResponse(**metrics, as_of=datetime.now(tz=timezone.utc))

    # ------------------------------------------------------------------
    # GET /dashboard/api/services/health
    # ------------------------------------------------------------------
    @app.get("/dashboard/api/services/health", response_model=ServicesHealthResponse)
    def services_health(db: DbDep) -> ServicesHealthResponse:
        try:
            runners = q.list_runners(db)
        except SQLAlchemyError as exc:
            logger.warning("services_health: query failed: %s", exc)
            runners = []

        return ServicesHealthResponse(
            runners=[RunnerItem.model_validate(r) for r in runners],
            stale_threshold_seconds=q.RUNNER_STALE_THRESHOLD_SECONDS,
            as_of=datetime.now(tz=timezone.utc),
        )

    # ------------------------------------------------------------------
    # Phase 4.4 — Computer Core analytics endpoints
    # ------------------------------------------------------------------

    from . import core_queries as cq

    @app.get(
        "/dashboard/api/core/overview",
        response_model=CoreOverviewResponse,
        summary="Computer Core — operational overview",
    )
    def core_overview(db: DbDep) -> CoreOverviewResponse:
        try:
            data = cq.get_core_overview(db)
        except Exception as exc:
            logger.warning("core_overview: query failed: %s", exc)
            data = {
                "total_slides": 0, "slides_today": 0, "uploaded_today": 0,
                "failed_slides": 0, "active_uploads": 0, "queued_uploads": 0,
                "delayed_uploads": 0, "recovery_backlog": 0, "unreviewed_changes": 0,
                "total_bytes": 0, "status_counts": {}, "upload_status_counts": {},
            }
        return CoreOverviewResponse(**data, as_of=datetime.now(tz=timezone.utc))

    @app.get(
        "/dashboard/api/core/scanners",
        response_model=ScannerActivityResponse,
        summary="Computer Core — per-scanner activity",
    )
    def core_scanners(db: DbDep) -> ScannerActivityResponse:
        fleet = _load_scanner_fleet()
        try:
            items = cq.get_scanner_activity(db, fleet)
        except Exception as exc:
            logger.warning("core_scanners: query failed: %s", exc)
            items = []
        return ScannerActivityResponse(
            scanners=[ScannerActivityItem(**i) for i in items],
            as_of=datetime.now(tz=timezone.utc),
        )

    @app.get(
        "/dashboard/api/core/stains",
        response_model=StainDistributionResponse,
        summary="Computer Core — stain type distribution",
    )
    def core_stains(db: DbDep) -> StainDistributionResponse:
        try:
            items = cq.get_stain_distribution(db)
        except Exception as exc:
            logger.warning("core_stains: query failed: %s", exc)
            items = []
        total = sum(i["count"] for i in items)
        return StainDistributionResponse(
            items=[StainDistributionItem(**i) for i in items],
            total=total,
            as_of=datetime.now(tz=timezone.utc),
        )

    @app.get(
        "/dashboard/api/core/recovery",
        response_model=RecoveryStatsResponse,
        summary="Computer Core — recovery matrix statistics",
    )
    def core_recovery(db: DbDep) -> RecoveryStatsResponse:
        try:
            data = cq.get_recovery_stats(db)
        except Exception as exc:
            logger.warning("core_recovery: query failed: %s", exc)
            data = {
                "total_monitored": 0, "failed_count": 0, "suspicious_count": 0,
                "manual_review_count": 0, "auto_recovered": 0, "manual_review_required": 0,
                "total_changes": 0, "total_resolved": 0, "recovery_rate": 0.0,
                "recent_7d": 0, "by_folder": {}, "by_review_status": {}, "by_outcome": {},
            }
        return RecoveryStatsResponse(**data, as_of=datetime.now(tz=timezone.utc))

    @app.get(
        "/dashboard/api/core/storage",
        response_model=StorageStatsResponse,
        summary="Computer Core — storage analytics",
    )
    def core_storage(db: DbDep) -> StorageStatsResponse:
        try:
            data = cq.get_storage_stats(db)
        except Exception as exc:
            logger.warning("core_storage: query failed: %s", exc)
            data = {
                "total_slides_with_size": 0, "total_bytes": 0, "avg_bytes": 0,
                "max_bytes": 0, "min_bytes": 0, "uploaded_today_bytes": 0, "by_scanner": [],
            }
        return StorageStatsResponse(
            **{k: v for k, v in data.items() if k != "by_scanner"},
            by_scanner=[StorageScannerItem(**s) for s in data.get("by_scanner", [])],
            as_of=datetime.now(tz=timezone.utc),
        )

    @app.get(
        "/dashboard/api/core/uploads",
        response_model=UploadVelocityResponse,
        summary="Computer Core — upload velocity and throughput",
    )
    def core_uploads(db: DbDep) -> UploadVelocityResponse:
        try:
            data = cq.get_upload_velocity(db)
        except Exception as exc:
            logger.warning("core_uploads: query failed: %s", exc)
            data = {
                "avg_speed_mbps": None, "avg_duration_seconds": None,
                "total_in_queue": 0, "completed_total": 0, "failed_total": 0,
                "total_retries": 0, "queue_depth": 0, "delayed_count": 0,
                "daily_uploads_7d": [],
            }
        return UploadVelocityResponse(
            **{k: v for k, v in data.items() if k != "daily_uploads_7d"},
            daily_uploads_7d=[DailyUploadCount(**d) for d in data.get("daily_uploads_7d", [])],
            as_of=datetime.now(tz=timezone.utc),
        )

    return app
