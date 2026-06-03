# Nextflow Pipeline Guide

Nextflow is an optional orchestration layer that wraps the Pathoryx Python services as isolated pipeline stages. It adds resume capability, cluster dispatch, and reproducibility tracking on top of the database-first architecture.

When to use Nextflow vs the orchestrator:

| Use case | Recommendation |
|----------|---------------|
| Continuous watch-folder processing | `pathoryx-orchestrate` (always-on daemon) |
| Batch processing a known manifest | Nextflow |
| SLURM / LSF / Kubernetes cluster | Nextflow |
| Reproducible research audit trail | Nextflow (with `-resume`) |
| Development / testing | Either |

---

## Quick start

```bash
cd /home/shahram/Pathoryx-Enterprise/nextflow

# Run on local machine with a manifest of slides:
nextflow run main.nf \
    -profile local \
    --input_manifest manifest.csv

# Dry-run: validate manifest and config without processing:
nextflow run main.nf \
    -profile local \
    --dry_run true \
    --input_manifest manifest.csv

# Resume a previous run:
nextflow run main.nf \
    -profile local \
    --input_manifest manifest.csv \
    -resume
```

---

## Manifest format

`manifest.csv`:

```csv
slide_path,global_artifact_id,scanner_type
/data/scans/slide1.svs,abc-123-def,APERIO
/data/scans/slide2.ndpi,xyz-456-ghi,HAMAMATSU
/data/scans/slide3.mrxs,pqr-789-jkl,MIRAX
```

- `slide_path`: absolute path to the WSI file
- `global_artifact_id`: UUID or unique string; auto-generated if blank
- `scanner_type`: optional; used for scanner-specific routing (`APERIO`, `HAMAMATSU`, `MIRAX`, `GENERIC`)

Generate a manifest from a directory:

```bash
find /data/slides -name "*.svs" -o -name "*.ndpi" -o -name "*.mrxs" | \
    awk 'BEGIN{print "slide_path,global_artifact_id,scanner_type"} \
         {print $0","NR",GENERIC"}' > manifest.csv
```

---

## Profiles

| Profile | Executor | Containers | Use case |
|---------|----------|------------|---------|
| `local` | local | no | Development, single machine |
| `docker` | local | Docker | Reproducible local run |
| `singularity` | local | Singularity | HPC without Docker |
| `slurm` | SLURM | Singularity | HPC cluster |
| `kubernetes` | Kubernetes | Docker | Cloud / K8s cluster |
| `test` | local | no | CI validation with small dataset |

```bash
# Docker profile (requires docker-compose.yml images built first):
nextflow run main.nf -profile docker --input_manifest manifest.csv

# SLURM profile:
nextflow run main.nf -profile slurm --input_manifest manifest.csv -resume

# Test profile (uses bundled test fixtures):
nextflow run main.nf -profile test
```

---

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--input_manifest` | `manifest.csv` | Path to slide manifest CSV |
| `--babelshark_config` | `$BABELSHARK_CONFIG` env | BabelShark YAML config |
| `--qc_config` | `$QC_SERVICE_CONFIG` env | QC YAML config |
| `--dicom_config` | `$DICOM_CONFIG` env | DICOM YAML config |
| `--outdir` | `results` | Output directory for logs and reports |
| `--dry_run` | `false` | Validate inputs without processing |
| `--global_run_id` | auto UUID | Identifier for this pipeline run |
| `--scanner_routing` | `false` | Enable scanner-specific QC routing |

---

## Scanner-specific routing

With `--scanner_routing true`, the pipeline uses scanner-appropriate QC parameters:

- `APERIO` → Leica GT450 / SVS defaults
- `HAMAMATSU` → NDPI compression / tiling defaults
- `MIRAX` → MIRAX/3DHistech defaults
- `GENERIC` → Conservative defaults

Enable in a run:

```bash
nextflow run main.nf \
    -profile local \
    --input_manifest manifest.csv \
    --scanner_routing true
```

---

## Retry and error strategy

Each stage retries up to `maxRetries` times with exponential backoff. Failed slides are
collected into a `failed_ch` channel and written to `results/failed_samples.csv` for
manual review.

Default retry settings (in `nextflow.config`):

| Stage | maxRetries | Backoff | errorStrategy |
|-------|-----------|---------|---------------|
| intake | 3 | 30s×attempt | retry |
| qc | 2 | 60s×attempt | retry, then ignore |
| dicom | 3 | 120s×attempt | retry |
| upload | 5 | 60s×attempt | retry |

---

## Correlation ID propagation

Each slide gets a `correlation_id` (UUID) when it enters the pipeline. This ID:

- Is passed to every stage via environment variable `PATHORYX_CORRELATION_ID`
- Appears in every log line from every service processing that slide
- Is stored in `core.service_trigger.correlation_id`

The `global_run_id` (UUID for the entire Nextflow run) is similarly propagated via
`PATHORYX_GLOBAL_RUN_ID`.

To trace a slide end-to-end after a run:

```bash
# Find all logs for a specific slide:
grep -r "abc-123-def" results/logs/

# In the database:
psql "$DATABASE_URL" -c "
SELECT event_type, occurred_at, event_payload
FROM events.pipeline_events
WHERE aggregate_id = 'abc-123-def'
ORDER BY event_version;"
```

---

## Output directory structure

```
results/
├── logs/
│   ├── intake_<artifact_id>.json
│   ├── qc_<artifact_id>.json
│   ├── dicom_<artifact_id>.json
│   └── upload_<artifact_id>.json
├── failed_samples.csv          ← slides that failed all retries
└── reports/
    ├── pipeline_report.html    ← Nextflow execution report
    └── pipeline_timeline.html  ← Stage timeline visualization
```

---

## Environment variable passthrough

Nextflow stages inherit the shell environment by default. Ensure these are set before running:

```bash
export DATABASE_URL="postgresql://..."
export BABELSHARK_CONFIG="/path/to/babelshark.yaml"
export QC_SERVICE_CONFIG="/path/to/qc.yaml"
export DICOM_CONFIG="/path/to/dicom.yaml"
export SECTRA_HOST="..."
export SECTRA_PORT="4242"
export SECTRA_REMOTE_AE="..."
export SECTRA_LOCAL_AE="..."
export PATHORYX_ALLOWED_INPUT_ROOTS="/data/slides,/mnt/nfs"

nextflow run main.nf -profile local --input_manifest manifest.csv
```

Or source your `.env`:

```bash
export $(grep -v '^#' .env | xargs)
nextflow run main.nf -profile local --input_manifest manifest.csv
```

---

## Nextflow Tower / Seqera Platform

To monitor runs in Nextflow Tower:

```bash
nextflow run main.nf \
    -profile slurm \
    --input_manifest manifest.csv \
    -with-tower https://tower.nf
```

Set `TOWER_ACCESS_TOKEN` in your environment first.

---

## Troubleshooting Nextflow runs

```bash
# Check execution log:
cat .nextflow.log

# List cached work directories:
ls .nextflow_work/

# Force re-run a specific stage (delete its cache):
nextflow clean -f     # cleans all work dirs
nextflow run main.nf -profile local --input_manifest manifest.csv  # re-runs

# Check failed sample report:
cat results/failed_samples.csv
```
