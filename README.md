# Pathoryx

Whole Slide Image (WSI) ingestion and processing pipeline.
Processes 500–1000 slides/day across BabelShark intake, QC inference, DICOM conversion, and PACS upload.

## Quick Start

```bash
# 1. Clone / navigate
cd /home/shahram/Pathoryx-Enterprise

# 2. Configure
cp .env.example .env
# Edit .env — fill in DATABASE_URL, BABELSHARK_CONFIG, QC_SERVICE_CONFIG,
#              DICOM_CONFIG, SECTRA_* settings

# 3. Install
pip install -e .

# 4. Run database migrations
alembic upgrade head

# 5. Start all services
pathoryx-orchestrate

# Or start individually:
pathoryx-babelshark
pathoryx-qc
pathoryx-dicom
pathoryx-uploader
pathoryx-failed-watcher
```

For detailed setup instructions, see [SETUP.md](SETUP.md).

## Docker

```bash
cp .env.example .env
# Edit .env

docker-compose up postgres -d       # Start DB
docker-compose run --rm migrate     # Apply migrations
docker-compose up                   # Start all services
```

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

```
Watch Folder(s)
     │
     ▼
[BabelShark]──→ core.service_trigger ──→ [QC Service]
                                               │
                                    accepted   │  rejected
                                               ▼
                                    core.service_trigger ──→ [DICOM Service]
                                                                    │
                                               storescu (chunked)   │
                                                                    ▼
                                    core.service_trigger ──→ [Uploader]
                                                                    │
                                                             status: uploaded
```

All services communicate via `core.service_trigger` (PostgreSQL). No message broker required.

## Documentation

| File | Contents |
|------|----------|
| [SETUP.md](SETUP.md) | Installation, virtualenv, environment variables |
| [DATABASE_SETUP.md](DATABASE_SETUP.md) | PostgreSQL user, database, migrations |
| [RUNBOOK.md](RUNBOOK.md) | Day-to-day operations, queue management, maintenance |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Known errors A–Q with root causes and fixes |
| [TESTING_WITH_REAL_DATA.md](TESTING_WITH_REAL_DATA.md) | Smoke tests through 100-slide load tests |
| [NEXTFLOW.md](NEXTFLOW.md) | Nextflow batch orchestration guide |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, DB schema, state machine |
| [OPERATIONS.md](OPERATIONS.md) | Start/stop, health checks, monitoring queries |
| [OBSERVABILITY.md](OBSERVABILITY.md) | Prometheus metrics, OTel tracing, log format |
| [MIGRATION_PLAN.md](MIGRATION_PLAN.md) | How to migrate from original Pathoryx |
| [FAILURE_RECOVERY.md](FAILURE_RECOVERY.md) | Dead-letter queue, crashed runner recovery |
| [SECURITY_MODEL.md](SECURITY_MODEL.md) | Credentials, DB privileges, path validation |
| [CLEANUP_REPORT.md](CLEANUP_REPORT.md) | All bugs fixed vs original project |

## Key Improvements vs Original

1. **No hardcoded credentials** — env-only via Pydantic BaseSettings, validated at startup
2. **ModelRegistry loaded once** — not per slide (was causing GPU weight reload every inference)
3. **FOR UPDATE SKIP LOCKED** — safe concurrent trigger dequeue
4. **storescu batching** — prevents ARG_MAX overflow on large DICOM series
5. **Streaming SHA-256** — 4 MB chunks, no OOM on 2–10 GB WSI files
6. **Immutable event store** — full audit trail with replay capability
7. **Health/readiness endpoints** — Kubernetes probe support on every service
8. **Prometheus metrics** — queue depth, latency, GPU utilization
9. **SIGTERM handling** — graceful shutdown on every service
10. **Path traversal protection** — all filesystem operations validated

## Requirements

- Python 3.12+
- PostgreSQL 14+
- `storescu` (dcmtk) for DICOM upload
- NVIDIA GPU (optional, for QC service)
- Nextflow 23.10+ (optional, for pipeline orchestration)
