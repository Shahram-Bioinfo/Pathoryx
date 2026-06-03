"""
Model registry — import all models so Alembic's autogenerate can discover them.
"""
from pathoryx_enterprise.db.models.core import (  # noqa: F401
    FileRecord,
    MetadataSnapshot,
    PipelineRun,
    RunnerRegistration,
    ServiceTrigger,
    StepRun,
    TechnicalMetrics,
)
from pathoryx_enterprise.db.models.events import PipelineEvent  # noqa: F401
from pathoryx_enterprise.db.models.audit import EventLog, ErrorLog  # noqa: F401
from pathoryx_enterprise.db.models.babelshark import ExtractionResult  # noqa: F401
from pathoryx_enterprise.db.models.qc import QCResult  # noqa: F401
from pathoryx_enterprise.db.models.dicomizer import ConversionResult  # noqa: F401
from pathoryx_enterprise.db.models.uploader import UploadResult  # noqa: F401
from pathoryx_enterprise.db.models.failed_watcher import (  # noqa: F401
    TechnicianChange,
    WatchedFolderSnapshot,
)
