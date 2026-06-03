from __future__ import annotations

import enum


class SlideQCStatus(str, enum.Enum):
    pending = "pending"
    started = "started"
    completed = "completed"
    failed = "failed"
    skipped_already_processed = "skipped_already_processed"


class StepStatus(str, enum.Enum):
    pending = "pending"
    started = "started"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"
