"""
DEPRECATED — this module has moved to recovery_sentry.change_detector.

Re-exports for backward compatibility only. Do not add new imports here.
"""
from pathoryx_enterprise.services.recovery_sentry.change_detector import (  # noqa: F401
    ChangeEvent,
    detect_changes,
    scan_folder,
)
