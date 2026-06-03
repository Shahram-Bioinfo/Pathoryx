import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { useRecentEvents } from '../../hooks/useEvents'
import type { EventItem } from '../../types/api'
import { fmtDatetime, fmtEventType, fmtServiceName } from '../../utils/formatters'

// ─── Severity classification ─────────────────────────────────────────────────

type Sev = 'error' | 'success' | 'warn' | 'active' | 'neutral'

function severity(eventType: string): Sev {
  const t = eventType.toLowerCase()
  if (t.includes('fail') || t.includes('error'))                                           return 'error'
  if (t.includes('pass') || t.includes('done') || t.includes('upload') || t.includes('complet')) return 'success'
  if (t.includes('retry') || t.includes('stale') || t.includes('warn'))                   return 'warn'
  if (t.includes('running') || t.includes('start') || t.includes('pending'))              return 'active'
  return 'neutral'
}

const SEV_DOT: Record<Sev, string> = {
  error:   'var(--chart-rose)',
  success: 'var(--chart-emerald)',
  warn:    'var(--chart-amber)',
  active:  'var(--accent)',
  neutral: 'var(--text-faint)',
}

const SEV_BORDER: Record<Sev, string> = {
  error:   'rgba(251,113,133,0.28)',
  success: 'rgba(52,211,153,0.20)',
  warn:    'rgba(252,211,77,0.22)',
  active:  'rgba(34,211,238,0.18)',
  neutral: 'var(--border-faint)',
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function fmtHHmmss(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('en-GB', { hour12: false })
  } catch {
    return '—'
  }
}

// Ordered list of payload keys worth surfacing as a summary line.
// Checked in order; first two non-empty values are used.
const SUMMARY_KEYS = [
  'decision_status', 'qc_result', 'outcome', 'final_outcome',
  'conversion_status', 'upload_status', 'action_taken',
  'target_system', 'error_reason', 'error', 'result',
]

function payloadSummary(p: Record<string, unknown> | null | undefined): string {
  if (!p || typeof p !== 'object') return ''
  const bits: string[] = []
  for (const key of SUMMARY_KEYS) {
    const val = p[key]
    if (val != null && String(val).trim()) {
      bits.push(String(val).replace(/_/g, ' '))
      if (bits.length >= 2) break
    }
  }
  return bits.join(' · ')
}

// ─── Single event row ─────────────────────────────────────────────────────────

function EventRow({
  ev,
  idx,
  open,
  onToggle,
}: {
  ev: EventItem
  idx: number
  open: boolean
  onToggle: () => void
}) {
  const sev      = severity(ev.event_type)
  const summary  = payloadSummary(ev.event_payload)
  const hasData  = !!ev.event_payload && Object.keys(ev.event_payload).length > 0

  return (
    <div
      style={{
        borderBottom:   '1px solid var(--border-faint)',
        borderLeft:     `2px solid ${SEV_BORDER[sev]}`,
        paddingLeft:    '8px',
        // Entry animation fires once per DOM node mount — React reconciliation
        // by event_id means only genuinely new events animate. Existing rows
        // keep their nodes across polls, so no re-animation occurs.
        animation:      'entry 240ms cubic-bezier(0.16,1,0.3,1) both',
        animationDelay: `${Math.min(idx * 28, 200)}ms`,
      }}
    >
      {/* Clickable row — expands payload if available */}
      <button
        type="button"
        className="w-full flex items-start gap-2.5 py-1.5 text-left"
        onClick={hasData ? onToggle : undefined}
        style={{ cursor: hasData ? 'pointer' : 'default', background: 'transparent' }}
        aria-expanded={hasData ? open : undefined}
      >
        {/* Severity dot */}
        <span
          className="h-1.5 w-1.5 rounded-full mt-1.5 flex-shrink-0"
          style={{ background: SEV_DOT[sev] }}
          aria-hidden
        />

        {/* Event type + summary */}
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium truncate" style={{ color: 'var(--text-secondary)' }}>
            {fmtEventType(ev.event_type)}
          </p>
          <p className="text-[10px] mt-0.5 flex flex-wrap gap-x-1.5" style={{ color: 'var(--text-muted)' }}>
            <span>{fmtServiceName(ev.service_name)}</span>
            {summary && (
              <span style={{ color: 'var(--text-faint)' }}>· {summary}</span>
            )}
            {ev.global_artifact_id && (
              <span className="font-mono" style={{ color: 'var(--text-faint)' }}>
                · {ev.global_artifact_id.slice(0, 10)}…
              </span>
            )}
          </p>
        </div>

        {/* HH:mm:ss timestamp (monospace) + expand chevron */}
        <div className="flex-shrink-0 flex flex-col items-end gap-0.5 pl-2">
          <span
            className="text-[10px] tabular"
            style={{ color: 'var(--text-faint)', fontFamily: '"JetBrains Mono", monospace' }}
            title={fmtDatetime(ev.occurred_at)}
          >
            {fmtHHmmss(ev.occurred_at)}
          </span>
          {hasData && (
            <ChevronDown
              className="h-2.5 w-2.5"
              style={{
                color:      'var(--text-faint)',
                transform:  open ? 'rotate(180deg)' : 'none',
                transition: 'transform 150ms ease',
              }}
              aria-hidden
            />
          )}
        </div>
      </button>

      {/* Expanded JSON payload panel */}
      {open && hasData && ev.event_payload && (
        <div
          className="mb-2 rounded overflow-x-auto scrollbar-none"
          style={{
            background: 'var(--surface-inset)',
            border:     '1px solid var(--border-faint)',
          }}
        >
          <pre
            className="p-2 text-[10px] leading-relaxed"
            style={{
              color:       'var(--text-muted)',
              fontFamily:  '"JetBrains Mono", monospace',
              whiteSpace:  'pre-wrap',
              wordBreak:   'break-all',
            }}
          >
            {JSON.stringify(ev.event_payload, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}

// ─── Event list renderer (shared by both modes) ───────────────────────────────

function EventList({
  events,
  maxItems,
  emptyMessage = 'No events recorded',
}: {
  events: EventItem[]
  maxItems: number
  emptyMessage?: string
}) {
  // Track which event_ids have their payload expanded.
  // State lives at this level so it survives across parent re-renders
  // (e.g. new events arriving from polling don't collapse open rows).
  const [open, setOpen] = useState<Set<number>>(new Set())

  const toggle = (id: number) =>
    setOpen(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  if (events.length === 0) {
    return (
      <p className="text-xs tracking-wide" style={{ color: 'var(--text-faint)' }}>
        {emptyMessage}
      </p>
    )
  }

  return (
    <div className="max-h-80 overflow-y-auto scrollbar-none">
      {events.slice(0, maxItems).map((ev, idx) => (
        <EventRow
          key={ev.event_id}
          ev={ev}
          idx={idx}
          open={open.has(ev.event_id)}
          onToggle={() => toggle(ev.event_id)}
        />
      ))}
    </div>
  )
}

// ─── Self-fetching wrapper (live mode) ───────────────────────────────────────

function LiveEventList({
  pollInterval,
  limit,
  maxItems,
  emptyMessage,
}: {
  pollInterval: number
  limit: number
  maxItems: number
  emptyMessage?: string
}) {
  const { data } = useRecentEvents(limit, pollInterval)
  return (
    <EventList
      events={data?.items ?? []}
      maxItems={maxItems}
      emptyMessage={emptyMessage}
    />
  )
}

// ─── Public component ─────────────────────────────────────────────────────────

interface Props {
  /**
   * Prop-driven mode: pass an array to render these events without polling.
   * Used when the parent already holds the data (e.g. Slide Detail).
   * Omit to enter live self-fetching mode.
   */
  events?: EventItem[]
  /** Live mode: polling interval in ms. Default: 5 000 */
  pollInterval?: number
  /** Live mode: how many events to request per poll. Default: 30 */
  limit?: number
  /** Maximum rows to render. Default: 15 */
  maxItems?: number
  /** Empty-state text override. */
  emptyMessage?: string
}

/**
 * EventStream — live operational event feed with expand-to-inspect.
 *
 * ── Live mode (default) ──
 * Polls /events/recent every `pollInterval` ms. New events receive the `entry`
 * animation on their DOM nodes because React reconciles by event_id — only
 * rows whose key wasn't in the previous render get a new node and the animation.
 * Rows already rendered never re-animate across polls.
 *
 * ── Prop-driven mode ──
 * Pass `events={...}` to render a fixed or parent-managed list without polling.
 * The expand/collapse state for JSON payloads is still self-managed here.
 *
 * ── Click behaviour ──
 * Clicking any row that carries a non-empty event_payload toggles an inline
 * JSON panel beneath it. Rows without payload are non-interactive. The chevron
 * indicator only appears when a payload is available.
 *
 * ── Timestamp ──
 * Shows HH:mm:ss in JetBrains Mono. Hover for the full `MMM d yyyy HH:mm`
 * datetime via the title attribute.
 */
export function EventStream({
  events,
  pollInterval = 5_000,
  limit = 30,
  maxItems = 15,
  emptyMessage,
}: Props) {
  // Prop-driven: render the supplied list without polling.
  if (events !== undefined) {
    return (
      <EventList events={events} maxItems={maxItems} emptyMessage={emptyMessage} />
    )
  }

  // Live: delegate to a separate component so the hook call is always
  // unconditional (hooks cannot be called conditionally).
  return (
    <LiveEventList
      pollInterval={pollInterval}
      limit={limit}
      maxItems={maxItems}
      emptyMessage={emptyMessage}
    />
  )
}
