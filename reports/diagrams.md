# Pathoryx Enterprise — Architecture Diagrams

## Diagram 1: Pipeline Flow

```mermaid
flowchart TD
    A["🔬 Scanner / NAS\n(WSI file)"] --> B
    B["BabelShark / Watcher\nIntake & Classification"]
    B --> C["core.file_records\nstatus: intake_registered"]
    C --> D["core.service_trigger\ntarget: qc_service"]

    D --> E["QC Service\npathoryx-qc"]
    E --> F["QC Engine\nBlur · Stain · Penmark · Bubble\nOpenSlide + PyTorch"]
    F --> G["qc.qc_results\ndecision_status · metrics · timing"]
    G --> H{Decision}

    H -->|qc_passed| I["file_records\nstatus: qc_passed"]
    H -->|qc_failed| J["file_records\nstatus: qc_failed"]
    J --> K["failed_watcher\nTechnician review"]

    I --> L["core.service_trigger\ntarget: dicom_service"]
    L --> M["DICOM Service\npathoryx-dicom"]
    M --> N["wsidicomizer CLI\nWSI → DICOM folder"]
    N --> O["dcmtk dcmodify\nIDS7 header injection"]
    O --> P["dicomizer.conversion_results\nconversion_status · output_path"]
    P --> Q["file_records\nstatus: dicom_done"]
    Q --> R["core.service_trigger\ntarget: upload_service"]

    R --> S["Upload Service\npathoryx-uploader"]
    S --> T["storescu C-STORE\n⚠️ Not yet wired"]
    T --> U["Sectra IDS7 PACS"]
    S --> V["uploader.upload_results\nupload_status · duration"]
    V --> W["file_records\nstatus: uploaded"]
```

## Diagram 2: FileRecord State Machine

```mermaid
stateDiagram-v2
    [*] --> detected
    detected --> intake_running
    intake_running --> intake_registered

    intake_registered --> qc_pending
    qc_pending --> qc_running
    qc_running --> qc_passed
    qc_running --> qc_failed

    qc_failed --> manual_review
    qc_passed --> dicom_pending
    intake_registered --> dicom_pending : skip_qc

    dicom_pending --> dicom_running
    dicom_running --> dicom_done
    dicom_running --> dicom_failed

    dicom_failed --> manual_review
    dicom_done --> upload_pending

    upload_pending --> upload_running
    upload_running --> uploaded
    upload_running --> upload_failed

    upload_failed --> manual_review
    uploaded --> archived
    manual_review --> discarded
    manual_review --> qc_pending : requeue
```

## Diagram 3: Entity Relationship (Core)

```mermaid
erDiagram
    FILE_RECORDS {
        bigint internal_id PK
        uuid uuid UK
        text global_artifact_id
        text canonical_path UK
        text status
        text scanner_id
        timestamptz created_at
        timestamptz updated_at
    }

    SERVICE_TRIGGER {
        bigint internal_id PK
        text source_service
        text target_service
        text stage_name
        bigint file_record_internal_id FK
        text trigger_status
        jsonb trigger_payload_json
        int retry_count
        int max_retries
        text correlation_id
        timestamptz triggered_at
        timestamptz finished_at
    }

    QC_RESULTS {
        bigint internal_id PK
        text idempotency_key UK
        bigint file_record_internal_id FK
        bigint trigger_internal_id
        text decision_status
        jsonb blur_metrics
        jsonb stain_metrics
        float memory_rss_mb
        float total_duration_seconds
        timestamptz started_at
        timestamptz finished_at
    }

    CONVERSION_RESULTS {
        bigint internal_id PK
        text idempotency_key UK
        bigint file_record_internal_id FK
        bigint trigger_internal_id
        text conversion_status
        text source_path
        text output_path
        jsonb failure_context
        float duration_seconds
    }

    UPLOAD_RESULTS {
        bigint internal_id PK
        text idempotency_key UK
        bigint file_record_internal_id FK
        bigint trigger_internal_id
        text upload_status
        text upload_method
        float duration_seconds
    }

    PIPELINE_EVENTS {
        bigint event_id PK
        text idempotency_key UK
        text event_type
        text aggregate_id
        bigint file_record_internal_id
        bigint caused_by_event_id FK
        jsonb event_payload
        timestamptz occurred_at
    }

    FILE_RECORDS ||--o{ SERVICE_TRIGGER : "triggers"
    FILE_RECORDS ||--o{ QC_RESULTS : "has_qc"
    FILE_RECORDS ||--o{ CONVERSION_RESULTS : "has_conversion"
    FILE_RECORDS ||--o{ UPLOAD_RESULTS : "has_upload"
    FILE_RECORDS ||--o{ PIPELINE_EVENTS : "events"
    PIPELINE_EVENTS ||--o{ PIPELINE_EVENTS : "caused_by"
```

## Diagram 4: Trigger Lifecycle

```mermaid
sequenceDiagram
    participant Upstream as Upstream Service
    participant DB as PostgreSQL
    participant Runner as Service Runner
    participant Writer as DB Writer

    Upstream->>DB: INSERT core.service_trigger (pending)
    DB-->>Upstream: trigger.internal_id

    loop Poll interval (10s default)
        Runner->>DB: SELECT … FOR UPDATE SKIP LOCKED
        DB-->>Runner: trigger row (locked)

        alt No trigger
            Runner->>Runner: sleep(poll_interval)
        else Trigger found
            Runner->>Runner: _process_trigger()
            Runner->>Writer: record_success(trigger, result)
            Writer->>DB: INSERT result table (idempotent)
            Writer->>DB: UPDATE file_records.status
            Writer->>DB: INSERT next service_trigger
            Writer->>DB: mark_completed(trigger)
            Writer->>DB: INSERT pipeline_event
        end
    end

    alt Conversion failure
        Runner->>Writer: record_failure(trigger, error, failure_context)
        Writer->>DB: INSERT result (status=failed)
        Writer->>DB: UPDATE file_records.status = *_failed
        Writer->>DB: mark_failed(trigger)
        Writer->>DB: INSERT pipeline_event
    end
```

## Diagram 5: Telemetry Coverage

```mermaid
graph LR
    subgraph QC["QC Service ✅ Rich"]
        Q1[started_at ✅]
        Q2[finished_at ✅]
        Q3[duration_seconds ✅]
        Q4[memory_rss_mb ✅]
        Q5[cpu_percent_avg ⚠️ partial]
        Q6[decision + metrics ✅]
        Q7[error_reason ✅]
        Q8[runner_id host_id ✅]
    end

    subgraph DICOM["DICOM Service ⚠️ Partial"]
        D1[conversion_status ✅]
        D2[source/output path ✅]
        D3[failure_context ✅]
        D4[duration_seconds ✅]
        D5[started_at ❌ missing]
        D6[memory_rss_mb ❌ missing]
        D7[runner_id host_id ✅]
    end

    subgraph UPL["Upload Service ⚠️ Partial"]
        U1[upload_status ✅]
        U2[duration_seconds ✅]
        U3[file_size ✅]
        U4[response_summary ❌ not written]
        U5[storescu not wired ❌]
    end

    subgraph EVENTS["Event Store ✅ Complete"]
        E1[226 events recorded]
        E2[causation chain]
        E3[idempotent writes]
        E4[append-only enforced]
    end
```
