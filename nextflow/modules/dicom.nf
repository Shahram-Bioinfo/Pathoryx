/*
 * DICOM conversion module.
 *
 * Inputs:  tuple(artifact_id, slide_file, scanner_type, correlation_id)
 * Outputs: converted — output DICOM directory
 *          failed    — slides that failed conversion after all retries
 *          dicom_log — JSON result record
 */

process DICOM_CONVERT {
    tag "${artifact_id}"
    label 'dicom'

    errorStrategy { task.attempt <= task.maxRetries ? 'retry' : 'ignore' }
    maxRetries 3

    input:
    tuple val(artifact_id), path(slide_file), val(scanner_type), val(correlation_id)

    output:
    tuple val(artifact_id), path("dicom_output_${artifact_id}"), val(scanner_type), val(correlation_id), emit: converted, optional: true
    tuple val(artifact_id), path(slide_file), val(scanner_type), val(correlation_id), val('dicom'), val('CONVERSION_FAILED'), emit: failed, optional: true
    path "dicom_${artifact_id}.json", emit: dicom_log

    script:
    """
    mkdir -p dicom_output_${artifact_id}

    PATHORYX_CORRELATION_ID="${correlation_id}" \
    PATHORYX_GLOBAL_RUN_ID="${params.global_run_id}" \
    python -c "
import json, sys, os, shutil
from pathlib import Path
from pipeline.config import load_config
from pipeline.services.conversion_service import ConversionService

correlation_id = os.environ.get('PATHORYX_CORRELATION_ID', '')
global_run_id  = os.environ.get('PATHORYX_GLOBAL_RUN_ID', '')

config = load_config('${params.dicom_config}')
svc = ConversionService(config)
result = svc.convert('${slide_file}')

if result.status.value not in ('completed', 'skipped_already_dicom'):
    print(f'Conversion failed: {result.failure_context}', file=sys.stderr)
    sys.exit(1)

output_dir = Path('dicom_output_${artifact_id}')
src = Path(str(result.output_path))
if src.is_dir():
    for f in src.rglob('*'):
        if f.is_file():
            shutil.copy2(f, output_dir / f.name)
else:
    shutil.copy2(src, output_dir / src.name)

log = {
    'artifact_id': '${artifact_id}',
    'status': result.status.value,
    'output_dir': str(output_dir.resolve()),
    'correlation_id': correlation_id,
    'global_run_id': global_run_id,
    'scanner_type': '${scanner_type}',
}
with open('dicom_${artifact_id}.json', 'w') as f:
    json.dump(log, f)
"
    """
}
