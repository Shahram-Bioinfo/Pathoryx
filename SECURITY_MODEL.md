# Security Model

## Credential Management

**Rule**: No credentials anywhere in source code. All secrets via environment variables.

- `DATABASE_URL` — must not contain placeholder values (`strongpassword`, `CHANGEME`, etc.).
  Validated at service startup by `BaseSettings` field validators.
- `SECTRA_HOST`, `SECTRA_PORT`, `SECTRA_REMOTE_AE`, `SECTRA_LOCAL_AE` — PACS connection, env-only.
- No `.env` files committed to git. `.gitignore` excludes `.env` and `.env.*`.

## Database Least Privilege

The application user should have minimal privileges:

```sql
-- Create app user
CREATE USER pathoryx_app WITH PASSWORD 'REAL_PASSWORD';

-- Grant per-schema
GRANT USAGE ON SCHEMA core, events, qc, babelshark, dicomizer, uploader, failed_watcher, audit
  TO pathoryx_app;

-- Grant table-level access (not superuser)
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA core TO pathoryx_app;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA events TO pathoryx_app;
-- Events are append-only — no UPDATE/DELETE for app user
REVOKE UPDATE, DELETE ON events.pipeline_events FROM pathoryx_app;

GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA qc TO pathoryx_app;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA babelshark TO pathoryx_app;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA dicomizer TO pathoryx_app;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA uploader TO pathoryx_app;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA failed_watcher TO pathoryx_app;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA audit TO pathoryx_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA core, events, qc, babelshark, dicomizer, uploader,
  failed_watcher, audit TO pathoryx_app;
```

## Path Traversal Protection

All filesystem operations pass paths through `validate_path_under_roots()`:

```python
from pathoryx_enterprise.utils.path_validation import validate_path_under_roots, PathTraversalError

safe_path = validate_path_under_roots(user_supplied_path, allowed_roots=[STAGING_DIR])
```

Raises `PathTraversalError` if the resolved path is not under any allowed root.

The `PATHORYX_ALLOWED_INPUT_ROOTS` env var lists allowed root directories.

## Docker Security

- All service containers run as non-root user `pathoryx` (uid/gid created in Dockerfile).
- No `-v /:/host` mounts — only specific data directories are mounted.
- Staging and output directories are mounted read-only where possible (babelshark: watch dir).

## Audit Trail

Every state transition produces an immutable event in `events.pipeline_events`.
Technician interventions produce records in `failed_watcher.technician_changes`.
These tables cannot be modified by the application user (REVOKE UPDATE/DELETE).

## Runner Identity

Each runner has a stable `runner_id` (deterministic UUID5 from service + environment) and
`host_id` (hostname). These are recorded on every trigger, event, and DB write,
enabling forensic tracing of which machine processed which slide.

## Secret Rotation

To rotate the database password:
1. Update `DATABASE_URL` in `.env` (or secrets manager).
2. Rolling restart all services — each service reads credentials at startup.
3. No code changes required.
