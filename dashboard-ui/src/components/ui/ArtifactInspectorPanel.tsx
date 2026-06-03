import { useState } from 'react'
import {
  X, ChevronDown, CheckCircle2, XCircle, FlaskConical,
  ShieldCheck, Microscope, Send, HardDrive, AlertTriangle,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { ArtifactTimeline } from './ArtifactTimeline'
import { StatusBadge } from './StatusBadge'
import { useSlideDetail } from '../../hooks/useSlideDetail'
import type {
  TriggerItem,
  ExtractionResultSummary,
  QCResultSummary,
  ConversionResultSummary,
  UploadResultSummary,
  RecoveryEventItem,
} from '../../types/api'
import {
  fmtBytes,
  fmtDatetime,
  fmtDuration,
  fmtRelative,
  fmtServiceName,
  fmtStageName,
} from '../../utils/formatters'

// ─── Stage rail ───────────────────────────────────────────────────────────────

type StageState = 'done' | 'active' | 'failed' | 'skipped' | 'pending'

interface StageInfo {
  key:       string
  label:     string
  icon:      LucideIcon
  colorVar:  string
  borderVar: string
  statusSet: string[]
  doneSet:   string[]
  failSet:   string[]
}

const RAIL_STAGES: StageInfo[] = [
  {
    key: 'intake', label: 'Acquisition', icon: FlaskConical,
    colorVar: 'var(--stage-intake-color)', borderVar: 'var(--stage-intake-border)',
    statusSet: ['detected','intake_running','intake_registered'],
    doneSet:   ['intake_registered'], failSet: [],
  },
  {
    key: 'qc', label: 'Analysis', icon: ShieldCheck,
    colorVar: 'var(--stage-qc-color)', borderVar: 'var(--stage-qc-border)',
    statusSet: ['qc_pending','qc_running','qc_passed','qc_failed'],
    doneSet:   ['qc_passed'], failSet: ['qc_failed'],
  },
  {
    key: 'dicom', label: 'Processing', icon: Microscope,
    colorVar: 'var(--stage-dicom-color)', borderVar: 'var(--stage-dicom-border)',
    statusSet: ['dicom_pending','dicom_running','dicom_done','dicom_failed'],
    doneSet:   ['dicom_done'], failSet: ['dicom_failed'],
  },
  {
    key: 'upload', label: 'Transmission', icon: Send,
    colorVar: 'var(--stage-upload-color)', borderVar: 'var(--stage-upload-border)',
    statusSet: ['upload_pending','upload_running','uploaded','upload_failed'],
    doneSet:   ['uploaded'], failSet: ['upload_failed'],
  },
]

const STAGE_ORDER = ['intake', 'qc', 'dicom', 'upload']

function getRailState(stage: StageInfo, status: string | null, triggers: TriggerItem[]): StageState {
  if (!status) return 'pending'
  if (stage.doneSet.includes(status)) return 'done'
  if (stage.failSet.includes(status)) return 'failed'
  if (stage.statusSet.includes(status)) return 'active'

  const stageTriggers = triggers.filter(t => t.stage_name.toLowerCase() === stage.key)
  if (stageTriggers.some(t => t.trigger_status === 'completed')) return 'done'
  if (stageTriggers.some(t => t.trigger_status === 'failed'))    return 'failed'

  const si = STAGE_ORDER.indexOf(stage.key)
  const ci = STAGE_ORDER.findIndex(k => {
    if (k === 'upload' && status === 'uploaded') return true
    if (k === 'intake' && status === 'intake_registered') return true
    return status.startsWith(k + '_')
  })
  return ci > si ? 'done' : 'pending'
}

function getStageStats(key: string, triggers: TriggerItem[]) {
  const ts = triggers.filter(t => t.stage_name.toLowerCase() === key)
  const totalRetries = ts.reduce((a, t) => a + t.retry_count, 0)
  const durations = ts
    .filter(t => t.started_at && t.finished_at)
    .map(t => (new Date(t.finished_at!).getTime() - new Date(t.started_at!).getTime()) / 1000)
  const avgDur = durations.length ? durations.reduce((a, d) => a + d, 0) / durations.length : null
  return { count: ts.length, retries: totalRetries, avgDuration: avgDur }
}

function InspectorLineageRail({
  status,
  triggers,
}: {
  status: string | null
  triggers: TriggerItem[]
}) {
  return (
    <div className="flex items-stretch gap-0">
      {RAIL_STAGES.map((stage, idx) => {
        const state  = getRailState(stage, status, triggers)
        const stats  = getStageStats(stage.key, triggers)
        const Icon   = stage.icon
        const isLast = idx === RAIL_STAGES.length - 1

        const nodeColor =
          state === 'done'    ? 'var(--chart-emerald)' :
          state === 'failed'  ? 'var(--chart-rose)'    :
          state === 'active'  ? stage.colorVar          :
          'var(--text-faint)'

        const nodeBg =
          state === 'done'    ? 'rgba(5,150,105,0.07)'  :
          state === 'failed'  ? 'rgba(225,29,72,0.07)'  :
          state === 'active'  ? `color-mix(in srgb, ${stage.colorVar} 10%, transparent)` :
          'transparent'

        const nodeBorder =
          state === 'done'    ? 'rgba(5,150,105,0.22)'  :
          state === 'failed'  ? 'rgba(225,29,72,0.22)'  :
          state === 'active'  ? stage.borderVar          :
          'var(--border-faint)'

        return (
          <div key={stage.key} className="flex items-center flex-1 min-w-0">
            {/* Stage node */}
            <div className="flex flex-col items-center flex-1 min-w-0">
              {/* Node chip */}
              <div
                className="flex items-center gap-1.5 px-2 py-1.5 rounded-md w-full"
                style={{
                  background:  nodeBg,
                  border:      `1px solid ${nodeBorder}`,
                }}
              >
                {/* Icon / outcome */}
                <span className="flex-shrink-0">
                  {state === 'done'   && <CheckCircle2 className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />}
                  {state === 'failed' && <XCircle      className="h-3 w-3 text-rose-600    dark:text-rose-400"    />}
                  {(state === 'active' || state === 'pending') && (
                    <Icon
                      className="h-3 w-3"
                      style={{ color: state === 'active' ? stage.colorVar : 'var(--text-faint)', opacity: state === 'pending' ? 0.35 : 1 }}
                    />
                  )}
                </span>

                {/* Label */}
                <div className="min-w-0 flex-1">
                  <p
                    className="text-[9px] font-semibold uppercase tracking-wider leading-none truncate"
                    style={{ color: nodeColor }}
                  >
                    {stage.label}
                  </p>
                  {/* Stats row */}
                  <div className="flex items-center gap-1.5 mt-0.5">
                    {stats.avgDuration != null && (
                      <span className="text-[8px] font-mono" style={{ color: 'var(--text-faint)' }}>
                        {fmtDuration(stats.avgDuration)}
                      </span>
                    )}
                    {stats.retries > 0 && (
                      <span className="text-[8px] font-mono" style={{ color: 'var(--chart-amber)' }}>
                        ×{stats.retries}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* Connector rail */}
            {!isLast && (
              <div
                className="flex-shrink-0 mx-1"
                style={{
                  width:      20,
                  height:     1,
                  background: state === 'done'
                    ? `linear-gradient(90deg, var(--chart-emerald), ${RAIL_STAGES[idx + 1].colorVar})`
                    : 'var(--border-faint)',
                  opacity: state === 'done' ? 0.4 : 1,
                }}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}

// ─── Metadata row ─────────────────────────────────────────────────────────────

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div
      className="flex items-start gap-2 py-1.5"
      style={{ borderBottom: '1px solid var(--border-faint)' }}
    >
      <span
        className="flex-shrink-0 text-[9px] uppercase tracking-wider"
        style={{ color: 'var(--text-faint)', width: '7rem' }}
      >
        {label}
      </span>
      <span className="flex-1 text-[10px] font-mono break-all" style={{ color: 'var(--text-secondary)' }}>
        {value ?? <span style={{ color: 'var(--text-faint)' }}>—</span>}
      </span>
    </div>
  )
}

// ─── Collapsible section ──────────────────────────────────────────────────────

function InspectorSection({
  label,
  colorVar,
  icon: Icon,
  defaultOpen = false,
  children,
}: {
  label:       string
  colorVar:    string
  icon?:       LucideIcon
  defaultOpen?: boolean
  children:    React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div style={{ borderTop: '1px solid var(--border-faint)' }}>
      <button
        type="button"
        className="w-full flex items-center gap-2 py-2.5 text-left"
        onClick={() => setOpen(v => !v)}
        style={{ background: 'transparent' }}
      >
        {Icon && (
          <Icon className="h-3 w-3 flex-shrink-0" style={{ color: colorVar }} aria-hidden />
        )}
        {!Icon && (
          <span
            className="h-3 w-[2px] flex-shrink-0 rounded-full"
            style={{ background: colorVar }}
          />
        )}
        <span
          className="flex-1 text-[9px] font-semibold uppercase tracking-widest"
          style={{ color: 'var(--text-muted)' }}
        >
          {label}
        </span>
        <ChevronDown
          className="h-3 w-3 flex-shrink-0"
          style={{
            color:      'var(--text-faint)',
            transform:  open ? 'rotate(180deg)' : 'none',
            transition: 'transform 150ms ease',
          }}
          aria-hidden
        />
      </button>
      {open && <div className="pb-3">{children}</div>}
    </div>
  )
}

// ─── Failure forensics block ──────────────────────────────────────────────────

function FailureForensicsBlock({
  triggers,
  recovery,
}: {
  triggers: TriggerItem[]
  recovery: RecoveryEventItem[]
}) {
  const failed = triggers.filter(t => t.trigger_status === 'failed')
  if (!failed.length) return null

  const hasRecovery = recovery.length > 0

  return (
    <div
      className="rounded-lg p-3 mb-0"
      style={{
        background: 'rgba(225,29,72,0.04)',
        border:     '1px solid rgba(225,29,72,0.16)',
      }}
    >
      <div className="flex items-center gap-2 mb-2.5">
        <span className="h-1.5 w-1.5 rounded-full flex-shrink-0" style={{ background: 'var(--chart-rose)' }} />
        <span className="text-[9px] font-semibold uppercase tracking-widest" style={{ color: 'var(--chart-rose)' }}>
          Failure Analysis
        </span>
        {hasRecovery && (
          <span
            className="ml-auto text-[9px] font-mono tracking-wide"
            style={{ color: 'var(--chart-amber)' }}
          >
            Recovery events detected
          </span>
        )}
      </div>

      <div className="space-y-2">
        {failed.map(t => (
          <div key={t.internal_id}>
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <span className="text-[9px] font-mono" style={{ color: 'var(--text-faint)' }}>
                {fmtStageName(t.stage_name)}
              </span>
              <span style={{ color: 'var(--border-faint)' }}>·</span>
              <span className="text-[9px] font-mono" style={{ color: 'var(--text-muted)' }}>
                {fmtServiceName(t.target_service)}
              </span>
              {t.retry_count > 0 && (
                <>
                  <span style={{ color: 'var(--border-faint)' }}>·</span>
                  <span className="text-[9px] font-mono" style={{ color: 'var(--chart-amber)' }}>
                    {t.retry_count} retries
                  </span>
                </>
              )}
              <span
                className="ml-auto text-[9px] font-mono"
                style={{ color: 'var(--text-faint)' }}
                title={fmtDatetime(t.triggered_at)}
              >
                {fmtRelative(t.triggered_at)}
              </span>
            </div>
            {t.error_message && (
              <pre
                className="text-[9px] font-mono break-all whitespace-pre-wrap rounded px-2 py-1.5"
                style={{
                  background: 'rgba(225,29,72,0.08)',
                  border:     '1px solid rgba(225,29,72,0.20)',
                  color:      'var(--chart-rose)',
                }}
              >
                {t.error_message}
              </pre>
            )}
          </div>
        ))}
      </div>

      {!hasRecovery && (
        <p className="text-[9px] mt-2.5" style={{ color: 'var(--text-faint)' }}>
          No recovery events recorded — consider queuing via Recovery Center
        </p>
      )}
    </div>
  )
}

// ─── Recovery events block ────────────────────────────────────────────────────

function RecoveryBlock({ events }: { events: RecoveryEventItem[] }) {
  if (!events.length) return null
  return (
    <div>
      {events.map(ev => (
        <div
          key={ev.internal_id}
          className="rounded-lg p-2.5 mb-2"
          style={{
            background: 'rgba(217,119,6,0.05)',
            border:     '1px solid rgba(217,119,6,0.18)',
          }}
        >
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span
              className="text-[9px] font-mono font-semibold uppercase tracking-wide"
              style={{ color: 'var(--chart-amber)' }}
            >
              {ev.change_type.replace(/_/g, ' ')}
            </span>
            <span className="text-[9px] font-mono" style={{ color: 'var(--text-faint)' }}>
              {ev.watch_folder_label}
            </span>
            <StatusBadge status={ev.review_status} className="ml-auto" />
          </div>

          {(ev.old_path || ev.new_path) && (
            <p className="text-[9px] font-mono mb-1 flex items-center gap-1" style={{ color: 'var(--text-faint)' }}>
              {ev.old_path && <span className="truncate max-w-[160px]" title={ev.old_path}>{ev.old_path}</span>}
              {ev.old_path && ev.new_path && <span>→</span>}
              {ev.new_path && <span className="truncate max-w-[160px]" title={ev.new_path}>{ev.new_path}</span>}
            </p>
          )}

          {ev.recovery_outcome && (
            <div className="flex items-center gap-1.5 mt-1">
              <StatusBadge status={ev.recovery_outcome} />
              {ev.recovery_reason && (
                <span className="text-[9px]" style={{ color: 'var(--text-muted)' }}>
                  {ev.recovery_reason}
                </span>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ─── Key-value chip row ───────────────────────────────────────────────────────

function ChipRow({ items }: { items: { label: string; value: string }[] }) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {items.filter(i => i.value && i.value !== '—').map(({ label, value }) => (
        <div
          key={label}
          className="flex items-center gap-1 px-1.5 py-0.5 rounded"
          style={{
            background: 'var(--surface-inset)',
            border:     '1px solid var(--border-faint)',
          }}
        >
          <span className="text-[8px] uppercase tracking-wide" style={{ color: 'var(--text-faint)' }}>
            {label}
          </span>
          <span className="text-[9px] font-mono" style={{ color: 'var(--text-secondary)' }}>
            {value}
          </span>
        </div>
      ))}
    </div>
  )
}

// ─── Metadata panel for extraction result ────────────────────────────────────

function IntakeMeta({ ext }: { ext: ExtractionResultSummary }) {
  return (
    <div>
      <MetaRow label="Slide ID"        value={ext.slide_id} />
      <MetaRow label="Stain type"      value={ext.stain_type} />
      <MetaRow label="Scanner model"   value={ext.scanner_model} />
      <MetaRow label="Scanner vendor"  value={ext.scanner_vendor} />
      <MetaRow label="Intake decision" value={ext.intake_decision ? <StatusBadge status={ext.intake_decision} /> : null} />
      <MetaRow label="Action"          value={ext.action_taken} />
      <MetaRow label="Extraction"      value={ext.extraction_status ? <StatusBadge status={ext.extraction_status} /> : null} />
      <MetaRow label="Requires QC"     value={ext.requires_qc != null ? (ext.requires_qc ? 'Yes' : 'No') : null} />
      <MetaRow label="Next stage"      value={ext.next_stage ? fmtStageName(ext.next_stage) : null} />
    </div>
  )
}

function QCMeta({ qc }: { qc: QCResultSummary }) {
  const dur = qc.started_at && qc.finished_at
    ? (new Date(qc.finished_at).getTime() - new Date(qc.started_at).getTime()) / 1000
    : qc.total_duration_seconds
  return (
    <div>
      <MetaRow label="Result"      value={qc.qc_result ? <StatusBadge status={qc.qc_result} /> : null} />
      <MetaRow label="Decision"    value={qc.decision_status ? <StatusBadge status={qc.decision_status} /> : null} />
      <MetaRow label="Reason"      value={qc.decision_reason} />
      {qc.error_reason && (
        <MetaRow
          label="Error"
          value={<span style={{ color: 'var(--chart-rose)' }}>{qc.error_reason}</span>}
        />
      )}
      <MetaRow label="Context"     value={qc.qc_context?.replace(/_/g, ' ')} />
      <MetaRow label="Input mode"  value={qc.input_mode} />
      <MetaRow label="Duration"    value={fmtDuration(dur)} />
      <MetaRow label="Processed"   value={<span title={fmtDatetime(qc.processed_at)}>{fmtRelative(qc.processed_at)}</span>} />
    </div>
  )
}

function DicomMeta({ conv }: { conv: ConversionResultSummary }) {
  return (
    <div>
      <MetaRow label="Status"      value={conv.conversion_status ? <StatusBadge status={conv.conversion_status} /> : null} />
      <MetaRow label="Tool"        value={conv.conversion_tool} />
      <MetaRow label="Format"      value={conv.output_format} />
      <MetaRow label="Was DICOM"   value={conv.was_already_dicom != null ? (conv.was_already_dicom ? 'Yes' : 'No') : null} />
      <MetaRow label="Input size"  value={fmtBytes(conv.input_file_size_bytes)} />
      <MetaRow label="Output size" value={fmtBytes(conv.output_file_size_bytes)} />
      <MetaRow label="Duration"    value={fmtDuration(conv.duration_seconds)} />
      <MetaRow label="Processed"   value={<span title={fmtDatetime(conv.processed_at)}>{fmtRelative(conv.processed_at)}</span>} />
    </div>
  )
}

function UploadMeta({ upl }: { upl: UploadResultSummary }) {
  return (
    <div>
      <MetaRow label="Status"   value={upl.upload_status ? <StatusBadge status={upl.upload_status} /> : null} />
      <MetaRow label="Outcome"  value={upl.final_outcome} />
      <MetaRow label="Target"   value={upl.target_system} />
      <MetaRow label="Method"   value={upl.upload_method} />
      <MetaRow
        label="Retries"
        value={
          <span style={upl.retry_count > 0 ? { color: 'var(--chart-rose)' } : undefined}>
            {String(upl.retry_count)}
          </span>
        }
      />
      <MetaRow label="Duration" value={fmtDuration(upl.duration_seconds)} />
      <MetaRow label="Processed" value={<span title={fmtDatetime(upl.processed_at)}>{fmtRelative(upl.processed_at)}</span>} />
    </div>
  )
}

// ─── Main panel ───────────────────────────────────────────────────────────────

interface Props {
  artifactId: string
  onClose:    () => void
}

export function ArtifactInspectorPanel({ artifactId, onClose }: Props) {
  const { data, isPending, isError, refetch } = useSlideDetail(artifactId)

  // ── Loading ──
  if (isPending) {
    return (
      <div
        className="mission-card glass rounded-xl p-5"
        style={{ border: '1px solid var(--border-default)' }}
      >
        <div className="flex items-center justify-between mb-4">
          <div className="ops-skeleton h-4 w-48 rounded" />
          <button type="button" onClick={onClose} className="btn-ghost-ops p-1" aria-label="Close">
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
        <div className="space-y-2.5">
          {[80, 60, 90, 50, 70].map((w, i) => (
            <div key={i} className={`ops-skeleton h-3 rounded`} style={{ width: `${w}%` }} />
          ))}
        </div>
      </div>
    )
  }

  // ── Error ──
  if (isError || !data) {
    return (
      <div
        className="glass rounded-xl p-5"
        style={{ border: '1px solid var(--border-default)' }}
      >
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
            Failed to load artifact
          </span>
          <button type="button" onClick={onClose} className="btn-ghost-ops p-1" aria-label="Close">
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
        <button type="button" className="btn-ops text-xs" onClick={() => refetch()}>
          Retry
        </button>
      </div>
    )
  }

  const { file_record: fr, qc_result: qc, conversion_result: conv, upload_result: upl,
          recent_events, triggers, recovery_events, extraction_result: ext } = data

  const displayName   = fr.original_filename ?? fr.global_artifact_id ?? 'Artifact'
  const scannerLabel  = fr.scanner_name ?? fr.scanner_id ?? ext?.scanner_model ?? '—'
  const hasFailed     = triggers.some(t => t.trigger_status === 'failed') ||
                        (fr.status?.includes('_failed') ?? false)
  const hasRecovery   = recovery_events.length > 0

  return (
    <div
      className="mission-card glass rounded-xl overflow-hidden"
      style={{ border: '1px solid var(--border-default)' }}
    >
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div
        className="flex items-start gap-3 px-4 py-3"
        style={{ borderBottom: '1px solid var(--border-faint)' }}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <h2
              className="text-sm font-semibold leading-tight truncate max-w-sm"
              style={{ color: 'var(--text-primary)' }}
              title={displayName}
            >
              {displayName}
            </h2>
            <StatusBadge status={fr.status} />
          </div>
          {fr.global_artifact_id && (
            <p
              className="text-[9px] font-mono truncate"
              style={{ color: 'var(--accent)' }}
              title={fr.global_artifact_id}
            >
              {fr.global_artifact_id}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="btn-ghost-ops p-1 flex-shrink-0"
          aria-label="Close inspector"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>

      {/* ── Scrollable body ─────────────────────────────────────────────────── */}
      <div className="overflow-y-auto scrollbar-none" style={{ maxHeight: 'calc(100vh - 8rem)' }}>

        {/* Metadata chips */}
        <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--border-faint)' }}>
          <ChipRow items={[
            { label: 'scanner', value: scannerLabel },
            { label: 'format',  value: fr.file_format ?? '—' },
            { label: 'size',    value: fmtBytes(fr.file_size) },
            { label: 'type',    value: fr.artifact_type ?? '—' },
            ...(ext?.slide_id ? [{ label: 'slide id', value: ext.slide_id }] : []),
          ]} />
          {fr.current_file_path && (
            <p
              className="mt-2 text-[9px] font-mono truncate"
              style={{ color: 'var(--text-faint)' }}
              title={fr.current_file_path}
            >
              {fr.current_file_path}
            </p>
          )}
        </div>

        {/* ── Pipeline lineage rail ─────────────────────────────────────────── */}
        <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--border-faint)' }}>
          <p className="section-label mb-2.5">Pipeline Lineage</p>
          <InspectorLineageRail status={fr.status} triggers={triggers} />
        </div>

        {/* ── Failure forensics (conditional) ──────────────────────────────── */}
        {hasFailed && (
          <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--border-faint)' }}>
            <p className="section-label mb-2.5" style={{ color: 'var(--chart-rose)' }}>
              Failure Forensics
            </p>
            <FailureForensicsBlock triggers={triggers} recovery={recovery_events} />
          </div>
        )}

        {/* ── Recovery events (conditional) ────────────────────────────────── */}
        {hasRecovery && (
          <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--border-faint)' }}>
            <div className="flex items-center gap-2 mb-2.5">
              <AlertTriangle className="h-3 w-3 flex-shrink-0" style={{ color: 'var(--chart-amber)' }} aria-hidden />
              <p className="section-label mb-0" style={{ color: 'var(--chart-amber)' }}>
                Recovery Events
              </p>
            </div>
            <RecoveryBlock events={recovery_events} />
          </div>
        )}

        {/* ── Operational timeline ─────────────────────────────────────────── */}
        <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--border-faint)' }}>
          <p className="section-label mb-2.5">Operational Timeline</p>
          <ArtifactTimeline
            triggers={triggers}
            events={recent_events}
            recovery={recovery_events}
            maxHeight="16rem"
          />
        </div>

        {/* ── Collapsible metadata sections ────────────────────────────────── */}
        <div className="px-4">
          <InspectorSection
            label="Artifact Data"
            colorVar="var(--text-muted)"
            icon={HardDrive}
          >
            <MetaRow label="Artifact ID"    value={<span className="text-[9px]">{fr.global_artifact_id}</span>} />
            <MetaRow label="Filename"       value={fr.original_filename} />
            <MetaRow label="Current name"   value={fr.current_filename} />
            <MetaRow label="Format"         value={fr.file_format} />
            <MetaRow label="Size"           value={fmtBytes(fr.file_size)} />
            <MetaRow label="Scanner ID"     value={fr.scanner_id} />
            <MetaRow label="Scanner"        value={fr.scanner_name} />
            <MetaRow label="Artifact type"  value={fr.artifact_type} />
            <MetaRow label="Registered"     value={fmtDatetime(fr.created_at)} />
            <MetaRow label="Last updated"   value={fmtDatetime(fr.updated_at)} />
            {fr.original_path && (
              <MetaRow
                label="Original path"
                value={<span className="truncate block" title={fr.original_path}>{fr.original_path}</span>}
              />
            )}
          </InspectorSection>

          {ext && (
            <InspectorSection
              label="Intake · Babel-Shark Service"
              colorVar="var(--stage-intake-color)"
              icon={FlaskConical}
            >
              <IntakeMeta ext={ext} />
            </InspectorSection>
          )}

          {qc && (
            <InspectorSection
              label="QC Analysis"
              colorVar="var(--stage-qc-color)"
              icon={ShieldCheck}
              defaultOpen={qc.qc_result === 'qc_failed' || qc.decision_status === 'qc_failed'}
            >
              <QCMeta qc={qc} />
            </InspectorSection>
          )}

          {conv && (
            <InspectorSection
              label="DICOM Processing"
              colorVar="var(--stage-dicom-color)"
              icon={Microscope}
            >
              <DicomMeta conv={conv} />
            </InspectorSection>
          )}

          {upl && (
            <InspectorSection
              label="Transmission"
              colorVar="var(--stage-upload-color)"
              icon={Send}
              defaultOpen={upl.upload_status === 'upload_failed'}
            >
              <UploadMeta upl={upl} />
            </InspectorSection>
          )}
        </div>
      </div>
    </div>
  )
}
