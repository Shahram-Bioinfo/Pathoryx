from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pathoryx_enterprise.services.qc.engine.domain.enums import SlideQCStatus


@dataclass(slots=True)
class QCModuleResult:
    module_name: str
    success: bool
    values: dict[str, Any]
    duration_seconds: float
    artifacts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SlideQCResult:
    status: SlideQCStatus
    source_path: Path
    source_checksum: str | None
    total_duration_seconds: float
    stain_result: QCModuleResult | None = None
    penmark_result: QCModuleResult | None = None
    bubble_result: QCModuleResult | None = None
    blur_result: QCModuleResult | None = None
    summary: dict[str, Any] = field(default_factory=dict)