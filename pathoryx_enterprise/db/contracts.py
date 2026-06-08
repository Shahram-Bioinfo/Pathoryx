"""
Canonical database contract for Palantir Enterprise.

This file is the single authoritative declaration of every table managed by
this system. Services validate against this contract at startup to catch
schema drift between code and database early.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Tuple


@dataclass(frozen=True)
class EnterpriseDBContract:
    core_tables: Tuple[str, ...]
    event_tables: Tuple[str, ...]
    service_tables: Tuple[str, ...]
    ops_tables: Tuple[str, ...]
    failed_watcher_tables: Tuple[str, ...]


ENTERPRISE_DB_CONTRACT: Final[EnterpriseDBContract] = EnterpriseDBContract(
    core_tables=(
        "core.file_records",
        "core.metadata_snapshots",
        "core.pipeline_runs",
        "core.step_runs",
        "core.service_trigger",
        "core.technical_metrics",
        "core.runner_registrations",
    ),
    event_tables=(
        "events.pipeline_events",
    ),
    service_tables=(
        "babelshark.extraction_results",
        "qc.qc_results",
        "dicomizer.conversion_results",
        "uploader.upload_results",
    ),
    ops_tables=(
        "ops.event_logs",
        "ops.error_logs",
    ),
    failed_watcher_tables=(
        "failed_watcher.technician_changes",
        "failed_watcher.watched_folder_snapshots",
    ),
)

ALL_TABLES: Final[Tuple[str, ...]] = (
    ENTERPRISE_DB_CONTRACT.core_tables
    + ENTERPRISE_DB_CONTRACT.event_tables
    + ENTERPRISE_DB_CONTRACT.service_tables
    + ENTERPRISE_DB_CONTRACT.ops_tables
    + ENTERPRISE_DB_CONTRACT.failed_watcher_tables
)
