import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft, ChevronDown, ChevronRight,
  CheckCircle2, XCircle, AlertTriangle, RefreshCcw,
  FlaskConical, ShieldCheck, Microscope, Send, HardDrive,
  type LucideIcon,
} from 'lucide-react'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { EventStream } from '../components/ui/EventStream'
import { PageLoader } from '../components/ui/LoadingSpinner'
import { StatusBadge } from '../components/ui/StatusBadge'
import { useSlideDetail } from '../hooks/useSlideDetail'
import type {
  TriggerItem,
  RecoveryEventItem,
  ExtractionResultSummary,
  QCResultSummary,
  ConversionResultSummary,
  UploadResultSummary,
} from '../types/api'
import {
  fmtBytes, fmtDatetime, fmtDuration, fmtRelative,
  fmtServiceName, fmtStageName,
} from '../utils/formatters'

// ════════════════════════════════════════════════════════════════════════
// SHARED HELPERS
// ════════════════════════════════════════════════════════════════════════

function fmtHHmmss(iso: string | null | undefined): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleTimeString('en-GB', { hour12: false }) }
  catch { return '—' }
}

function msToSec(a: string | null | undefined, b: string | null | undefined): number | null {
  if (!a || !b) return null
  const d = new Date(b).getTime() - new Date(a).getTime()
  return d >= 0 ? d / 1000 : null
}

/** Compact inline label-value pair used throughout */
function Field({ label, value, accent }: { label: string; value: React.ReactNode; accent?: string }) {
  return (
    <div className="flex items-start gap-1.5 min-w-0">
      <span
        className="text-[9px] uppercase tracking-wider flex-shrink-0 mt-px"
        style={{ color: 'var(--text-faint)', minWidth: '4.5rem' }}
      >
        {label}
      </span>
      <span
        className="text-[10px] font-mono break-all"
        style={{ color: accent ?? 'var(--text-secondary)' }}
      >
        {value ?? <span style={{ color: 'var(--text-faint)' }}>—</span>}
      </span>
    </div>
  )
}

/** Chip for header metadata */
function Chip({ label, value }: { label: string; value: string }) {
  return (
    <div
      className="flex items-center gap-1 px-2 py-0.5 rounded"
      style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)' }}
    >
      <span className="text-[8px] uppercase tracking-wider" style={{ color: 'var(--text-faint)' }}>
        {label}
      </span>
      <span className="text-[10px] font-mono" style={{ color: 'var(--text-secondary)' }}>
        {value}
      </span>
    </div>
  )
}

/** Collapsible section panel */
function InspectionPanel({
  icon: Icon, title, colorVar, defaultOpen = true, children,
}: {
  icon?: LucideIcon
  title: string
  colorVar?: string
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="glass rounded-xl overflow-hidden" style={{ border: '1px solid var(--border-default)' }}>
      <button
        type="button"
        className="w-full flex items-center gap-2 px-4 py-3 text-left"
        style={{ background: 'transparent' }}
        onClick={() => setOpen(v => !v)}
      >
        {Icon && (
          <Icon className="h-3.5 w-3.5 flex-shrink-0" style={{ color: colorVar ?? 'var(--text-muted)' }} aria-hidden />
        )}
        <span
          className="flex-1 text-[10px] font-semibold uppercase tracking-[0.18em]"
          style={{ color: colorVar ?? 'var(--text-muted)' }}
        >
          {title}
        </span>
        <ChevronDown
          className="h-3 w-3 flex-shrink-0"
          style={{
            color: 'var(--text-faint)',
            transform: open ? 'rotate(180deg)' : 'none',
            transition: 'transform 150ms ease',
          }}
          aria-hidden
        />
      </button>
      {open && (
        <div className="px-4 pb-4" style={{ borderTop: '1px solid var(--border-faint)' }}>
          {children}
        </div>
      )}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// LIFECYCLE RAIL
// Horizontal pipeline visualization — per-stage metrics derived from
// trigger data rather than just the status string.
// ════════════════════════════════════════════════════════════════════════

type NodeState = 'done' | 'failed' | 'active' | 'pending'

interface StageConfig {
  key:       string
  label:     string
  icon:      LucideIcon
  colorVar:  string
  borderVar: string
  bgVar:     string
}

const PIPELINE_STAGES: StageConfig[] = [
  { key: 'intake', label: 'Acquisition', icon: FlaskConical,
    colorVar: 'var(--stage-intake-color)', borderVar: 'var(--stage-intake-border)', bgVar: 'var(--stage-intake-bg)' },
  { key: 'qc',     label: 'QC Analysis',  icon: ShieldCheck,
    colorVar: 'var(--stage-qc-color)',     borderVar: 'var(--stage-qc-border)',     bgVar: 'var(--stage-qc-bg)' },
  { key: 'dicom',  label: 'Processing',   icon: Microscope,
    colorVar: 'var(--stage-dicom-color)',  borderVar: 'var(--stage-dicom-border)',  bgVar: 'var(--stage-dicom-bg)' },
  { key: 'upload', label: 'Transmission', icon: Send,
    colorVar: 'var(--stage-upload-color)', borderVar: 'var(--stage-upload-border)', bgVar: 'var(--stage-upload-bg)' },
]

function deriveStageFallback(key: string, status: string | null): NodeState {
  if (!status) return 'pending'
  const DONE: Record<string, string>   = { intake: 'intake_registered', qc: 'qc_passed', dicom: 'dicom_done', upload: 'uploaded' }
  const FAIL: Record<string, string>   = { qc: 'qc_failed', dicom: 'dicom_failed', upload: 'upload_failed' }
  const ORDER = ['intake', 'qc', 'dicom', 'upload']
  if (FAIL[key] && status === FAIL[key]) return 'failed'
  if (DONE[key] && status === DONE[key]) return 'done'
  if (status.startsWith(key + '_')) return 'active'
  const keyI = ORDER.indexOf(key)
  const curI = ORDER.findIndex(k => {
    if (k === 'intake') return ['detected', 'intake_registered', 'intake_running'].includes(status) || status.startsWith('intake_')
    if (k === 'upload') return status === 'uploaded' || status.startsWith('upload_')
    return status.startsWith(k + '_')
  })
  return curI > keyI ? 'done' : 'pending'
}

interface StageInfo {
  state:       NodeState
  durationSec: number | null
  retries:     number
  triggeredAt: string | null
}

function computeStageInfo(key: string, triggers: TriggerItem[], status: string | null): StageInfo {
  const ts = triggers.filter(t => t.stage_name.toLowerCase() === key)
  if (!ts.length) return { state: deriveStageFallback(key, status), durationSec: null, retries: 0, triggeredAt: null }

  const retries     = ts.reduce((s, t) => s + t.retry_count, 0)
  const hasFailed   = ts.some(t => t.trigger_status === 'failed')
  const hasComplete = ts.some(t => t.trigger_status === 'completed')
  const hasRunning  = ts.some(t => t.trigger_status === 'running' || t.trigger_status === 'pending')

  const durationSec = ts
    .filter(t => t.started_at && t.finished_at)
    .reduce((sum, t) => sum + (msToSec(t.started_at, t.finished_at) ?? 0), 0) || null

  const sorted = [...ts].sort((a, b) =>
    new Date(a.triggered_at ?? 0).getTime() - new Date(b.triggered_at ?? 0).getTime()
  )

  const state: NodeState = hasFailed && !hasComplete ? 'failed'
    : hasComplete ? 'done'
    : hasRunning  ? 'active'
    : 'pending'

  return { state, durationSec, retries, triggeredAt: sorted[0]?.triggered_at ?? null }
}

function ArtifactLifecycleRail({ triggers, status }: { triggers: TriggerItem[]; status: string | null }) {
  const stages = PIPELINE_STAGES.map(s => ({ ...s, ...computeStageInfo(s.key, triggers, status) }))

  return (
    <div className="flex items-stretch gap-0 w-full">
      {/* Scanner source node */}
      <div className="flex items-center flex-shrink-0">
        <div className="flex flex-col items-center">
          <div
            className="flex items-center justify-center h-7 w-7 rounded-md border"
            style={{ background: 'var(--accent-faint)', borderColor: 'var(--border-default)' }}
          >
            <HardDrive className="h-3.5 w-3.5" style={{ color: 'var(--text-faint)' }} aria-hidden />
          </div>
          <p className="text-[9px] uppercase tracking-wider mt-1.5" style={{ color: 'var(--text-faint)' }}>
            Scanner
          </p>
          <p className="text-[9px] font-mono" style={{ color: 'var(--chart-emerald)' }}>source</p>
        </div>
      </div>

      {stages.map((s, idx) => {
        const Icon       = s.icon
        const isDone     = s.state === 'done'
        const isFailed   = s.state === 'failed'
        const isActive   = s.state === 'active'
        const isPending  = s.state === 'pending'

        const nodeColor  = isDone ? 'var(--chart-emerald)' : isFailed ? 'var(--chart-rose)' : isActive ? s.colorVar : 'var(--text-faint)'
        const nodeBg     = isDone ? 'rgba(5,150,105,0.07)' : isFailed ? 'rgba(225,29,72,0.07)' : isActive ? s.bgVar : 'transparent'
        const nodeBorder = isDone ? 'rgba(5,150,105,0.25)' : isFailed ? 'rgba(225,29,72,0.25)' : isActive ? s.borderVar : 'var(--border-faint)'

        /* Connector line from prev node */
        const prevDone = idx === 0 ? true : stages[idx - 1].state === 'done'
        const connColor = prevDone && !isPending
          ? `linear-gradient(90deg, ${idx === 0 ? 'var(--accent)' : stages[idx - 1].colorVar}, ${s.colorVar})`
          : 'var(--border-faint)'

        return (
          <div key={s.key} className="flex items-center flex-1 min-w-0">
            {/* Connector */}
            <div
              className="flex-shrink-0"
              style={{
                width: 20, height: 1,
                background: connColor,
                opacity: prevDone && !isPending ? 0.45 : 1,
              }}
            />

            {/* Stage node */}
            <div className="flex-1 flex flex-col items-center min-w-0 px-1">
              <div
                className="flex items-center justify-center h-7 w-7 rounded-md border flex-shrink-0"
                style={{ background: nodeBg, borderColor: nodeBorder }}
              >
                {isDone    && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />}
                {isFailed  && <XCircle      className="h-3.5 w-3.5 text-rose-600 dark:text-rose-400"      />}
                {!isDone && !isFailed && (
                  <Icon
                    className="h-3.5 w-3.5"
                    style={{ color: nodeColor, opacity: isPending ? 0.3 : 1 }}
                    aria-hidden
                  />
                )}
              </div>

              <p
                className="text-[9px] uppercase tracking-wider mt-1.5 text-center leading-none"
                style={{ color: nodeColor }}
              >
                {s.label}
              </p>

              {/* Duration + retries row */}
              <div className="flex items-center gap-1 mt-0.5">
                {s.durationSec != null ? (
                  <span className="text-[9px] font-mono" style={{ color: 'var(--text-faint)' }}>
                    {fmtDuration(s.durationSec)}
                  </span>
                ) : (
                  <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>
                    {isPending ? 'pending' : ''}
                  </span>
                )}
                {s.retries > 0 && (
                  <span className="text-[9px] font-mono" style={{ color: 'var(--chart-amber)' }}>
                    ×{s.retries}
                  </span>
                )}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// TRIGGER CHAIN
// Per-trigger operational rows showing timing breakdown:
// queue delay → execution time → outcome
// ════════════════════════════════════════════════════════════════════════

const STAGE_COLOR: Record<string, string> = {
  intake: 'var(--stage-intake-color)',
  qc:     'var(--stage-qc-color)',
  dicom:  'var(--stage-dicom-color)',
  upload: 'var(--stage-upload-color)',
}

function TriggerRow({ t }: { t: TriggerItem }) {
  const [open, setOpen] = useState(false)

  const queueSec = msToSec(t.triggered_at, t.accepted_at)
  const execSec  = msToSec(t.started_at, t.finished_at)
  const stageColor = STAGE_COLOR[t.stage_name.toLowerCase()] ?? 'var(--text-muted)'
  const isError  = !!t.error_message

  return (
    <div style={{ borderBottom: '1px solid var(--border-faint)' }}>
      <div
        className="flex items-center gap-2 py-2 cursor-pointer"
        style={{ background: open ? 'var(--accent-faint)' : 'transparent', transition: 'background 80ms' }}
        onClick={() => isError && setOpen(v => !v)}
        role={isError ? 'button' : undefined}
        tabIndex={isError ? 0 : undefined}
        onKeyDown={isError ? e => e.key === 'Enter' && setOpen(v => !v) : undefined}
      >
        {/* Stage label */}
        <span
          className="text-[9px] font-mono font-semibold uppercase tracking-wider flex-shrink-0"
          style={{ color: stageColor, width: '3.5rem' }}
        >
          {fmtStageName(t.stage_name)}
        </span>

        {/* Route */}
        <span className="flex-1 min-w-0 text-[10px] font-mono truncate" style={{ color: 'var(--text-secondary)' }}>
          {fmtServiceName(t.source_service)}
          <span style={{ color: 'var(--text-faint)' }}> → </span>
          {fmtServiceName(t.target_service)}
        </span>

        {/* Status */}
        <StatusBadge status={t.trigger_status} />

        {/* Timing: queue */}
        {queueSec != null && (
          <span className="text-[9px] font-mono flex-shrink-0 hidden sm:block" style={{ color: 'var(--text-faint)' }}
                title="Queue delay">
            q{fmtDuration(queueSec)}
          </span>
        )}

        {/* Timing: exec */}
        {execSec != null && (
          <span className="text-[9px] font-mono flex-shrink-0" style={{ color: 'var(--text-muted)' }}
                title="Execution time">
            {fmtDuration(execSec)}
          </span>
        )}

        {/* Retries */}
        {t.retry_count > 0 && (
          <span className="text-[9px] font-mono flex-shrink-0" style={{ color: 'var(--chart-amber)' }}>
            ×{t.retry_count}
          </span>
        )}

        {/* Timestamp */}
        <span
          className="text-[9px] flex-shrink-0 hidden sm:block"
          style={{ color: 'var(--text-faint)' }}
          title={fmtDatetime(t.triggered_at)}
        >
          {fmtHHmmss(t.triggered_at)}
        </span>

        {/* Expand chevron for errors */}
        {isError && (
          <ChevronDown
            className="h-2.5 w-2.5 flex-shrink-0"
            style={{ color: 'var(--text-faint)', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 150ms ease' }}
            aria-hidden
          />
        )}
      </div>

      {/* Error expansion */}
      {open && isError && (
        <div className="pb-2">
          <pre
            className="mx-1 rounded px-2.5 py-2 text-[9px] font-mono break-all whitespace-pre-wrap"
            style={{
              background: 'rgba(225,29,72,0.06)',
              border:     '1px solid rgba(225,29,72,0.18)',
              color:      'var(--chart-rose)',
            }}
          >
            {t.error_message}
          </pre>
        </div>
      )}
    </div>
  )
}

function TriggerChain({ triggers }: { triggers: TriggerItem[] }) {
  if (!triggers.length) {
    return (
      <p className="text-[10px] py-2" style={{ color: 'var(--text-faint)' }}>
        No service triggers recorded
      </p>
    )
  }

  const sorted = [...triggers].sort(
    (a, b) => new Date(a.triggered_at ?? 0).getTime() - new Date(b.triggered_at ?? 0).getTime()
  )

  return (
    <div>
      {/* Column labels */}
      <div className="flex items-center gap-2 pb-1.5 mb-1" style={{ borderBottom: '1px solid var(--border-faint)' }}>
        <span className="text-[8px] uppercase tracking-wider" style={{ color: 'var(--text-faint)', width: '3.5rem' }}>Stage</span>
        <span className="flex-1 text-[8px] uppercase tracking-wider" style={{ color: 'var(--text-faint)' }}>Route</span>
        <span className="text-[8px] uppercase tracking-wider" style={{ color: 'var(--text-faint)' }}>Status</span>
        <span className="hidden sm:block text-[8px] uppercase tracking-wider" style={{ color: 'var(--text-faint)' }}>Queue</span>
        <span className="text-[8px] uppercase tracking-wider" style={{ color: 'var(--text-faint)' }}>Exec</span>
        <span className="text-[8px] uppercase tracking-wider" style={{ color: 'var(--text-faint)' }}>Retry</span>
        <span className="hidden sm:block text-[8px] uppercase tracking-wider" style={{ color: 'var(--text-faint)' }}>Time</span>
      </div>
      {sorted.map(t => <TriggerRow key={t.internal_id} t={t} />)}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// FAILURE ANALYSIS
// Aggregates all failure signals into one diagnostic block.
// Only rendered when failures exist.
// ════════════════════════════════════════════════════════════════════════

function FailureAnalysis({
  triggers,
  recovery,
}: {
  triggers: TriggerItem[]
  recovery: RecoveryEventItem[]
}) {
  const failed = triggers.filter(t => t.trigger_status === 'failed')
  if (!failed.length) return null

  const hasRecovery = recovery.length > 0
  const exhausted   = failed.filter(t => t.retry_count >= t.max_retries)

  return (
    <div
      className="rounded-xl p-4"
      style={{
        background: 'rgba(225,29,72,0.04)',
        border:     '1px solid rgba(225,29,72,0.16)',
      }}
    >
      {/* Header */}
      <div className="flex items-center gap-2 mb-3">
        <span className="h-1.5 w-1.5 rounded-full flex-shrink-0" style={{ background: 'var(--chart-rose)' }} />
        <span className="text-[9px] font-semibold uppercase tracking-widest" style={{ color: 'var(--chart-rose)' }}>
          Failure Analysis
        </span>
        {hasRecovery && (
          <span className="ml-auto text-[9px] font-mono flex items-center gap-1" style={{ color: 'var(--chart-teal)' }}>
            <RefreshCcw className="h-2.5 w-2.5" aria-hidden />
            recovery events detected
          </span>
        )}
      </div>

      {/* Fault summary */}
      <div className="flex items-center gap-3 mb-3 flex-wrap">
        <span className="text-[10px] font-mono" style={{ color: 'var(--text-muted)' }}>
          {failed.length} failed {failed.length === 1 ? 'trigger' : 'triggers'}
        </span>
        {exhausted.length > 0 && (
          <span className="text-[10px] font-mono" style={{ color: 'var(--chart-rose)' }}>
            · {exhausted.length} exhausted (manual intervention required)
          </span>
        )}
        {!hasRecovery && (
          <span className="text-[10px]" style={{ color: 'var(--text-faint)' }}>
            · no recovery events — consider queuing via Recovery Center
          </span>
        )}
      </div>

      {/* Per-trigger fault entries */}
      <div className="space-y-2">
        {failed.map(t => (
          <div key={t.internal_id} className="rounded-lg p-2.5"
               style={{ background: 'rgba(225,29,72,0.06)', border: '1px solid rgba(225,29,72,0.16)' }}>
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <span className="text-[9px] font-mono font-semibold"
                    style={{ color: 'var(--chart-rose)' }}>
                {fmtStageName(t.stage_name)}
              </span>
              <span className="text-[9px] font-mono" style={{ color: 'var(--text-faint)' }}>
                {fmtServiceName(t.target_service)}
              </span>
              <span className="text-[9px] font-mono" style={{ color: 'var(--chart-amber)' }}>
                {t.retry_count}/{t.max_retries} retries
                {t.retry_count >= t.max_retries ? ' — exhausted' : ''}
              </span>
              <span className="ml-auto text-[9px] font-mono" style={{ color: 'var(--text-faint)' }}
                    title={fmtDatetime(t.triggered_at)}>
                {fmtRelative(t.triggered_at)}
              </span>
            </div>
            {t.error_message && (
              <pre className="text-[9px] font-mono break-all whitespace-pre-wrap"
                   style={{ color: 'var(--chart-rose)' }}>
                {t.error_message}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// RECOVERY EVENTS
// ════════════════════════════════════════════════════════════════════════

function RecoveryBlock({ events }: { events: RecoveryEventItem[] }) {
  if (!events.length) return null

  return (
    <div>
      {events.map(ev => (
        <div
          key={ev.internal_id}
          className="rounded-lg p-3 mb-2"
          style={{ background: 'rgba(217,119,6,0.05)', border: '1px solid rgba(217,119,6,0.18)' }}
        >
          <div className="flex items-center gap-2 flex-wrap mb-1.5">
            <span className="text-[9px] font-mono font-semibold uppercase tracking-wide"
                  style={{ color: 'var(--chart-amber)' }}>
              {ev.change_type.replace(/_/g, ' ')}
            </span>
            <span className="text-[9px] font-mono" style={{ color: 'var(--text-faint)' }}>
              {ev.watch_folder_label}
            </span>
            <StatusBadge status={ev.review_status} className="ml-auto" />
          </div>
          {(ev.old_path || ev.new_path) && (
            <p className="text-[9px] font-mono flex items-center gap-1" style={{ color: 'var(--text-faint)' }}>
              {ev.old_path && <span className="truncate max-w-[160px]" title={ev.old_path}>{ev.old_path}</span>}
              {ev.old_path && ev.new_path && <span>→</span>}
              {ev.new_path && <span className="truncate max-w-[160px]" title={ev.new_path}>{ev.new_path}</span>}
            </p>
          )}
          {ev.recovery_outcome && (
            <div className="flex items-center gap-1.5 mt-1.5">
              <StatusBadge status={ev.recovery_outcome} />
              {ev.recovery_reason && (
                <span className="text-[9px]" style={{ color: 'var(--text-muted)' }}>{ev.recovery_reason}</span>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// ARTIFACT PROVENANCE
// Compact identity card for the right column.
// ════════════════════════════════════════════════════════════════════════

function ArtifactProvenance({
  fr, ext,
}: {
  fr: {
    global_artifact_id: string | null; original_filename: string | null
    current_filename: string | null; file_format: string | null; file_size: number | null
    scanner_id: string | null; scanner_name: string | null; artifact_type: string | null
    original_path: string | null; current_file_path: string | null
    created_at: string | null; updated_at: string | null
  }
  ext: ExtractionResultSummary | null
}) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="glass rounded-xl p-4" style={{ border: '1px solid var(--border-default)' }}>
      <div className="flex items-center gap-2 mb-3">
        <HardDrive className="h-3.5 w-3.5 flex-shrink-0" style={{ color: 'var(--text-muted)' }} aria-hidden />
        <p className="section-label mb-0">Artifact Provenance</p>
      </div>

      <div className="space-y-1.5">
        {fr.global_artifact_id && (
          <div>
            <span className="text-[8px] uppercase tracking-wider block mb-0.5" style={{ color: 'var(--text-faint)' }}>
              Artifact ID
            </span>
            <span className="text-[10px] font-mono break-all" style={{ color: 'var(--accent)' }}>
              {fr.global_artifact_id}
            </span>
          </div>
        )}

        <Field label="Scanner"   value={fr.scanner_name ?? fr.scanner_id ?? ext?.scanner_model} />
        <Field label="Format"    value={fr.file_format} />
        <Field label="Size"      value={fmtBytes(fr.file_size)} />
        <Field label="Type"      value={fr.artifact_type} />
        {ext?.slide_id && <Field label="Slide ID" value={ext.slide_id} />}
        {ext?.stain_type && <Field label="Stain" value={ext.stain_type} />}
        <Field label="Registered" value={fmtDatetime(fr.created_at)} />
        <Field label="Updated"    value={fmtDatetime(fr.updated_at)} />
      </div>

      {/* Paths — collapsible since they're long */}
      <button
        type="button"
        className="flex items-center gap-1 mt-3"
        style={{ color: 'var(--text-faint)', background: 'transparent' }}
        onClick={() => setExpanded(v => !v)}
      >
        <ChevronRight
          className="h-2.5 w-2.5"
          style={{ transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 150ms' }}
          aria-hidden
        />
        <span className="text-[9px] uppercase tracking-wider">File paths</span>
      </button>

      {expanded && (
        <div className="mt-2 space-y-1.5">
          {fr.original_path && (
            <Field label="Original" value={
              <span className="block truncate" title={fr.original_path}>{fr.original_path}</span>
            } />
          )}
          {fr.current_file_path && (
            <Field label="Current" value={
              <span className="block truncate" title={fr.current_file_path}>{fr.current_file_path}</span>
            } />
          )}
          {fr.scanner_id && <Field label="Scanner ID" value={fr.scanner_id} />}
        </div>
      )}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// STAGE RESULT BLOCKS
// Each pipeline stage gets a compact "execution report" card:
// 3-4 key operational metrics visible, full details expandable.
// ════════════════════════════════════════════════════════════════════════

function IntakeResultBlock({ ext }: { ext: ExtractionResultSummary }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="glass rounded-xl overflow-hidden" style={{ border: '1px solid var(--stage-intake-border)' }}>
      <div className="flex items-center gap-2.5 px-4 py-3"
           style={{ background: 'var(--stage-intake-bg)', borderBottom: '1px solid var(--border-faint)' }}>
        <FlaskConical className="h-3.5 w-3.5" style={{ color: 'var(--stage-intake-color)' }} aria-hidden />
        <span className="section-label mb-0" style={{ color: 'var(--stage-intake-color)', flex: 1 }}>
          Intake · Babel-Shark
        </span>
        {ext.intake_decision && <StatusBadge status={ext.intake_decision} />}
      </div>

      <div className="px-4 py-3 space-y-1.5">
        {ext.stain_type     && <Field label="Stain"        value={ext.stain_type} />}
        {ext.scanner_model  && <Field label="Scanner"      value={`${ext.scanner_vendor ?? ''} ${ext.scanner_model}`.trim()} />}
        {ext.action_taken   && <Field label="Action"       value={ext.action_taken} />}
        {ext.next_stage     && <Field label="Next stage"   value={fmtStageName(ext.next_stage)} />}
        {ext.requires_qc != null && (
          <Field label="QC required" value={ext.requires_qc ? 'Yes' : 'No'} />
        )}

        {/* Expandable: full extraction details */}
        <button
          type="button"
          className="flex items-center gap-1 pt-1"
          style={{ color: 'var(--text-faint)', background: 'transparent' }}
          onClick={() => setExpanded(v => !v)}
        >
          <ChevronRight className="h-2.5 w-2.5"
            style={{ transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 150ms' }} aria-hidden />
          <span className="text-[9px] uppercase tracking-wider">
            {expanded ? 'Hide' : 'Full extraction metadata'}
          </span>
        </button>

        {expanded && (
          <div className="pt-1 space-y-1.5">
            {ext.slide_id          && <Field label="Slide ID"    value={ext.slide_id} />}
            {ext.extraction_status && <Field label="Extraction"  value={<StatusBadge status={ext.extraction_status} />} />}
            {ext.has_internal_qc != null && (
              <Field label="Internal QC" value={ext.has_internal_qc ? 'Yes' : 'No'} />
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function QCResultBlock({ qc }: { qc: QCResultSummary }) {
  const [expanded, setExpanded] = useState(false)
  const isFailed  = qc.qc_result === 'qc_failed' || qc.decision_status === 'qc_failed'
  const borderVar = isFailed ? '1px solid rgba(225,29,72,0.22)' : '1px solid var(--stage-qc-border)'

  const durationSec = qc.started_at && qc.finished_at
    ? (new Date(qc.finished_at).getTime() - new Date(qc.started_at).getTime()) / 1000
    : qc.total_duration_seconds

  return (
    <div className="glass rounded-xl overflow-hidden" style={{ border: borderVar }}>
      <div className="flex items-center gap-2.5 px-4 py-3"
           style={{
             background: isFailed ? 'rgba(225,29,72,0.06)' : 'var(--stage-qc-bg)',
             borderBottom: '1px solid var(--border-faint)',
           }}>
        <ShieldCheck className="h-3.5 w-3.5"
          style={{ color: isFailed ? 'var(--chart-rose)' : 'var(--stage-qc-color)' }} aria-hidden />
        <span className="section-label mb-0"
              style={{ color: isFailed ? 'var(--chart-rose)' : 'var(--stage-qc-color)', flex: 1 }}>
          QC Analysis
        </span>
        {qc.qc_result && <StatusBadge status={qc.qc_result} />}
      </div>

      <div className="px-4 py-3 space-y-1.5">
        {durationSec != null && (
          <Field label="Duration"  value={fmtDuration(durationSec)} />
        )}
        {qc.scanner_name  && <Field label="Scanner"  value={qc.scanner_name} />}
        {qc.input_mode    && <Field label="Mode"     value={qc.input_mode} />}
        {qc.decision_status && <Field label="Decision"  value={<StatusBadge status={qc.decision_status} />} />}
        {qc.decision_reason && <Field label="Reason"   value={qc.decision_reason} />}
        {qc.error_reason    && (
          <Field label="Error"
            value={<span style={{ color: 'var(--chart-rose)' }}>{qc.error_reason}</span>} />
        )}

        <button
          type="button"
          className="flex items-center gap-1 pt-1"
          style={{ color: 'var(--text-faint)', background: 'transparent' }}
          onClick={() => setExpanded(v => !v)}
        >
          <ChevronRight className="h-2.5 w-2.5"
            style={{ transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 150ms' }} aria-hidden />
          <span className="text-[9px] uppercase tracking-wider">{expanded ? 'Hide' : 'Full QC metadata'}</span>
        </button>

        {expanded && (
          <div className="pt-1 space-y-1.5">
            {qc.qc_context   && <Field label="Context"     value={qc.qc_context.replace(/_/g, ' ')} />}
            {qc.next_service && <Field label="Next service" value={fmtServiceName(qc.next_service)} />}
            {qc.next_stage   && <Field label="Next stage"  value={fmtStageName(qc.next_stage)} />}
            {qc.trust_scanner_qc != null && (
              <Field label="Trust scanner" value={qc.trust_scanner_qc ? 'Yes' : 'No'} />
            )}
            {qc.source_path  && (
              <Field label="Source" value={
                <span className="truncate block" title={qc.source_path}>{qc.source_path}</span>
              } />
            )}
            <Field label="Processed" value={
              <span title={fmtDatetime(qc.processed_at)}>{fmtRelative(qc.processed_at)}</span>
            } />
          </div>
        )}
      </div>
    </div>
  )
}

function DicomResultBlock({ conv }: { conv: ConversionResultSummary }) {
  const [expanded, setExpanded] = useState(false)
  const isFailed = conv.conversion_status?.includes('fail') ?? false

  const sizeDelta = conv.input_file_size_bytes && conv.output_file_size_bytes
    ? `${fmtBytes(conv.input_file_size_bytes)} → ${fmtBytes(conv.output_file_size_bytes)}`
    : null

  return (
    <div className="glass rounded-xl overflow-hidden"
         style={{ border: isFailed ? '1px solid rgba(225,29,72,0.22)' : '1px solid var(--stage-dicom-border)' }}>
      <div className="flex items-center gap-2.5 px-4 py-3"
           style={{
             background: isFailed ? 'rgba(225,29,72,0.06)' : 'var(--stage-dicom-bg)',
             borderBottom: '1px solid var(--border-faint)',
           }}>
        <Microscope className="h-3.5 w-3.5"
          style={{ color: isFailed ? 'var(--chart-rose)' : 'var(--stage-dicom-color)' }} aria-hidden />
        <span className="section-label mb-0"
              style={{ color: isFailed ? 'var(--chart-rose)' : 'var(--stage-dicom-color)', flex: 1 }}>
          DICOM Processing
        </span>
        {conv.conversion_status && <StatusBadge status={conv.conversion_status} />}
      </div>

      <div className="px-4 py-3 space-y-1.5">
        {conv.duration_seconds != null && <Field label="Duration" value={fmtDuration(conv.duration_seconds)} />}
        {conv.conversion_tool  && <Field label="Tool"    value={conv.conversion_tool} />}
        {conv.output_format    && <Field label="Format"  value={conv.output_format} />}
        {sizeDelta             && <Field label="Size Δ"  value={sizeDelta} />}
        {conv.was_already_dicom != null && (
          <Field label="Was DICOM" value={conv.was_already_dicom ? 'Yes' : 'No'} />
        )}

        <button
          type="button"
          className="flex items-center gap-1 pt-1"
          style={{ color: 'var(--text-faint)', background: 'transparent' }}
          onClick={() => setExpanded(v => !v)}
        >
          <ChevronRight className="h-2.5 w-2.5"
            style={{ transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 150ms' }} aria-hidden />
          <span className="text-[9px] uppercase tracking-wider">{expanded ? 'Hide' : 'File paths'}</span>
        </button>

        {expanded && (
          <div className="pt-1 space-y-1.5">
            {conv.source_path && (
              <Field label="Source" value={
                <span className="truncate block" title={conv.source_path}>{conv.source_path}</span>
              } />
            )}
            {conv.output_path && (
              <Field label="Output" value={
                <span className="truncate block" title={conv.output_path}>{conv.output_path}</span>
              } />
            )}
            <Field label="Processed" value={
              <span title={fmtDatetime(conv.processed_at)}>{fmtRelative(conv.processed_at)}</span>
            } />
          </div>
        )}
      </div>
    </div>
  )
}

function UploadResultBlock({ upl }: { upl: UploadResultSummary }) {
  const [expanded, setExpanded] = useState(false)
  const isFailed = upl.upload_status?.includes('fail') ?? false

  return (
    <div className="glass rounded-xl overflow-hidden"
         style={{ border: isFailed ? '1px solid rgba(225,29,72,0.22)' : '1px solid var(--stage-upload-border)' }}>
      <div className="flex items-center gap-2.5 px-4 py-3"
           style={{
             background: isFailed ? 'rgba(225,29,72,0.06)' : 'var(--stage-upload-bg)',
             borderBottom: '1px solid var(--border-faint)',
           }}>
        <Send className="h-3.5 w-3.5"
          style={{ color: isFailed ? 'var(--chart-rose)' : 'var(--stage-upload-color)' }} aria-hidden />
        <span className="section-label mb-0"
              style={{ color: isFailed ? 'var(--chart-rose)' : 'var(--stage-upload-color)', flex: 1 }}>
          Transmission
        </span>
        {upl.upload_status && <StatusBadge status={upl.upload_status} />}
      </div>

      <div className="px-4 py-3 space-y-1.5">
        {upl.duration_seconds != null && <Field label="Duration" value={fmtDuration(upl.duration_seconds)} />}
        {upl.target_system     && <Field label="Target"  value={upl.target_system} />}
        {upl.upload_method     && <Field label="Method"  value={upl.upload_method} />}
        {upl.final_outcome     && <Field label="Outcome" value={upl.final_outcome} />}
        {upl.retry_count > 0 && (
          <Field label="Retries"
            value={<span style={{ color: 'var(--chart-amber)' }}>{upl.retry_count}</span>} />
        )}

        <button
          type="button"
          className="flex items-center gap-1 pt-1"
          style={{ color: 'var(--text-faint)', background: 'transparent' }}
          onClick={() => setExpanded(v => !v)}
        >
          <ChevronRight className="h-2.5 w-2.5"
            style={{ transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 150ms' }} aria-hidden />
          <span className="text-[9px] uppercase tracking-wider">{expanded ? 'Hide' : 'Endpoint & path'}</span>
        </button>

        {expanded && (
          <div className="pt-1 space-y-1.5">
            {upl.target_endpoint && (
              <Field label="Endpoint" value={
                <span className="truncate block" title={upl.target_endpoint}>{upl.target_endpoint}</span>
              } />
            )}
            {upl.source_path && (
              <Field label="Source" value={
                <span className="truncate block" title={upl.source_path}>{upl.source_path}</span>
              } />
            )}
            <Field label="Processed" value={
              <span title={fmtDatetime(upl.processed_at)}>{fmtRelative(upl.processed_at)}</span>
            } />
          </div>
        )}
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// PAGE
// ════════════════════════════════════════════════════════════════════════

export function SlideDetail() {
  const { artifactId } = useParams<{ artifactId: string }>()
  const { data, isPending, isError, refetch } = useSlideDetail(
    artifactId ? decodeURIComponent(artifactId) : undefined,
  )

  if (isPending) return <PageLoader />
  if (isError) return (
    <div className="mt-8">
      <ErrorBanner message="Artifact not found or backend unavailable." onRetry={refetch} />
    </div>
  )
  if (!data) return null

  const {
    file_record: fr,
    qc_result:        qc,
    conversion_result: conv,
    upload_result:    upl,
    recent_events,
    triggers,
    recovery_events,
    extraction_result: ext,
  } = data

  const displayName   = fr.original_filename ?? fr.global_artifact_id ?? 'Artifact'
  const scannerLabel  = fr.scanner_name ?? fr.scanner_id ?? ext?.scanner_model ?? '—'
  const hasFailed     = triggers.some(t => t.trigger_status === 'failed') || (fr.status?.includes('_failed') ?? false)
  const hasRecovery   = recovery_events.length > 0

  // Compute total pipeline elapsed time (first trigger → last finish)
  const allTs = triggers.flatMap(t => [t.triggered_at, t.finished_at]).filter(Boolean) as string[]
  const elapsedSec = allTs.length >= 2
    ? (Math.max(...allTs.map(t => new Date(t).getTime())) - Math.min(...allTs.map(t => new Date(t).getTime()))) / 1000
    : null

  return (
    <>
      {/* ── Breadcrumb ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 mb-4">
        <Link
          to="/slides"
          className="flex items-center gap-1.5"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--accent)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
        >
          <ArrowLeft className="h-3 w-3" aria-hidden />
          <span className="text-[10px] font-mono">Slide Explorer</span>
        </Link>
        <span className="text-[10px]" style={{ color: 'var(--border-strong)' }}>/</span>
        <span className="text-[10px] font-mono truncate max-w-xs" style={{ color: 'var(--text-secondary)' }}>
          {displayName}
        </span>
      </div>

      {/* ── Artifact Header ─────────────────────────────────────────── */}
      <div
        className="mission-card glass p-5 mb-4"
        style={{ border: '1px solid var(--border-default)' }}
      >
        {/* Identity row */}
        <div className="flex items-start gap-3 flex-wrap mb-3">
          <div className="flex-1 min-w-0">
            <h1
              className="text-xl font-semibold tracking-tight leading-tight"
              style={{ color: 'var(--text-primary)' }}
            >
              {fr.original_filename ?? 'Unnamed Artifact'}
            </h1>
            {fr.global_artifact_id && (
              <p className="text-[10px] font-mono mt-0.5" style={{ color: 'var(--accent)' }}>
                {fr.global_artifact_id}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <StatusBadge status={fr.status} />
          </div>
        </div>

        {/* Metadata chips + elapsed */}
        <div className="flex items-center gap-2 flex-wrap">
          <Chip label="Scanner" value={scannerLabel} />
          <Chip label="Format"  value={fr.file_format ?? '—'} />
          <Chip label="Size"    value={fmtBytes(fr.file_size)} />
          {ext?.stain_type && <Chip label="Stain" value={ext.stain_type} />}
          <Chip label="Registered" value={fmtRelative(fr.created_at) ?? '—'} />
          {elapsedSec != null && (
            <div
              className="flex items-center gap-1 px-2 py-0.5 rounded ml-auto"
              style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)' }}
            >
              <span className="text-[8px] uppercase tracking-wider" style={{ color: 'var(--text-faint)' }}>
                Pipeline elapsed
              </span>
              <span className="text-[10px] font-mono" style={{ color: 'var(--text-secondary)' }}>
                {fmtDuration(elapsedSec)}
              </span>
            </div>
          )}
        </div>

        {/* Current path */}
        {fr.current_file_path && (
          <p className="mt-2.5 text-[9px] font-mono truncate" style={{ color: 'var(--text-faint)' }}
             title={fr.current_file_path}>
            {fr.current_file_path}
          </p>
        )}
      </div>

      {/* ── Lifecycle Rail ───────────────────────────────────────────── */}
      <div
        className="mission-card glass p-5 mb-5"
        style={{ border: '1px solid var(--border-default)' }}
      >
        <p className="section-label">Pipeline Lifecycle</p>
        <p className="panel-anno">Stage progression · durations · retry counts</p>
        <ArtifactLifecycleRail triggers={triggers} status={fr.status} />
      </div>

      {/* ── Main intelligence grid ───────────────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">

        {/* LEFT — operational investigation */}
        <div className="xl:col-span-2 space-y-4">

          {/* Trigger Chain */}
          <InspectionPanel icon={ChevronRight} title="Service Trigger Chain" colorVar="var(--text-muted)">
            <div className="pt-3">
              <TriggerChain triggers={triggers} />
            </div>
          </InspectionPanel>

          {/* Failure Analysis — conditional */}
          {hasFailed && (
            <InspectionPanel
              icon={AlertTriangle}
              title="Failure Analysis"
              colorVar="var(--chart-rose)"
              defaultOpen
            >
              <div className="pt-3">
                <FailureAnalysis triggers={triggers} recovery={recovery_events} />
              </div>
            </InspectionPanel>
          )}

          {/* Recovery Events — conditional */}
          {hasRecovery && (
            <InspectionPanel
              icon={RefreshCcw}
              title="RecoverySentry Events"
              colorVar="var(--chart-amber)"
              defaultOpen={false}
            >
              <div className="pt-3">
                <RecoveryBlock events={recovery_events} />
              </div>
            </InspectionPanel>
          )}

          {/* Mission Event Log */}
          <InspectionPanel title="Mission Event Log" defaultOpen={!hasFailed}>
            <div className="pt-3">
              <EventStream
                events={recent_events}
                maxItems={20}
                emptyMessage="No events recorded for this artifact."
              />
            </div>
          </InspectionPanel>
        </div>

        {/* RIGHT — artifact data + service results */}
        <div className="space-y-4">
          <ArtifactProvenance fr={fr} ext={ext} />
          {ext  && <IntakeResultBlock ext={ext} />}
          {qc   && <QCResultBlock    qc={qc} />}
          {conv && <DicomResultBlock  conv={conv} />}
          {upl  && <UploadResultBlock upl={upl} />}
        </div>
      </div>
    </>
  )
}
