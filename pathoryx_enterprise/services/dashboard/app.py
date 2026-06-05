"""
FastAPI application for the Pathoryx Dashboard.

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
    DbHealthResponse,
    EnvironmentConfig,
    OperationalIncident,
    OperationalIncidentsResponse,
    ServiceHealthExtended,
    ServiceHealthExtendedResponse,
    StuckTriggerItem,
    StuckTriggersResponse,
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


def _resolve_label_root_dir() -> Optional[Path]:
    """
    Resolve the BabelShark label_root_dir from config.

    Reads BABELSHARK_CONFIG_PATH (or BABELSHARK_CONFIG) env var, loads the
    YAML, and returns the label_root_dir path.  Returns None if the config
    is not available — the caller should return HTTP 404 gracefully.
    """
    import os
    import yaml

    config_path_str = (
        os.environ.get("BABELSHARK_CONFIG_PATH")
        or os.environ.get("BABELSHARK_CONFIG")
    )
    if not config_path_str:
        # Try known default locations
        for candidate in ("configs/babelshark_config.yaml", "./configs/babelshark_config.yaml"):
            if Path(candidate).exists():
                config_path_str = candidate
                break

    if not config_path_str:
        return None

    try:
        with open(config_path_str) as fh:
            cfg = yaml.safe_load(fh) or {}
        label_root = cfg.get("label_root_dir")
        return Path(label_root) if label_root else None
    except Exception as exc:
        logger.debug("_resolve_label_root_dir: could not load config: %s", exc)
        return None


def create_app() -> FastAPI:
    app = FastAPI(
        title="Pathoryx Dashboard API",
        description="Read-only observability API for the Pathoryx Enterprise pipeline.",
        version="1.0.0",
        docs_url="/dashboard/docs",
        redoc_url="/dashboard/redoc",
        openapi_url="/dashboard/openapi.json",
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

        return MonitoredFilesResponse(
            total=total,
            items=[MonitoredFileItem(**item) for item in items],
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
        Validate a proposed filename against the Pathoryx slide ID parser rules.

        Safe to call on every keystroke — no filesystem or DB side-effects.
        Returns structured components so the UI can show field-level feedback.
        """
        result = validate_filename_structured(body.filename)
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
        Serve the extracted label image for a watched folder file.

        Looks up the label_root_dir from the BabelShark config, then searches
        for common image formats matching the file's stem.

        Returns 404 with a JSON reason when no image is found.
        """
        from fastapi.responses import FileResponse

        snapshot = q.get_monitored_file(db, file_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="File not found in watched folders")

        stem = Path(snapshot.filename).stem

        label_dir = _resolve_label_root_dir()
        if label_dir is None:
            raise HTTPException(
                status_code=404,
                detail="Label directory not configured (BABELSHARK_CONFIG_PATH not set)",
            )

        for suffix in (".jpg", ".jpeg", ".png", ".tif"):
            candidate = label_dir / f"{stem}{suffix}"
            if candidate.exists():
                return FileResponse(
                    path=str(candidate),
                    media_type=f"image/{'jpeg' if suffix in ('.jpg', '.jpeg') else suffix.lstrip('.')}",
                    headers={"Cache-Control": "max-age=3600"},
                )

        raise HTTPException(
            status_code=404,
            detail=f"No label image found for '{snapshot.filename}' in {label_dir}",
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

    return app
