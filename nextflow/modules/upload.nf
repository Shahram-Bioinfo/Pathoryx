/*
 * Upload finalization module.
 *
 * Sends DICOM files to Sectra PACS via storescu (chunked batches).
 *
 * Inputs:  tuple(artifact_id, dicom_dir, scanner_type, correlation_id)
 * Outputs: upload_log — JSON result
 *          failed     — slides that failed upload after all retries
 */

process UPLOAD_FINALIZE {
    tag "${artifact_id}"
    label 'upload'

    errorStrategy { task.attempt <= task.maxRetries ? 'retry' : 'ignore' }
    maxRetries 5

    input:
    tuple val(artifact_id), path(dicom_dir), val(scanner_type), val(correlation_id)

    output:
    path "upload_${artifact_id}.json", emit: upload_log
    tuple val(artifact_id), path(dicom_dir), val(scanner_type), val(correlation_id), val('upload'), val('UPLOAD_FAILED'), emit: failed, optional: true

    script:
    """
    PATHORYX_CORRELATION_ID="${correlation_id}" \
    PATHORYX_GLOBAL_RUN_ID="${params.global_run_id}" \
    python -c "
import json, sys, os
from pathlib import Path
from pathoryx_enterprise.services.dicom.upload_utils import (
    build_cstore_commands, run_all_cstore_batches
)

correlation_id = os.environ.get('PATHORYX_CORRELATION_ID', '')
global_run_id  = os.environ.get('PATHORYX_GLOBAL_RUN_ID', '')
host           = os.environ['SECTRA_HOST']
port           = int(os.environ['SECTRA_PORT'])
remote_ae      = os.environ['SECTRA_REMOTE_AE']
local_ae       = os.environ['SECTRA_LOCAL_AE']
batch_size     = int(os.environ.get('SECTRA_CSTORE_BATCH_SIZE', '500'))
cstore_bin     = os.environ.get('SECTRA_CSTORE_BIN', 'storescu')
timeout        = int(os.environ.get('SECTRA_UPLOAD_TIMEOUT_SECONDS', '1800'))

commands = build_cstore_commands(
    input_path=Path('${dicom_dir}'),
    host=host, port=port,
    local_ae=local_ae, remote_ae=remote_ae,
    cstore_bin=cstore_bin, batch_size=batch_size,
)

all_ok, batch_results = run_all_cstore_batches(commands, timeout_seconds=timeout)

log = {
    'artifact_id': '${artifact_id}',
    'success': all_ok,
    'batches': batch_results,
    'correlation_id': correlation_id,
    'global_run_id': global_run_id,
    'scanner_type': '${scanner_type}',
    'attempt': ${task.attempt},
}
with open('upload_${artifact_id}.json', 'w') as f:
    json.dump(log, f)

if not all_ok:
    failed = next(b for b in batch_results if b['returncode'] != 0)
    print(f'Upload batch {failed[\"batch_index\"]} failed (attempt ${task.attempt}): {failed[\"stderr\"]}', file=sys.stderr)
    sys.exit(1)
"
    """
}
