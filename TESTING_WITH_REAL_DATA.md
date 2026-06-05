# Testing with Real Data

This guide walks through verifying a working deployment from a single test slide through a full 100-slide load test.

## Prerequisites

- All services running (see [SETUP.md](SETUP.md))
- Database migrated to head
- At least one real WSI file (`.svs`, `.ndpi`, `.tiff`, or `.mrxs`)
- PACS reachable at `SECTRA_HOST:SECTRA_PORT` (or use dry-run mode for DICOM stages)

---

## Level 1: Single slide smoke test

### Step 1: Drop one slide into the watch folder

```bash
cp /path/to/test_slide.svs "$BABELSHARK_WATCH_DIR/"
```

BabelShark polls the watch folder. Within one poll interval (~10s) you should see log output:

```json
{"event": "intake.started", "service": "babelshark", "source_path": "/watch/test_slide.svs"}
```

### Step 2: Verify FileRecord was created

```sql
SELECT internal_id, global_artifact_id, status, canonical_path
FROM core.file_records
ORDER BY created_at DESC
LIMIT 5;
```

Expected `status`: `intake_registered`

### Step 3: Watch the trigger queue

```sql
SELECT target_service, stage_name, trigger_status, created_at
FROM core.service_trigger
ORDER BY created_at DESC
LIMIT 10;
```

The QC trigger should appear as `pending`, then transition to `running`, then `completed`.

### Step 4: Watch pipeline events

```sql
SELECT event_type, occurred_at, event_payload->>'status' AS status
FROM events.pipeline_events
WHERE aggregate_id = (
    SELECT global_artifact_id FROM core.file_records ORDER BY created_at DESC LIMIT 1
)
ORDER BY event_version ASC;
```

A healthy single-slide run produces events in this order:

1. `intake.started`
2. `intake.completed`
3. `qc.started`
4. `qc.completed`
5. `dicom.conversion_started`
6. `dicom.conversion_completed`
7. `upload.started`
8. `upload.completed`

### Step 5: Verify final status

```sql
SELECT status, updated_at
FROM core.file_records
WHERE global_artifact_id = 'YOUR_ARTIFACT_ID';
-- Expected: status = 'uploaded'
```

### Expected duration

| Stage | Typical time |
|-------|-------------|
| BabelShark intake | 5–30s (depends on file size, staging copy) |
| QC inference | 20–120s (GPU) / 60–300s (CPU) |
| DICOM conversion | 1–10 min (file size dependent) |
| PACS upload | 30s–5 min (network/PACS speed) |

---

## Level 2: Rejection path test

To test that QC rejection works correctly:

1. Use a blurry or penmarked slide, or temporarily set `QC_FORCE_REJECT=true` in `.env`.
2. Verify the slide stops at QC:

```sql
SELECT decision_status, decision_reason
FROM qc.qc_results
WHERE global_artifact_id = 'YOUR_ARTIFACT_ID';
-- Expected: decision_status = 'rejected'
```

3. Verify no DICOM trigger was created for this slide:

```sql
SELECT count(*) FROM core.service_trigger
WHERE global_artifact_id = 'YOUR_ARTIFACT_ID'
  AND target_service = 'dicom_service';
-- Expected: 0
```

---

## Level 3: Failure recovery test

To test dead-letter queue and recovery:

1. Stop the QC service mid-run (SIGKILL).
2. The trigger remains in `running` status.
3. After `FAILED_WATCHER_CRASH_THRESHOLD_SECONDS` (default 120s), the failed watcher detects the crashed runner.
4. Manually requeue (or wait for auto-requeue):

```sql
UPDATE core.service_trigger
SET trigger_status = 'pending',
    started_at = NULL,
    claimed_by_runner_id = NULL,
    claimed_by_host_id = NULL
WHERE trigger_status = 'running'
  AND started_at < NOW() - INTERVAL '5 minutes';
```

5. Restart QC — it should pick up and process the slide normally.

---

## Level 4: 100-slide load test

### Prepare a manifest

```bash
# List 100 slides into a manifest
ls /data/slides/*.svs | head -100 | while IFS= read -r f; do
    echo "${f},$(python3 -c "import uuid; print(uuid.uuid4())")"
done > test_manifest_100.csv

# Add CSV header
sed -i '1s/^/slide_path,global_artifact_id\n/' test_manifest_100.csv
```

### Option A: Copy to watch folder (continuous mode)

```bash
# Drop all 100 into the watch folder over 10 minutes
for f in /data/slides/test_batch/*.svs; do
    cp "$f" "$BABELSHARK_WATCH_DIR/"
    sleep 6   # ~10 per minute
done
```

### Option B: Nextflow batch mode

```bash
cd nextflow/
nextflow run main.nf \
    -profile local \
    --input_manifest ../test_manifest_100.csv \
    -resume
```

### Monitor progress

```sql
-- Live queue depth by stage
SELECT target_service, trigger_status, count(*)
FROM core.service_trigger
GROUP BY target_service, trigger_status
ORDER BY target_service, trigger_status;
```

```sql
-- Throughput: slides completed per hour
SELECT
    date_trunc('hour', updated_at) AS hour,
    count(*) AS completed
FROM core.file_records
WHERE status = 'uploaded'
GROUP BY 1
ORDER BY 1 DESC;
```

### Prometheus metrics (if running)

```bash
curl http://localhost:9091/metrics | grep pathoryx_queue_depth
curl http://localhost:9092/metrics | grep pathoryx_slide_processing_seconds
```

### Expected throughput

| Configuration | Expected slides/hour |
|---------------|---------------------|
| Single machine, CPU QC | 20–40 |
| Single machine, GPU QC (V100) | 80–150 |
| 4-node SLURM, GPU | 400–600 |

### Pass/fail criteria for load test

- No slides stuck in `running` status for > 30 min without heartbeat
- Event version sequence is gapless (no missing events):

```sql
SELECT aggregate_id FROM events.pipeline_events
GROUP BY aggregate_id
HAVING max(event_version) != count(*);
-- Empty result = all sequences intact
```

- No duplicate FileRecord registrations:

```sql
SELECT canonical_path, count(*)
FROM core.file_records
GROUP BY canonical_path
HAVING count(*) > 1;
-- Empty result = no duplicates
```

---

## Checking logs during a test

```bash
# Follow all service logs (if running individually):
tail -f /var/log/pathoryx/*.log

# Docker Compose:
docker-compose logs -f

# Filter by artifact_id:
docker-compose logs qc 2>&1 | grep "YOUR_ARTIFACT_ID"
```

---

## After testing: clean up test data

```sql
-- Remove test file records (development environment only):
DELETE FROM core.file_records
WHERE canonical_path LIKE '/data/slides/test_batch/%';

-- The cascade will delete related triggers, events (if CASCADE set).
-- In production: NEVER delete from events.pipeline_events.
```
