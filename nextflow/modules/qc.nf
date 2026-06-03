/*
 * QC inference module.
 *
 * Inputs:  tuple(artifact_id, slide_file, scanner_type, correlation_id)
 * Outputs: passed  — QC-accepted slides
 *          failed  — QC-rejected or errored slides
 *          qc_log  — JSON result record
 *
 * Scanner routing: when params.scanner_routing is true, the QC config is
 * adjusted per scanner type (APERIO/HAMAMATSU/MIRAX/GENERIC).
 */

process QC_INFERENCE {
    tag "${artifact_id}"
    label 'qc'

    errorStrategy { task.attempt <= task.maxRetries ? 'retry' : 'ignore' }
    maxRetries 2

    input:
    tuple val(artifact_id), path(slide_file), val(scanner_type), val(correlation_id)

    output:
    tuple val(artifact_id), path(slide_file), val(scanner_type), val(correlation_id), emit: passed, optional: true
    tuple val(artifact_id), path(slide_file), val(scanner_type), val(correlation_id), val('qc'), val('REJECTED'), emit: failed, optional: true
    path "qc_${artifact_id}.json", emit: qc_log

    script:
    def scanner_env = params.scanner_routing ? "PATHORYX_SCANNER_TYPE=${scanner_type}" : ""
    """
    PATHORYX_CORRELATION_ID="${correlation_id}" \
    PATHORYX_GLOBAL_RUN_ID="${params.global_run_id}" \
    ${scanner_env} \
    python -c "
import json, sys, os
from pathlib import Path
from pipeline.config import load_config
from pipeline.services.model_registry import ModelRegistry
from pipeline.services.qc_inference_service import SlideQcInferenceService
from pipeline.services.qc_decision_service import SlideQcDecisionService

correlation_id  = os.environ.get('PATHORYX_CORRELATION_ID', '')
global_run_id   = os.environ.get('PATHORYX_GLOBAL_RUN_ID', '')
scanner_type    = os.environ.get('PATHORYX_SCANNER_TYPE', 'GENERIC')

config = load_config('${params.qc_config}')
inference_svc = SlideQcInferenceService(config)
decision_svc  = SlideQcDecisionService(config)

source = Path('${slide_file}').resolve()
inference_result = inference_svc.process_slide(source)
decision = decision_svc.decide(inference_result, source)

result = {
    'artifact_id': '${artifact_id}',
    'decision_status': decision['decision_status'],
    'decision_reason': decision['decision_reason'],
    'scanner_type': scanner_type,
    'correlation_id': correlation_id,
    'global_run_id': global_run_id,
}

with open('qc_${artifact_id}.json', 'w') as f:
    json.dump(result, f)

if decision['decision_status'] != 'accepted':
    print(f'QC rejected [{scanner_type}]: {decision[\"decision_reason\"]}', file=sys.stderr)
    sys.exit(1)
"
    """
}
