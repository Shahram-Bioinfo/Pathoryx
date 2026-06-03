# Database Setup

## PostgreSQL version

PostgreSQL 14 or newer is required. Check:

```bash
psql --version
# psql (PostgreSQL) 14.x
```

## 1. Create the database user

Connect as the PostgreSQL superuser:

```bash
sudo -u postgres psql
```

Then:

```sql
-- Create the application user
CREATE USER pathoryx_user WITH PASSWORD 'CHANGE_THIS_PASSWORD';

-- Optionally allow the user to create its own database (only needed for tests)
-- ALTER USER pathoryx_user CREATEDB;

\q
```

## 2. Create the database

```bash
sudo -u postgres createdb -O pathoryx_user pathoryx_enterprise
```

Verify connection:

```bash
psql "postgresql://pathoryx_user:CHANGE_THIS_PASSWORD@localhost:5432/pathoryx_enterprise" -c "SELECT version();"
```

## 3. Enable required extensions

Connect as superuser (extensions require superuser):

```bash
sudo -u postgres psql pathoryx_enterprise
```

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
\q
```

## 4. Set DATABASE_URL

In your `.env` file:

```
DATABASE_URL=postgresql://pathoryx_user:CHANGE_THIS_PASSWORD@localhost:5432/pathoryx_enterprise
```

## 5. Run Alembic migrations

```bash
source .venv/bin/activate
alembic upgrade head
```

This creates all schemas, tables, indexes, and triggers:

| Schema | Purpose |
|--------|---------|
| `core` | file_records, service_trigger, runner_registrations, metadata_snapshots |
| `events` | pipeline_events (immutable event log) |
| `babelshark` | intake_results |
| `qc` | qc_results |
| `dicomizer` | dicom_results |
| `uploader` | upload_results |
| `failed_watcher` | failed_watcher_state, technician_changes |
| `audit` | audit_log |

Verify:

```bash
alembic current
# Should print: 0001 (head)
```

## 6. Apply least-privilege grants (production)

After the migration, grant only the permissions each service needs:

```sql
-- Connect as postgres superuser
sudo -u postgres psql pathoryx_enterprise

GRANT USAGE ON SCHEMA core, events, qc, babelshark, dicomizer, uploader, failed_watcher, audit
  TO pathoryx_user;

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA core TO pathoryx_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA core TO pathoryx_user;

-- Events are append-only
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA events TO pathoryx_user;
REVOKE UPDATE, DELETE ON events.pipeline_events FROM pathoryx_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA events TO pathoryx_user;

GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA babelshark TO pathoryx_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA babelshark TO pathoryx_user;

GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA qc TO pathoryx_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA qc TO pathoryx_user;

GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA dicomizer TO pathoryx_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA dicomizer TO pathoryx_user;

GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA uploader TO pathoryx_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA uploader TO pathoryx_user;

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA failed_watcher TO pathoryx_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA failed_watcher TO pathoryx_user;

GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA audit TO pathoryx_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA audit TO pathoryx_user;
```

## 7. Verify schema

```sql
-- Connect as pathoryx_user
psql "postgresql://pathoryx_user:CHANGE_THIS_PASSWORD@localhost:5432/pathoryx_enterprise"

\dn          -- list schemas
\dt core.*   -- list core tables
\dt events.* -- list event tables
```

Expected core tables: `file_records`, `service_trigger`, `runner_registrations`, `metadata_snapshots`, `pipeline_runs`, `step_runs`, `technical_metrics`.

## Connection pooling

Default pool settings (tunable via `.env`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `DB_POOL_SIZE` | 5 | Persistent connections per service process |
| `DB_MAX_OVERFLOW` | 10 | Burst connections above pool_size |
| `DB_POOL_TIMEOUT` | 30 | Seconds to wait for a connection |
| `DB_POOL_RECYCLE` | 3600 | Seconds before a connection is recycled |

With 5 services + orchestrator running, worst-case simultaneous connections ≈ `6 × (5 + 10) = 90`. Set PostgreSQL `max_connections` ≥ 100.

```sql
SHOW max_connections;
-- If too low: edit postgresql.conf and reload
-- ALTER SYSTEM SET max_connections = 200;
-- SELECT pg_reload_conf();
```

## Backup and restore

```bash
# Backup
pg_dump -U pathoryx_user -F c pathoryx_enterprise > backup_$(date +%Y%m%d).dump

# Restore to a fresh database
createdb -O pathoryx_user pathoryx_enterprise_restore
pg_restore -U pathoryx_user -d pathoryx_enterprise_restore backup_20260527.dump
```

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for database-specific errors (password auth, missing extensions, permission errors).
