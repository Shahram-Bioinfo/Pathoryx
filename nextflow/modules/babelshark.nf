/*
 * BabelShark intake module.
 *
 * Inputs:  tuple(artifact_id, slide_file, scanner_type, correlation_id)
 * Outputs: passed — slides that registered successfully
 *          failed — slides that failed, with (artifact_id, slide, scanner, correlation_id, stage, reason)
 *          intake_log — JSON record
 */

process BABELSHARK_INTAKE {
    tag "${artifact_id}"
    label 'intake'

    errorStrategy { task.attempt <= task.maxRetries ? 'retry' : 'ignore' }
    maxRetries 3

    input:
    tuple val(artifact_id), path(slide_file), val(scanner_type), val(correlation_id)

    output:
    tuple val(artifact_id), path(slide_file), val(scanner_type), val(correlation_id), emit: passed,  optional: true
    tuple val(artifact_id), path(slide_file), val(scanner_type), val(correlation_id), val('intake'), val('FAILED'), emit: failed, optional: true
    path "intake_${artifact_id}.json", emit: intake_log

    script:
    """
    PATHORYX_CORRELATION_ID="${correlation_id}" \
    PATHORYX_GLOBAL_RUN_ID="${params.global_run_id}" \
    python -c "
import json, sys, os
from pathlib import Path
from pathoryx_enterprise.services.babelshark.core.database_manager import DatabaseManager

correlation_id = os.environ.get('PATHORYX_CORRELATION_ID', '')
global_run_id  = os.environ.get('PATHORYX_GLOBAL_RUN_ID', '')

db = DatabaseManager()
decision = db.classify_intake('${slide_file}')

if decision['action'] == 'skip_duplicate':
    result = {
        'status': 'skipped', 'artifact_id': '${artifact_id}',
        'reason': 'duplicate', 'correlation_id': correlation_id,
    }
else:
    reg = db.register_collected_file(
        source_path='${slide_file}',
        staged_path='${slide_file}',
        file_name=Path('${slide_file}').name,
        file_format=Path('${slide_file}').suffix.lstrip('.'),
        file_size=Path('${slide_file}').stat().st_size,
        intake_decision=decision,
        scanner_type='${scanner_type}',
        correlation_id=correlation_id,
        global_run_id=global_run_id,
    )
    result = {
        'status': 'registered',
        'artifact_id': '${artifact_id}',
        'record_id': reg['record_id'],
        'global_artifact_id': reg['global_artifact_id'],
        'correlation_id': correlation_id,
        'scanner_type': '${scanner_type}',
    }

with open('intake_${artifact_id}.json', 'w') as f:
    json.dump(result, f)
"
    """
}
