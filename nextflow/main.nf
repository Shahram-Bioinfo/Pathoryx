#!/usr/bin/env nextflow
/*
 * Pathoryx — Nextflow pipeline wrapper.
 *
 * Wraps the Python service entrypoints as Nextflow stages with:
 *   - Resume capability (skip already-completed slides)
 *   - Scanner-specific QC routing
 *   - Dry-run / validation mode
 *   - Correlation ID and global_run_id propagation
 *   - Failed sample collection channel
 *
 * Usage:
 *   nextflow run main.nf -profile local --input_manifest manifest.csv
 *   nextflow run main.nf -profile local --dry_run true --input_manifest manifest.csv
 *   nextflow run main.nf -profile slurm --input_manifest manifest.csv -resume
 *
 * manifest.csv format:
 *   slide_path,global_artifact_id,scanner_type
 *   /data/scans/slide1.svs,abc123,APERIO
 *   /data/scans/slide2.ndpi,def456,HAMAMATSU
 *
 * scanner_type: APERIO | HAMAMATSU | MIRAX | GENERIC (optional, default: GENERIC)
 * global_artifact_id: leave blank to auto-generate
 */

nextflow.enable.dsl = 2

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------
params.input_manifest    = "manifest.csv"
params.babelshark_config = System.env.BABELSHARK_CONFIG ?: "configs/babelshark.yaml"
params.qc_config         = System.env.QC_SERVICE_CONFIG ?: "configs/qc.yaml"
params.dicom_config      = System.env.DICOM_CONFIG       ?: "configs/dicom.yaml"
params.outdir            = "results"
params.dry_run           = false
params.scanner_routing   = false
params.global_run_id     = java.util.UUID.randomUUID().toString()

// ---------------------------------------------------------------------------
// Include modules
// ---------------------------------------------------------------------------
include { BABELSHARK_INTAKE } from './modules/babelshark.nf'
include { QC_INFERENCE      } from './modules/qc.nf'
include { DICOM_CONVERT     } from './modules/dicom.nf'
include { UPLOAD_FINALIZE   } from './modules/upload.nf'

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------
def validate_params() {
    if (!params.input_manifest) {
        error "--input_manifest is required"
    }
    def manifest_file = file(params.input_manifest)
    if (!manifest_file.exists()) {
        error "Manifest not found: ${params.input_manifest}"
    }
    if (!params.babelshark_config) {
        error "--babelshark_config or BABELSHARK_CONFIG env var is required"
    }
    log.info """\
        Pathoryx Pipeline
        ==================
        manifest     : ${params.input_manifest}
        global_run_id: ${params.global_run_id}
        dry_run      : ${params.dry_run}
        outdir       : ${params.outdir}
        """.stripIndent()
}

// ---------------------------------------------------------------------------
// Main workflow
// ---------------------------------------------------------------------------
workflow {
    validate_params()

    // Build slide channel from manifest
    def slides_ch = Channel
        .fromPath(params.input_manifest)
        .splitCsv(header: true, strip: true)
        .map { row ->
            def artifact_id = row.global_artifact_id?.trim() ?:
                java.util.UUID.nameUUIDFromBytes(row.slide_path.bytes).toString()
            def scanner = row.scanner_type?.trim()?.toUpperCase() ?: "GENERIC"
            def correlation_id = java.util.UUID.randomUUID().toString()
            tuple(artifact_id, file(row.slide_path), scanner, correlation_id)
        }

    if (params.dry_run.toString() == "true") {
        slides_ch.count().view { n -> "DRY RUN: ${n} slides validated in manifest. No processing performed." }
        return
    }

    // Stage 1: BabelShark intake
    intake_out = BABELSHARK_INTAKE(slides_ch)

    // Stage 2: QC inference (passed slides only)
    qc_out = QC_INFERENCE(intake_out.passed)

    // Stage 3: DICOM conversion (QC-accepted slides)
    dicom_out = DICOM_CONVERT(qc_out.passed)

    // Stage 4: Upload to PACS
    upload_out = UPLOAD_FINALIZE(dicom_out.converted)

    // Collect all failures into a single report
    def failed_ch = Channel.empty()
        .mix(intake_out.failed)
        .mix(qc_out.failed)
        .mix(dicom_out.failed)
        .mix(upload_out.failed)

    failed_ch
        .map { artifact_id, slide, scanner, correlation_id, stage, reason ->
            "${artifact_id},${stage},${reason},${correlation_id},${slide}"
        }
        .collectFile(
            name: "failed_samples.csv",
            storeDir: "${params.outdir}",
            newLine: true,
            seed: "global_artifact_id,failed_stage,reason,correlation_id,slide_path"
        )
        .subscribe { f -> log.warn "Failed samples written to: ${f}" }
}
