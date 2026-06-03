import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { EventItem, TriggerItem, RecoveryEventItem } from '../../types/api'
import { fmtDuration, fmtEventType, fmtServiceName } from '../../utils/formatters'

// ─── Types ────────────────────────────────────────────────────────────────────

type TLOutcome = 'success' | 'error' | 'warn' | 'active' | 'neutral'
type TLSource  = 'trigger' | 'event' | 'recovery'

interface TLEntry {
  id:          string
  ts:          string | null
  source:      TLSource
  stage:       string
  label:       string
  detail:      string | null
  outcome:     TLOutcome
  durationSec: number | null
  retries:     number
  errorText:   string | null
  payload:     Record<string, unknown> | null
}

// ─── Colour maps (CSS vars — theme-safe) ─────────────────────────────────────

const STAGE_COLOR: Record<string, string> = {
  INTAKE: 'var(--stage-intake-color)',
  QC:     'var(--stage-qc-color)',
  DICOM:  'var(--stage-dicom-color)',
  UPLOAD: 'var(--stage-upload-color)',
  RECOV:  'var(--chart-amber)',
  SYS:    'var(--text-muted)',
}

const OUTCOME_COLOR: Record<TLOutcome, string> = {
  success: 'var(--chart-emerald)',
  error:   'var(--chart-rose)',
  warn:    'var(--chart-amber)',
  active:  'var(--accent)',
  neutral: 'var(--text-faint)',
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtHHmmss(iso: string | null): string {
  if (!iso) return '—:—:—'
  try { return new Date(iso).toLocaleTimeString('en-GB', { hour12: false }) }
  catch { return '—:—:—' }
}

function stageFromTrigger(name: string): string {
  const n = name.toLowerCase()
  if (n === 'qc')     return 'QC'
  if (n === 'dicom')  return 'DICOM'
  if (n === 'upload') return 'UPLOAD'
  if (n === 'intake') return 'INTAKE'
  return n.toUpperCase().slice(0, 6)
}

function stageFromEvent(type: string): string {
  const t = type.toLowerCase()
  if (t.includes('intake') || t.includes('detect') || t.includes('register') || t.includes('babel'))
    return 'INTAKE'
  if (t.includes('qc'))
    return 'QC'
  if (t.includes('dicom') || t.includes('conver'))
    return 'DICOM'
  if (t.includes('upload') || t.includes('transmit'))
    return 'UPLOAD'
  return 'SYS'
}

function sevFromEvent(type: string): TLOutcome {
  const t = type.toLowerCase()
  if (t.includes('fail') || t.includes('error'))                                              return 'error'
  if (t.includes('pass') || t.includes('done') || t.includes('uploaded') || t.includes('complet') || t.includes('register')) return 'success'
  if (t.includes('retry') || t.includes('stale') || t.includes('warn'))                      return 'warn'
  if (t.includes('running') || t.includes('start') || t.includes('pending') || t.includes('detect')) return 'active'
  return 'neutral'
}

// ─── Entry builders ───────────────────────────────────────────────────────────

function fromTrigger(t: TriggerItem): TLEntry {
  const started  = t.started_at  ? new Date(t.started_at).getTime()  : null
  const finished = t.finished_at ? new Date(t.finished_at).getTime() : null
  const dur      = started && finished ? (finished - started) / 1000 : null
  const status   = t.trigger_status

  const outcome: TLOutcome =
    status === 'completed' ? 'success' :
    status === 'failed'    ? 'error'   :
    (status === 'running' || status === 'pending') ? 'active' : 'neutral'

  return {
    id:          `trig-${t.internal_id}`,
    ts:          t.triggered_at,
    source:      'trigger',
    stage:       stageFromTrigger(t.stage_name),
    label:       `${fmtServiceName(t.source_service)} → ${fmtServiceName(t.target_service)}`,
    detail:      status ? status.replace(/_/g, ' ') : null,
    outcome,
    durationSec: dur,
    retries:     t.retry_count,
    errorText:   t.error_message ?? null,
    payload:     null,
  }
}

function fromEvent(ev: EventItem): TLEntry {
  return {
    id:          `ev-${ev.event_id}`,
    ts:          ev.occurred_at,
    source:      'event',
    stage:       stageFromEvent(ev.event_type),
    label:       fmtEventType(ev.event_type),
    detail:      fmtServiceName(ev.service_name),
    outcome:     sevFromEvent(ev.event_type),
    durationSec: null,
    retries:     0,
    errorText:   null,
    payload:     ev.event_payload && Object.keys(ev.event_payload).length > 0 ? ev.event_payload : null,
  }
}

function fromRecovery(rv: RecoveryEventItem): TLEntry {
  const outcome: TLOutcome =
    rv.recovery_outcome === 'requeued' ? 'success' :
    rv.review_status    === 'dismissed' ? 'neutral' :
    rv.review_status    === 'pending'   ? 'warn'    : 'active'

  return {
    id:          `rec-${rv.internal_id}`,
    ts:          rv.detected_at,
    source:      'recovery',
    stage:       'RECOV',
    label:       rv.change_type.replace(/_/g, ' '),
    detail:      rv.recovery_reason ?? rv.inferred_action ?? rv.watch_folder_label ?? null,
    outcome,
    durationSec: null,
    retries:     0,
    errorText:   null,
    payload:     null,
  }
}

function buildTimeline(
  triggers: TriggerItem[],
  events:   EventItem[],
  recovery: RecoveryEventItem[],
): TLEntry[] {
  return [
    ...triggers.map(fromTrigger),
    ...events.map(fromEvent),
    ...recovery.map(fromRecovery),
  ].sort((a, b) => {
    if (!a.ts) return 1
    if (!b.ts) return -1
    return new Date(a.ts).getTime() - new Date(b.ts).getTime()
  })
}

// ─── Source type pill ─────────────────────────────────────────────────────────

const SOURCE_LABEL: Record<TLSource, string> = {
  trigger:  'TRG',
  event:    'EVT',
  recovery: 'RCV',
}

// ─── Component ────────────────────────────────────────────────────────────────

interface Props {
  triggers:  TriggerItem[]
  events:    EventItem[]
  recovery:  RecoveryEventItem[]
  maxHeight?: string
}

export function ArtifactTimeline({ triggers, events, recovery, maxHeight = '20rem' }: Props) {
  const [open, setOpen] = useState<Set<string>>(new Set())

  const entries = buildTimeline(triggers, events, recovery)

  if (!entries.length) {
    return (
      <p className="text-[10px] tracking-widest uppercase" style={{ color: 'var(--text-faint)' }}>
        No timeline entries recorded
      </p>
    )
  }

  const toggle = (id: string) => setOpen(prev => {
    const n = new Set(prev)
    n.has(id) ? n.delete(id) : n.add(id)
    return n
  })

  return (
    <div className="overflow-y-auto scrollbar-none" style={{ maxHeight }}>
      {entries.map((entry, idx) => {
        const isOpen    = open.has(entry.id)
        const hasDetail = !!(entry.errorText || entry.payload)
        const dotColor  = OUTCOME_COLOR[entry.outcome]
        const stageColor = STAGE_COLOR[entry.stage] ?? 'var(--text-muted)'
        const isLast    = idx === entries.length - 1

        return (
          <div key={entry.id}>
            {/* Row */}
            <div
              className="flex items-start gap-2 py-1.5"
              style={{
                borderBottom: isLast ? 'none' : '1px solid var(--border-faint)',
                cursor: hasDetail ? 'pointer' : 'default',
              }}
              onClick={hasDetail ? () => toggle(entry.id) : undefined}
              role={hasDetail ? 'button' : undefined}
              tabIndex={hasDetail ? 0 : undefined}
              onKeyDown={hasDetail ? e => e.key === 'Enter' && toggle(entry.id) : undefined}
            >
              {/* HH:mm:ss */}
              <span
                className="text-[9px] font-mono tabular flex-shrink-0 mt-0.5"
                style={{ color: 'var(--text-faint)', width: '4.25rem' }}
              >
                {fmtHHmmss(entry.ts)}
              </span>

              {/* Stage label */}
              <span
                className="text-[9px] font-mono font-semibold tracking-wider flex-shrink-0 mt-0.5"
                style={{ color: stageColor, width: '3.5rem' }}
              >
                {entry.stage}
              </span>

              {/* Source type */}
              <span
                className="text-[8px] font-mono tracking-widest flex-shrink-0 mt-0.5 opacity-50"
                style={{ color: 'var(--text-faint)', width: '1.75rem' }}
              >
                {SOURCE_LABEL[entry.source]}
              </span>

              {/* Outcome dot */}
              <span
                className="h-1.5 w-1.5 rounded-full flex-shrink-0 mt-[5px]"
                style={{ background: dotColor }}
                aria-hidden
              />

              {/* Label + detail */}
              <div className="flex-1 min-w-0">
                <p
                  className="text-[10px] font-medium leading-snug"
                  style={{ color: 'var(--text-secondary)' }}
                >
                  {entry.label}
                </p>
                {entry.detail && (
                  <p className="text-[9px] mt-0.5 leading-snug" style={{ color: 'var(--text-faint)' }}>
                    {entry.detail}
                  </p>
                )}
              </div>

              {/* Retry badge */}
              {entry.retries > 0 && (
                <span
                  className="text-[9px] font-mono flex-shrink-0 mt-0.5"
                  style={{ color: 'var(--chart-amber)' }}
                >
                  ×{entry.retries}
                </span>
              )}

              {/* Duration */}
              <span
                className="text-[9px] font-mono tabular flex-shrink-0 mt-0.5 text-right"
                style={{ color: 'var(--text-faint)', width: '3rem' }}
              >
                {entry.durationSec != null ? fmtDuration(entry.durationSec) : ''}
              </span>

              {/* Expand chevron */}
              {hasDetail && (
                <ChevronDown
                  className="h-2.5 w-2.5 flex-shrink-0 mt-1"
                  style={{
                    color:      'var(--text-faint)',
                    transform:  isOpen ? 'rotate(180deg)' : 'none',
                    transition: 'transform 150ms ease',
                  }}
                  aria-hidden
                />
              )}
            </div>

            {/* Expandable detail */}
            {isOpen && (entry.errorText || entry.payload) && (
              <div className="pb-2 pl-[13.75rem]">
                <pre
                  className="rounded p-2 text-[9px] font-mono break-all overflow-x-auto scrollbar-none whitespace-pre-wrap"
                  style={{
                    background: entry.errorText ? 'rgba(225,29,72,0.06)' : 'var(--surface-inset)',
                    border:     entry.errorText
                      ? '1px solid rgba(225,29,72,0.18)'
                      : '1px solid var(--border-faint)',
                    color: entry.errorText ? 'var(--chart-rose)' : 'var(--text-muted)',
                  }}
                >
                  {entry.errorText ?? JSON.stringify(entry.payload, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
