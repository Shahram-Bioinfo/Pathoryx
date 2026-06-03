import {
  AlertTriangle, ChevronDown, ChevronRight, ChevronUp,
  ClipboardCheck, FileText, RefreshCcw, Search, Zap,
  type LucideIcon,
} from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { EventStream } from '../components/ui/EventStream'
import { PageHeader } from '../components/ui/PageHeader'
import { TelemetryMetricRow } from '../components/ui/TelemetryMetricRow'
import { SkeletonRow } from '../components/ui/LoadingSpinner'
import { StatusBadge } from '../components/ui/StatusBadge'
import { TechnicianReviewDrawer } from '../components/ui/TechnicianReviewDrawer'
import { useFailures } from '../hooks/useFailures'
import { useMonitoredFiles } from '../hooks/useMonitoredFiles'
import type { FailedSlideItem, FailedTriggerItem, MonitoredFileItem } from '../types/api'
import {
  fmtDatetime, fmtDuration, fmtRelative, fmtServiceName, fmtStageName,
} from '../utils/formatters'

// ─── Narrative helpers ────────────────────────────────────────────────────────

/** Parse a brief error category from a raw error message. */
function errorCategory(msg: string | null): string {
  if (!msg) return ''
  const l = msg.toLowerCase()
  if (l.includes('not found') || l.includes('no such file') || l.includes('missing')) return 'file not found'
  if (l.includes('timeout') || l.includes('timed out')) return 'timeout'
  if (l.includes('permission') || l.includes('access denied')) return 'permission error'
  if (l.includes('connection') || l.includes('unreachable')) return 'connection error'
  if (l.includes('inference') || l.includes('model')) return 'inference error'
  if (l.includes('unsupported') || l.includes('format')) return 'unsupported format'
  if (l.includes('memory') || l.includes('oom')) return 'memory error'
  return ''
}

/** Human-readable summary for one service's failures. */
function serviceNarrative(svc: string, triggers: FailedTriggerItem[]): string {
  const n = triggers.length
  if (n === 0) return `All ${fmtServiceName(svc)} trigger routes nominal.`
  const exhausted = triggers.filter(t => t.retry_count >= t.max_retries).length
  const cats = [...new Set(triggers.map(t => errorCategory(t.error_message)).filter(Boolean))].slice(0, 2)
  let s = `${n} failed ${n === 1 ? 'trigger' : 'triggers'}`
  if (cats.length) s += ` — ${cats.join(', ')}`
  if (exhausted > 0) s += `; ${exhausted} exhausted ${exhausted === 1 ? 'retry' : 'retries'}`
  return s + '.'
}

/** Top-level operational status sentence. */
function overallNarrative(
  triggers: FailedTriggerItem[],
  slides: FailedSlideItem[],
  loading: boolean,
): string {
  if (loading) return 'Loading incident data…'
  const total = triggers.length + slides.length
  if (total === 0) return 'All trigger routes nominal — no active incidents.'
  const exhausted = triggers.filter(t => t.retry_count >= t.max_retries).length
  const svcs = [...new Set(triggers.map(t => t.target_service))].map(fmtServiceName)
  let s = `${total} active ${total === 1 ? 'incident' : 'incidents'}`
  if (svcs.length) s += ` across ${svcs.join(', ')}`
  if (exhausted > 0) s += ` — ${exhausted} require manual intervention`
  return s + '.'
}

// ─── Narrative banner ─────────────────────────────────────────────────────────

function NarrativeBanner({
  triggers, slides, loading,
}: {
  triggers: FailedTriggerItem[]
  slides: FailedSlideItem[]
  loading: boolean
}) {
  const text      = overallNarrative(triggers, slides, loading)
  const exhausted = triggers.filter(t => t.retry_count >= t.max_retries).length
  const hasAny    = triggers.length + slides.length > 0

  const [bg, border, iconColor] = exhausted > 0
    ? ['rgba(225,29,72,0.05)', 'rgba(225,29,72,0.18)', 'var(--chart-rose)']
    : hasAny
    ? ['rgba(217,119,6,0.05)',  'rgba(217,119,6,0.18)',  'var(--chart-amber)']
    : ['rgba(52,211,153,0.05)', 'rgba(52,211,153,0.14)', 'var(--chart-emerald)']

  const Icon = exhausted > 0 ? AlertTriangle : hasAny ? AlertTriangle : RefreshCcw

  return (
    <div
      className="rounded-xl px-4 py-3 flex items-center gap-3 mb-5"
      style={{ background: bg, border: `1px solid ${border}` }}
    >
      <Icon className="h-3.5 w-3.5 flex-shrink-0" style={{ color: iconColor }} aria-hidden />
      <p className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
        {text}
      </p>
      {exhausted > 0 && (
        <span
          className="ml-auto text-[10px] font-semibold px-2 py-0.5 rounded flex-shrink-0"
          style={{
            color: 'var(--chart-rose)',
            background: 'rgba(225,29,72,0.08)',
            border: '1px solid rgba(225,29,72,0.20)',
          }}
        >
          {exhausted} need review
        </span>
      )}
    </div>
  )
}

// ─── Trigger incident detail (expanded body) ──────────────────────────────────

function TriggerDetail({
  t,
  slide,
  hasRecovery,
}: {
  t: FailedTriggerItem
  slide: FailedSlideItem | undefined
  hasRecovery: boolean
}) {
  const [payloadOpen, setPayloadOpen] = useState(false)

  // Compute durations between timing chain events
  function stepMs(a: string | null, b: string | null): number | null {
    if (!a || !b) return null
    return new Date(b).getTime() - new Date(a).getTime()
  }

  const queueMs   = stepMs(t.triggered_at, t.accepted_at)
  const waitMs    = stepMs(t.accepted_at,  t.started_at)
  const runMs     = stepMs(t.started_at,   t.finished_at)
  const exhausted = t.retry_count >= t.max_retries

  return (
    <div
      className="mx-4 mb-3 rounded-lg overflow-hidden"
      style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)' }}
    >
      {/* ── Forensic detail grid ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-0 divide-x divide-y"
        style={{ borderBottom: '1px solid var(--border-faint)', color: 'var(--text-faint)', fontSize: '10px' }}>
        {[
          { label: 'Route',   value: `${fmtServiceName(t.source_service)} → ${fmtServiceName(t.target_service)}` },
          { label: 'Stage',   value: fmtStageName(t.stage_name) },
          { label: 'Retries', value: `${t.retry_count} / ${t.max_retries}${exhausted ? ' — exhausted' : ''}` },
          { label: 'Artifact', value: t.global_artifact_id ? t.global_artifact_id.slice(0, 16) + '…' : '—' },
        ].map(({ label, value }) => (
          <div key={label} className="px-3 py-2">
            <p className="text-[9px] uppercase tracking-wider mb-0.5" style={{ color: 'var(--text-faint)' }}>{label}</p>
            <p
              className="text-xs font-mono"
              style={{ color: exhausted && label === 'Retries' ? 'var(--chart-rose)' : 'var(--text-secondary)' }}
              title={label === 'Artifact' ? (t.global_artifact_id ?? undefined) : undefined}
            >
              {value}
            </p>
          </div>
        ))}
      </div>

      {/* ── Timing chain ── */}
      <div
        className="px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1"
        style={{ borderBottom: '1px solid var(--border-faint)' }}
      >
        <span className="text-[9px] uppercase tracking-wider" style={{ color: 'var(--text-faint)' }}>Timeline</span>
        {[
          { label: 'Triggered', ts: t.triggered_at },
          { label: 'Accepted',  ts: t.accepted_at,  lag: queueMs != null ? `+${fmtDuration(queueMs / 1000)}` : null },
          { label: 'Started',   ts: t.started_at,   lag: waitMs  != null ? `+${fmtDuration(waitMs / 1000)}`  : null },
          { label: 'Failed',    ts: t.finished_at,  lag: runMs   != null ? `+${fmtDuration(runMs / 1000)}`   : null },
        ].map(({ label, ts, lag }, i) => (
          <span key={label} className="flex items-center gap-1.5 text-[10px]">
            {i > 0 && <span style={{ color: 'var(--border-strong)' }}>→</span>}
            <span>
              <span className="text-[9px] uppercase tracking-wider mr-1" style={{ color: 'var(--text-faint)' }}>{label}</span>
              {ts
                ? <span className="font-mono tabular" style={{ color: 'var(--text-muted)' }} title={fmtDatetime(ts)}>
                    {fmtRelative(ts)}
                    {lag && <span style={{ color: 'var(--text-faint)' }}> ({lag})</span>}
                  </span>
                : <span style={{ color: 'var(--text-faint)' }}>—</span>}
            </span>
          </span>
        ))}
      </div>

      {/* ── File info from related slide ── */}
      {slide && (
        <div
          className="px-3 py-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px]"
          style={{ borderBottom: '1px solid var(--border-faint)' }}
        >
          {slide.original_filename && (
            <span style={{ color: 'var(--text-muted)' }}>
              <span style={{ color: 'var(--text-faint)' }}>file </span>
              {slide.original_filename}
            </span>
          )}
          {slide.scanner_name && (
            <span style={{ color: 'var(--text-muted)' }}>
              <span style={{ color: 'var(--text-faint)' }}>scanner </span>
              {slide.scanner_name}
            </span>
          )}
          {slide.file_format && (
            <span className="font-mono" style={{ color: 'var(--text-muted)' }}>
              {slide.file_format}
            </span>
          )}
          {slide.current_file_path && (
            <span
              className="font-mono truncate max-w-xs"
              style={{ color: 'var(--text-faint)' }}
              title={slide.current_file_path}
            >
              {slide.current_file_path}
            </span>
          )}
        </div>
      )}

      {/* ── Error message ── */}
      {t.error_message && (
        <div
          className="px-3 pt-2 pb-1"
          style={{ borderBottom: '1px solid var(--border-faint)' }}
        >
          <p
            className="text-[9px] font-semibold uppercase tracking-wider mb-1.5"
            style={{ color: 'var(--chart-rose)' }}
          >
            Error
          </p>
          <pre
            className="text-[10px] font-mono leading-relaxed whitespace-pre-wrap break-all pb-2"
            style={{ color: 'var(--chart-rose)', opacity: 0.85 }}
          >
            {t.error_message}
          </pre>
        </div>
      )}

      {/* ── Trigger payload (collapsible) ── */}
      {t.trigger_payload_json && Object.keys(t.trigger_payload_json).length > 0 && (
        <div style={{ borderBottom: '1px solid var(--border-faint)' }}>
          <button
            type="button"
            className="w-full flex items-center gap-2 px-3 py-2 text-left"
            onClick={() => setPayloadOpen(v => !v)}
          >
            <span className="text-[9px] font-semibold uppercase tracking-wider" style={{ color: 'var(--text-faint)' }}>
              Trigger Payload
            </span>
            <ChevronDown
              className="h-2.5 w-2.5 ml-auto"
              style={{
                color: 'var(--text-faint)',
                transform: payloadOpen ? 'rotate(180deg)' : 'none',
                transition: 'transform 150ms ease',
              }}
              aria-hidden
            />
          </button>
          {payloadOpen && (
            <pre
              className="px-3 pb-2 text-[10px] font-mono leading-relaxed whitespace-pre-wrap break-all"
              style={{ color: 'var(--text-muted)' }}
            >
              {JSON.stringify(t.trigger_payload_json, null, 2)}
            </pre>
          )}
        </div>
      )}

      {/* ── Recovery indicator ── */}
      <div className="px-3 py-2 flex items-center gap-3">
        {hasRecovery ? (
          <>
            <RefreshCcw className="h-3 w-3 flex-shrink-0" style={{ color: 'var(--chart-teal)' }} aria-hidden />
            <span className="text-[10px]" style={{ color: 'var(--chart-teal)' }}>
              Recovery event recorded
            </span>
            {t.global_artifact_id && (
              <Link
                to={`/slides/${encodeURIComponent(t.global_artifact_id)}`}
                className="text-[10px] ml-auto flex items-center gap-1"
                style={{ color: 'var(--accent)' }}
                onClick={e => e.stopPropagation()}
              >
                View Slide Detail <ChevronRight className="h-2.5 w-2.5" aria-hidden />
              </Link>
            )}
          </>
        ) : (
          t.global_artifact_id && (
            <Link
              to={`/slides/${encodeURIComponent(t.global_artifact_id)}`}
              className="text-[10px] flex items-center gap-1 ml-auto"
              style={{ color: 'var(--text-muted)' }}
              onClick={e => e.stopPropagation()}
            >
              View Slide Detail <ChevronRight className="h-2.5 w-2.5" aria-hidden />
            </Link>
          )
        )}
      </div>
    </div>
  )
}

// ─── Trigger incident row ─────────────────────────────────────────────────────

function TriggerIncidentRow({
  t,
  slide,
  hasRecovery,
}: {
  t: FailedTriggerItem
  slide: FailedSlideItem | undefined
  hasRecovery: boolean
}) {
  const [open, setOpen] = useState(false)
  const exhausted = t.retry_count >= t.max_retries

  return (
    <div style={{ borderBottom: '1px solid var(--border-faint)' }}>
      <button
        type="button"
        className="w-full flex items-center gap-3 px-4 py-2.5 text-left"
        style={{
          background: open ? 'var(--accent-faint)' : 'transparent',
          transition: 'background 100ms ease',
        }}
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
      >
        {/* Expand chevron */}
        <span className="flex-shrink-0 w-3.5">
          {open
            ? <ChevronUp   className="h-3.5 w-3.5" style={{ color: 'var(--text-muted)' }} aria-hidden />
            : <ChevronDown className="h-3.5 w-3.5" style={{ color: 'var(--text-faint)' }} aria-hidden />}
        </span>

        {/* Route */}
        <span className="flex-1 min-w-0 text-xs font-medium truncate" style={{ color: 'var(--text-secondary)' }}>
          {fmtServiceName(t.source_service)}
          <span className="mx-1.5" style={{ color: 'var(--text-faint)' }}>→</span>
          {fmtServiceName(t.target_service)}
        </span>

        {/* Stage */}
        <span
          className="hidden sm:inline text-[10px] font-mono flex-shrink-0"
          style={{ color: 'var(--text-muted)' }}
        >
          {fmtStageName(t.stage_name)}
        </span>

        {/* Status */}
        <StatusBadge status={t.trigger_status} className="flex-shrink-0" />

        {/* Retry count */}
        <span
          className="text-[10px] font-mono flex-shrink-0 tabular"
          style={{
            color: exhausted
              ? 'var(--chart-rose)'
              : t.retry_count > 0
              ? 'var(--chart-amber)'
              : 'var(--text-faint)',
            fontWeight: exhausted ? 600 : 400,
          }}
          title={`${t.retry_count} of ${t.max_retries} retries`}
        >
          ×{t.retry_count}
          {exhausted && ' ⚠'}
        </span>

        {/* Recovery indicator dot */}
        {hasRecovery && (
          <span
            className="h-1.5 w-1.5 rounded-full flex-shrink-0"
            style={{ background: 'var(--chart-teal)' }}
            title="Recovery event recorded"
          />
        )}

        {/* Time */}
        <span
          className="text-[10px] flex-shrink-0"
          style={{ color: 'var(--text-faint)' }}
          title={fmtDatetime(t.triggered_at)}
        >
          {fmtRelative(t.triggered_at)}
        </span>
      </button>

      {open && (
        <TriggerDetail t={t} slide={slide} hasRecovery={hasRecovery} />
      )}
    </div>
  )
}

// ─── Service incident group ───────────────────────────────────────────────────

function ServiceIncidentGroup({
  targetService,
  triggers,
  slidesByArtifact,
  recoverySet,
}: {
  targetService: string
  triggers: FailedTriggerItem[]
  slidesByArtifact: Map<string, FailedSlideItem>
  recoverySet: Set<string>
}) {
  const [expanded, setExpanded] = useState(true)

  const exhausted   = triggers.filter(t => t.retry_count >= t.max_retries).length
  const lastFailure = triggers.reduce<string | null>((latest, t) => {
    if (!t.triggered_at) return latest
    if (!latest || t.triggered_at > latest) return t.triggered_at
    return latest
  }, null)

  const borderColor = exhausted > 0
    ? 'rgba(225,29,72,0.14)'
    : 'rgba(217,119,6,0.14)'

  return (
    <div
      className="glass rounded-xl overflow-hidden mb-4"
      style={{ border: `1px solid ${borderColor}` }}
    >
      {/* Group header */}
      <button
        type="button"
        className="w-full flex items-start gap-3 px-5 py-3.5 text-left"
        style={{ borderBottom: expanded ? '1px solid var(--border-faint)' : 'none' }}
        onClick={() => setExpanded(v => !v)}
        aria-expanded={expanded}
      >
        {/* Service name + count */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2.5 flex-wrap">
            <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
              {fmtServiceName(targetService)}
            </span>
            <span
              className="text-[10px] font-mono px-1.5 py-0.5 rounded"
              style={{
                background: exhausted > 0 ? 'rgba(225,29,72,0.08)' : 'var(--accent-faint)',
                border: `1px solid ${exhausted > 0 ? 'rgba(225,29,72,0.20)' : 'var(--border-default)'}`,
                color: exhausted > 0 ? 'var(--chart-rose)' : 'var(--text-muted)',
              }}
            >
              {triggers.length} {triggers.length === 1 ? 'failure' : 'failures'}
            </span>
            {exhausted > 0 && (
              <span
                className="text-[10px] font-semibold"
                style={{ color: 'var(--chart-rose)' }}
              >
                {exhausted} exhausted
              </span>
            )}
          </div>

          {/* Narrative */}
          <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
            {serviceNarrative(targetService, triggers)}
          </p>
        </div>

        {/* Last failure + expand */}
        <div className="flex items-center gap-3 flex-shrink-0 mt-0.5">
          {lastFailure && (
            <span className="text-[10px]" style={{ color: 'var(--text-faint)' }} title={fmtDatetime(lastFailure)}>
              {fmtRelative(lastFailure)}
            </span>
          )}
          {expanded
            ? <ChevronUp   className="h-3.5 w-3.5" style={{ color: 'var(--text-muted)' }} aria-hidden />
            : <ChevronDown className="h-3.5 w-3.5" style={{ color: 'var(--text-faint)' }} aria-hidden />}
        </div>
      </button>

      {/* Trigger rows */}
      {expanded && (
        <div>
          {triggers.map(t => (
            <TriggerIncidentRow
              key={t.internal_id}
              t={t}
              slide={t.global_artifact_id ? slidesByArtifact.get(t.global_artifact_id) : undefined}
              hasRecovery={!!t.global_artifact_id && recoverySet.has(t.global_artifact_id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Failed slide row ─────────────────────────────────────────────────────────

function FailedSlideRow({
  slide,
  hasRecovery,
  onReview,
}: {
  slide: FailedSlideItem
  hasRecovery: boolean
  onNavigate?: () => void  // kept for API compat but not used — navigation via Link
  onReview?: (slide: FailedSlideItem) => void
}) {
  const [open, setOpen] = useState(false)

  return (
    <div style={{ borderBottom: '1px solid var(--border-faint)' }}>
      <div
        className="flex items-center gap-3 px-4 py-2.5 cursor-pointer"
        style={{ background: open ? 'var(--accent-faint)' : 'transparent', transition: 'background 100ms ease' }}
        onClick={() => setOpen(v => !v)}
      >
        <span className="flex-shrink-0 w-3.5">
          {open
            ? <ChevronUp   className="h-3.5 w-3.5" style={{ color: 'var(--text-muted)' }} aria-hidden />
            : <ChevronDown className="h-3.5 w-3.5" style={{ color: 'var(--text-faint)' }} aria-hidden />}
        </span>

        <span
          className="flex-1 min-w-0 text-xs font-medium truncate"
          style={{ color: 'var(--text-primary)' }}
        >
          {slide.original_filename ?? '—'}
        </span>

        {slide.global_artifact_id && (
          <span
            className="hidden sm:inline text-[10px] font-mono truncate max-w-[120px] flex-shrink-0"
            style={{ color: 'var(--accent)' }}
          >
            {slide.global_artifact_id}
          </span>
        )}

        <StatusBadge status={slide.status} className="flex-shrink-0" />

        {hasRecovery && (
          <span
            className="h-1.5 w-1.5 rounded-full flex-shrink-0"
            style={{ background: 'var(--chart-teal)' }}
            title="Recovery event recorded"
          />
        )}

        <span
          className="text-[10px] flex-shrink-0"
          style={{ color: 'var(--text-faint)' }}
          title={fmtDatetime(slide.updated_at)}
        >
          {fmtRelative(slide.updated_at)}
        </span>
      </div>

      {open && (
        <div
          className="mx-4 mb-3 rounded-lg px-3 py-2.5 text-[10px]"
          style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)' }}
        >
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-1.5">
            {[
              { label: 'Artifact ID',   value: slide.global_artifact_id },
              { label: 'Scanner',       value: slide.scanner_name },
              { label: 'Scanner ID',    value: slide.scanner_id },
              { label: 'Format',        value: slide.file_format },
              { label: 'Path',          value: slide.current_file_path },
              { label: 'Last updated',  value: fmtDatetime(slide.updated_at) },
            ].map(({ label, value }) => value ? (
              <div key={label}>
                <span className="text-[9px] uppercase tracking-wider" style={{ color: 'var(--text-faint)' }}>
                  {label}{' '}
                </span>
                <span
                  className="font-mono"
                  style={{ color: 'var(--text-muted)' }}
                  title={value}
                >
                  {value.length > 40 ? value.slice(0, 40) + '…' : value}
                </span>
              </div>
            ) : null)}
          </div>

          <div className="flex items-center gap-3 mt-2.5 pt-2.5" style={{ borderTop: '1px solid var(--border-faint)' }}>
            {hasRecovery && (
              <span className="flex items-center gap-1" style={{ color: 'var(--chart-teal)' }}>
                <RefreshCcw className="h-3 w-3" aria-hidden />
                Recovery event recorded
              </span>
            )}
            {onReview && (
              <button
                type="button"
                className="flex items-center gap-1"
                style={{ color: 'var(--accent)', fontSize: '10px' }}
                onClick={e => { e.stopPropagation(); onReview(slide) }}
              >
                <ClipboardCheck className="h-3 w-3" aria-hidden />
                Technician Review
              </button>
            )}
            {slide.global_artifact_id && (
              <div className="ml-auto flex items-center gap-2">
                <Link
                  to={`/slides/${encodeURIComponent(slide.global_artifact_id)}`}
                  className="flex items-center gap-1 text-[10px]"
                  style={{ color: 'var(--accent)' }}
                  onClick={e => e.stopPropagation()}
                >
                  <Search className="h-2.5 w-2.5" aria-hidden />
                  Investigate
                </Link>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Section header ───────────────────────────────────────────────────────────

function SectionHeader({
  icon: Icon, title, count, colorVar,
}: {
  icon: LucideIcon
  title: string
  count: number
  colorVar: string
}) {
  return (
    <div className="flex items-center gap-2.5 mb-3">
      <Icon className="h-3.5 w-3.5" style={{ color: colorVar }} aria-hidden />
      <h2
        className="text-xs font-semibold uppercase tracking-[0.15em]"
        style={{ color: 'var(--text-secondary)' }}
      >
        {title}
      </h2>
      <span
        className="text-[10px] font-mono px-2 py-0.5 rounded"
        style={{
          background: 'var(--accent-faint)',
          border:     '1px solid var(--border-default)',
          color:      'var(--text-muted)',
        }}
      >
        {count}
      </span>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function FailureCenter() {
  const navigate = useNavigate()
  const { data, isPending, isError, refetch } = useFailures()
  const [reviewItem, setReviewItem] = useState<MonitoredFileItem | null>(null)

  // For Technician Review drawer — load all monitored files so we can match
  // a FailedSlideItem to its WatchedFolderSnapshot by filename/artifact_id
  const { data: monitoredData } = useMonitoredFiles({ limit: 500 })
  const monitoredByArtifact = new Map<string, MonitoredFileItem>()
  const monitoredByFilename = new Map<string, MonitoredFileItem>()
  for (const mf of monitoredData?.items ?? []) {
    if (mf.global_artifact_id) monitoredByArtifact.set(mf.global_artifact_id, mf)
    monitoredByFilename.set(mf.filename, mf)
  }

  function openReview(slide: FailedSlideItem) {
    const mf =
      (slide.global_artifact_id && monitoredByArtifact.get(slide.global_artifact_id)) ||
      (slide.original_filename  && monitoredByFilename.get(slide.original_filename))   ||
      null
    if (mf) setReviewItem(mf)
  }

  const triggers    = data?.failed_triggers ?? []
  const slides      = data?.failed_slides   ?? []
  const recoverySet = new Set(data?.artifact_ids_with_recovery ?? [])
  const hasFailures = triggers.length + slides.length > 0
  const exhausted   = triggers.filter(t => t.retry_count >= t.max_retries).length

  // Group triggers by target service, sorted: most failures first, then by most recent
  const triggerGroups: Map<string, FailedTriggerItem[]> = new Map()
  for (const t of triggers) {
    const svc = t.target_service
    if (!triggerGroups.has(svc)) triggerGroups.set(svc, [])
    triggerGroups.get(svc)!.push(t)
  }
  const sortedServices = [...triggerGroups.entries()]
    .sort((a, b) => {
      // Sort by exhausted count desc, then total count desc
      const aEx = a[1].filter(t => t.retry_count >= t.max_retries).length
      const bEx = b[1].filter(t => t.retry_count >= t.max_retries).length
      if (bEx !== aEx) return bEx - aEx
      return b[1].length - a[1].length
    })

  // Index slides by artifact ID so trigger rows can look up file info
  const slidesByArtifact = new Map<string, FailedSlideItem>()
  for (const s of slides) {
    if (s.global_artifact_id) slidesByArtifact.set(s.global_artifact_id, s)
  }

  return (
    <>
      <PageHeader
        tag="Incident Command"
        title="Failure Center"
        subtitle="Active pipeline incidents and trigger lineage"
        actions={
          hasFailures ? (
            <div
              className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[10px] font-semibold uppercase tracking-wider"
              style={{
                color:      'var(--chart-rose)',
                border:     '1px solid rgba(225,29,72,0.20)',
                background: 'rgba(225,29,72,0.06)',
                animation:  'pulseStatus 1.9s ease-in-out infinite',
                animationDelay: '0.37s',
                willChange: 'opacity, transform',
              }}
            >
              <AlertTriangle className="h-3 w-3" aria-hidden /> Failures Detected
            </div>
          ) : undefined
        }
      />

      {isError && (
        <div className="mb-5">
          <ErrorBanner message="Failed to load failure data." onRetry={refetch} />
        </div>
      )}

      {/* Narrative banner */}
      <NarrativeBanner triggers={triggers} slides={slides} loading={isPending} />

      {/* Incident status — unified instrument panel */}
      <TelemetryMetricRow
        className="mb-5"
        metrics={[
          {
            key:     'slides',
            label:   'Failed Slides',
            value:   String(slides.length),
            accent:  slides.length > 0 ? 'var(--chart-rose)' : undefined,
            sub:     slides.length > 0 ? 'investigation required' : 'nominal',
            loading: isPending,
          },
          {
            key:     'triggers',
            label:   'Failed Triggers',
            value:   String(triggers.length),
            accent:  triggers.length > 0 ? 'var(--chart-rose)' : undefined,
            sub:     triggers.length > 0 ? 'across pipeline routes' : 'all routes nominal',
            loading: isPending,
          },
          {
            key:     'exhausted',
            label:   'Need Review',
            value:   String(exhausted),
            accent:  exhausted > 0 ? 'var(--chart-rose)' : 'var(--chart-emerald)',
            sub:     'retries exhausted',
            loading: isPending,
          },
          {
            key:     'recovery',
            label:   'With Recovery',
            value:   String(recoverySet.size),
            accent:  recoverySet.size > 0 ? 'var(--chart-teal)' : undefined,
            sub:     'events recorded',
            loading: isPending,
          },
        ]}
      />

      {/* ── Trigger Incidents ── */}
      <section className="mb-6">
        <SectionHeader
          icon={Zap}
          title="Trigger Incidents"
          count={triggers.length}
          colorVar={triggers.length > 0 ? 'var(--chart-rose)' : 'var(--chart-emerald)'}
        />

        {isPending ? (
          <div className="glass rounded-xl overflow-hidden" style={{ border: '1px solid var(--border-default)' }}>
            <table className="ops-table">
              <tbody>{Array.from({ length: 4 }, (_, i) => <SkeletonRow key={i} cols={6} />)}</tbody>
            </table>
          </div>
        ) : sortedServices.length === 0 ? (
          <div
            className="glass rounded-xl p-5"
            style={{ border: '1px solid var(--border-default)' }}
          >
            <EmptyState
              title="All trigger routes nominal"
              description="No failed service triggers — pipeline operating normally."
              icon="✓"
            />
          </div>
        ) : (
          sortedServices.map(([svc, svgTriggers]) => (
            <ServiceIncidentGroup
              key={svc}
              targetService={svc}
              triggers={svgTriggers}
              slidesByArtifact={slidesByArtifact}
              recoverySet={recoverySet}
            />
          ))
        )}
      </section>

      {/* ── Failed Slides ── */}
      <section className="mb-6">
        <SectionHeader
          icon={FileText}
          title="Failed Slides"
          count={slides.length}
          colorVar={slides.length > 0 ? 'var(--chart-amber)' : 'var(--chart-emerald)'}
        />

        <div
          className="glass rounded-xl overflow-hidden"
          style={{ border: slides.length > 0 ? '1px solid rgba(217,119,6,0.14)' : '1px solid var(--border-default)' }}
        >
          {isPending ? (
            <table className="ops-table">
              <tbody>{Array.from({ length: 5 }, (_, i) => <SkeletonRow key={i} cols={4} />)}</tbody>
            </table>
          ) : slides.length === 0 ? (
            <div className="p-5">
              <EmptyState title="No failed slides" description="All slides processing normally." icon="✓" />
            </div>
          ) : (
            <div>
              {/* Column header */}
              <div
                className="flex items-center gap-3 px-4 py-2"
                style={{ borderBottom: '1px solid var(--border-faint)', background: 'var(--accent-faint)' }}
              >
                <span className="w-3.5" />
                <span className="flex-1 text-[10px] uppercase tracking-wider font-semibold" style={{ color: 'var(--text-muted)' }}>Filename</span>
                <span className="hidden sm:inline text-[10px] uppercase tracking-wider font-semibold w-32" style={{ color: 'var(--text-muted)' }}>Artifact</span>
                <span className="text-[10px] uppercase tracking-wider font-semibold w-24" style={{ color: 'var(--text-muted)' }}>Status</span>
                <span className="w-4" />
                <span className="text-[10px] uppercase tracking-wider font-semibold w-16 text-right" style={{ color: 'var(--text-muted)' }}>Updated</span>
              </div>
              {slides.map(s => (
                <FailedSlideRow
                  key={s.internal_id}
                  slide={s}
                  hasRecovery={!!s.global_artifact_id && recoverySet.has(s.global_artifact_id)}
                  onNavigate={() =>
                    s.global_artifact_id &&
                    navigate(`/slides/${encodeURIComponent(s.global_artifact_id)}`)
                  }
                  onReview={openReview}
                />
              ))}
            </div>
          )}
        </div>
      </section>

      {/* ── Live Pipeline Events ── */}
      <div
        className="glass rounded-xl p-5"
        style={{ border: '1px solid var(--border-default)' }}
      >
        <p className="section-label">Live Pipeline Events</p>
        <EventStream maxItems={8} />
      </div>

      {/* Technician Review Drawer */}
      {reviewItem && (
        <TechnicianReviewDrawer
          file={reviewItem}
          onClose={() => setReviewItem(null)}
        />
      )}
    </>
  )
}
